"""Hotline: dialog state machine, verify-after-actuation, hand-off, TwiML, media-stream pipeline, demo API."""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from laundryops.domain.models import ActionType, CycleState, Language, User
from laundryops.voice.audio import EnergyVAD, silence, tone, ulaw_decode, ulaw_encode
from laundryops.voice.media_stream import MediaStreamSession
from laundryops.voice.numbers import parse_machine_number, spoken_numbers_to_digits
from laundryops.voice.providers import ScriptedSTT, ToneTTS
from laundryops.voice.speech import shape_for_voice
from laundryops.voice.twiml import twiml_for_turn


def _with_phones(platform):
    for uid, phone in (("u_de", "+4915100000001"), ("u_en", "+4915100000002"), ("u_tr", "+4915100000003")):
        u = platform.directory.get_user(uid)
        platform.directory.add_user(u.model_copy(update={"phone": phone}))
    return platform


# ---- numbers & speech shaping ---------------------------------------------------------------
def test_spoken_numbers():
    assert spoken_numbers_to_digits("Waschmaschine drei geht nicht auf") == "Waschmaschine 3 geht nicht auf"
    assert spoken_numbers_to_digits("machine number three is locked") == "machine 3 is locked"
    assert spoken_numbers_to_digits("kurutma makinesi iki çalışmıyor").startswith("kurutma makinesi 2")
    assert parse_machine_number("die drei") == 3 and parse_machine_number("number five") == 5
    assert parse_machine_number("la dos") == 2 and parse_machine_number("Waschmaschine 12") == 12
    assert parse_machine_number("keine Ahnung") is None


def test_shape_for_voice_strips_signature_and_speaks_money():
    reply = "Hallo Lena,\n\nwir haben 3,50 € für Waschmaschine 2 erstattet; antworten Sie bitte mit einem Screenshot.\n\nViele Grüße\nIhr Waschraum-Service"
    spoken = shape_for_voice(reply, Language.DE)
    assert spoken.startswith("Wir haben 3 Euro 50") and "Screenshot" not in spoken and "Viele Grüße" not in spoken
    assert "SMS" in spoken


# ---- dialog -------------------------------------------------------------------------------------
def test_call_resolves_relay_and_asks_machine_when_ambiguous(platform, now):
    _with_phones(platform)
    platform.payments.plant_paid_session("u_de", "WM-0001", 350, minutes_ago=300, state=CycleState.FINISHED, started=True)
    ses, _ = platform.payments.plant_paid_session("u_de", "WM-0003", 350, minutes_ago=4)
    platform.iot.plant_not_started("WM-0003", ses.session_id)
    v = platform.voice
    t = v.start("CA1", "+4915100000001", now)
    assert "Lena" in t.say and t.locale == "de-DE"
    t = v.step("CA1", "Hallo, ich habe bezahlt aber die Tür geht nicht auf.", now=now)
    assert t.state == "ASK_MACHINE"
    t = v.step("CA1", "die drei", now=now)
    assert t.state == "VERIFY_RELAY" and "Ist die Tür jetzt offen" in t.say
    assert platform.iot.relay_calls == [("WM-0003", ses.session_id)]
    t = v.step("CA1", "ja, offen", now=now)
    assert t.state == "ANYTHING_ELSE"
    t = v.step("CA1", "nein danke", now=now)
    assert t.done and not t.handoff
    assert platform.payments.refund_calls == []
    assert platform.store.call_metrics()["containment_rate"] == 1.0


def test_verify_turn_refunds_only_after_our_own_failed_actuation(platform, now):
    _with_phones(platform)
    ses, p = platform.payments.plant_paid_session("u_en", "WM-0002", 400, minutes_ago=3)
    platform.iot.plant_not_started("WM-0002", ses.session_id)
    v = platform.voice
    v.start("CA2", "+4915100000002", now)
    t = v.step("CA2", "machine two won't start, the door is locked", now=now)
    assert t.state == "VERIFY_RELAY"
    t = v.step("CA2", "no, still locked", now=now)
    cs = v.calls["CA2"]
    assert cs.last_outcome.decision.reason_codes[0] == "EV_RETRIGGER_CONFIRMED_FAILED"
    assert [a.type for a in cs.last_outcome.actions] == [ActionType.REFUND, ActionType.OPEN_FAULT_TICKET]
    assert platform.payments.get_payment(p.payment_id).refunded_cents == 400
    assert "4 euros" in t.say and "anything else" in t.say.lower()


def test_confirmed_failure_without_prior_retrigger_moves_no_money(platform, now):
    """A caller cannot talk the agent into the 'confirmed failure' refund path without a real actuation on record."""
    from laundryops.domain.models import TicketIn
    ses, p = platform.payments.plant_paid_session("u_en", "WM-0002", 400, minutes_ago=30, state=CycleState.RUNNING, started=True)
    platform.iot.plant_running_other_user("WM-0002", ses.session_id)
    out = platform.handle(TicketIn(ticket_id="x", user_id="u_en", text="The door is still locked after the re-release. (WM-0002)",
                                   context={"confirmed_failure": True}), now)
    assert platform.payments.refund_calls == []
    assert "EV_RETRIGGER_CONFIRMED_FAILED" not in out.decision.reason_codes


def test_unknown_caller_gets_sms_and_warm_transfer_with_whisper(platform, now):
    v = platform.voice
    v.start("CA3", "+4400000000", now)
    t = v.step("CA3", "Hi, I was charged twice for machine two today", now=now)
    assert t.handoff and t.done and t.sms and "app" in t.sms.lower()
    assert "unknown caller" in t.whisper and "double_charge" in t.whisper and "ESC_NO_IDENTITY" in t.whisper
    assert platform.notifier.for_recipient("+4400000000")
    assert platform.store.call_metrics()["handoff_reasons"] == {"ESC_NO_IDENTITY": 1}


def test_language_switches_from_first_utterance_and_dtmf_fallback(platform, now):
    _with_phones(platform)
    ses, _ = platform.payments.plant_paid_session("u_de", "WM-0001", 350, minutes_ago=40, state=CycleState.RUNNING, started=True)
    platform.iot.plant_aborted("WM-0001", ses.session_id)
    v = platform.voice
    t = v.start("CA4", "+4915100000001", now)
    assert t.language == Language.DE
    t = v.step("CA4", dtmf="2", now=now)           # noisy basement: pressed 2 = cycle stopped
    cs = v.calls["CA4"]
    assert cs.last_outcome.triage.intent.value == "cycle_aborted"
    assert "3 Euro 50" in t.say and t.state == "ANYTHING_ELSE"
    t = v.step("CA4", "yes actually one more thing about the dryer which is really dirty and smells", now=now)
    assert t.language == Language.EN                 # switched after a long English utterance


def test_silence_twice_then_callback_sms(platform, now):
    _with_phones(platform)
    v = platform.voice
    v.start("CA5", "+4915100000003", now)
    t = v.step("CA5", "", now=now)
    assert "anlayamadım" in t.say.lower() and t.listen
    t = v.step("CA5", "", now=now)
    t = v.step("CA5", "", now=now)
    assert t.done and t.sms and platform.notifier.for_recipient("+4915100000003")
    assert platform.store.list_calls()[0]["outcome"] == "abandoned"


def test_damage_claim_on_the_phone_hands_over_with_context(platform, now):
    _with_phones(platform)
    v = platform.voice
    v.start("CA6", "+4915100000001", now)
    t = v.step("CA6", "Der Trockner hat meinen Pullover zerrissen", now=now)
    assert t.handoff and "ESC_DAMAGE_CLAIM" in t.whisper and "Lena Hoffmann" in t.whisper


# ---- TwiML ----------------------------------------------------------------------------------------
def test_twiml_gather_and_dial(platform, now):
    _with_phones(platform)
    t = platform.voice.start("CA7", "+4915100000001", now)
    xml = twiml_for_turn(t, "CA7", turn_url="https://x/voice/turn", human_number="+49891", whisper_url="https://x/voice/whisper?call=CA7")
    root = ET.fromstring(xml)
    g = root.find("Gather")
    assert g is not None and g.get("language") == "de-DE" and "speech" in g.get("input") and "Waschmaschine" in g.get("hints")
    assert g.get("action") == "https://x/voice/turn?call=CA7"
    t = platform.voice.step("CA7", "Ihr seid Betrüger, ich gehe zum Anwalt", now=now)
    xml = twiml_for_turn(t, "CA7", turn_url="https://x/voice/turn", human_number="+49891", whisper_url="https://x/voice/whisper?call=CA7")
    root = ET.fromstring(xml)
    assert root.find("Dial/Number").text == "+49891" and root.find("Dial/Number").get("url").endswith("call=CA7")


# ---- audio + media stream ---------------------------------------------------------------------------
def test_ulaw_roundtrip_and_vad_endpointing():
    samples = tone(440.0, 100)
    back = ulaw_decode(ulaw_encode(samples))
    assert max(abs(a - b) for a, b in zip(samples, back)) < 600       # 8-bit companding error
    vad = EnergyVAD()
    events = [vad.push(silence(200)), vad.push(tone(300.0, 400)), vad.push(silence(800))]
    assert events[1] == "start" and events[2] == "end" and len(vad.take_utterance()) > 8000 * 0.4


def test_media_stream_full_call_with_barge_in(platform, now):
    _with_phones(platform)
    ses, _ = platform.payments.plant_paid_session("u_de", "WM-0001", 350, minutes_ago=4)
    platform.iot.plant_not_started("WM-0001", ses.session_id)
    stt = ScriptedSTT(["Waschmaschine eins geht nicht auf", "ja ist offen", "nein danke das war's"])
    s = MediaStreamSession(platform.voice, stt, ToneTTS())
    out = s.on_message({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA8", "customParameters": {"from": "+4915100000001"}}})
    assert out[0]["event"] == "media" and out[-1]["event"] == "mark" and s.playing

    def speak(ms: int) -> list:
        msgs = []
        for chunk in (tone(300.0, ms), silence(900)):
            raw = ulaw_encode(chunk)
            for i in range(0, len(raw), 160):
                msgs.extend(s.on_message({"event": "media", "media": {"payload": base64.b64encode(raw[i:i + 160]).decode()}}))
        return msgs

    msgs = speak(600)                     # caller talks over the greeting -> barge-in clears playback, then utterance is handled
    assert msgs[0]["event"] == "clear" and s.stats["barge_ins"] == 1
    assert any(m["event"] == "media" for m in msgs) and s.stats["utterances"] == 1
    assert platform.iot.relay_calls == [("WM-0001", ses.session_id)]
    s.on_message({"event": "mark", "mark": {"name": "end_of_speech"}})
    speak(400)                            # "ja ist offen"
    speak(400)                            # "nein danke"
    assert s.done and not s.handoff and s.stats["utterances"] == 3
    assert stt.received[0][1] == Language.DE


# ---- API ---------------------------------------------------------------------------------------------
def test_voice_api_twilio_and_demo(settings):
    from laundryops.api import app as api
    from laundryops.store.db import Store
    from laundryops.support.service import Platform
    api.platform = Platform(settings=settings, store=Store(":memory:"))
    c = TestClient(api.app)
    c.post("/demo/seed?n=10&seed=5")
    user = next(u for u in api.platform.directory.users.values() if u.phone)
    r = c.post("/voice/incoming", data={"CallSid": "CA9", "From": user.phone})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml") and "<Gather" in r.text
    r = c.post("/voice/turn?call=CA9", data={"SpeechResult": "Wie viel kostet ein Waschgang?"})
    assert "<Gather" in r.text and "€" not in r.text and "euro" in r.text.lower()
    r = c.get("/voice/whisper?call=CA9")
    assert "<Say" in r.text
    d = c.post("/voice/demo/start", json={"from_number": user.phone}).json()
    assert d["listen"] and d["call_id"]
    d = c.post("/voice/demo/turn", json={"call_id": d["call_id"], "text": "", "dtmf": "4"}).json()
    assert d["state"] in ("ANYTHING_ELSE", "HANDOFF")
    assert c.get("/voice/calls").json()["metrics"]["calls"] >= 0


def test_finished_call_ignores_late_audio_and_keeps_outcome(platform, now):
    _with_phones(platform)
    v = platform.voice
    v.start("CA10", "+4915100000001", now)
    t = v.step("CA10", "Der Trockner hat meinen Pullover zerrissen", now=now)
    assert t.handoff
    t2 = v.step("CA10", "nein danke das war's", now=now)
    assert t2.done and t2.say == ""
    assert platform.store.call_metrics() == {"calls": 1, "contained": 0, "containment_rate": 0.0, "avg_turns": 1.0,
                                             "handoff_reasons": {"ESC_DAMAGE_CLAIM": 1}}


def test_staged_scenarios_run_end_to_end(platform, now):
    from laundryops.demo_stage import SCENARIOS, stage
    v = platform.voice
    info = stage(platform, "door_locked", "de", now)
    n = int(info["machine"]["label"].split()[-1])
    v.start("S1", info["from_number"], now)
    assert v.step("S1", "Ich hab bezahlt, aber die Tür geht nicht auf.", now=now).state == "ASK_MACHINE"
    assert v.step("S1", f"die {n}", now=now).state == "VERIFY_RELAY"
    t = v.step("S1", "Nein, immer noch zu.", now=now)
    assert "3 Euro 50" in t.say and v.calls["S1"].last_outcome.decision.reason_codes[0] == "EV_RETRIGGER_CONFIRMED_FAILED"
    for sc in SCENARIOS:
        stage(platform, sc, "en", now)            # every scenario stages without error


def test_german_first_then_switch_on_request_any_language(platform, now):
    from laundryops.demo_stage import stage
    v = platform.voice
    info = stage(platform, "door_locked", "de", now)
    n = int(info["machine"]["label"].split()[-1])
    t = v.start("L1", info["from_number"], now)
    assert t.locale == "de-DE" and t.say.startswith("Hallo")
    t = v.step("L1", "Do you speak English?", now=now)
    assert t.language == Language.EN and "What can I do for you" in t.say
    t = v.step("L1", "parla italiano?", now=now)
    assert t.language == Language.IT and t.locale == "it-IT"
    t = v.step("L1", "можно по-русски?", now=now)
    assert t.language == Language.RU
    t = v.step("L1", "я заплатил но дверь не открывается", now=now)
    assert t.state in ("ASK_MACHINE", "VERIFY_RELAY")
    if t.state == "ASK_MACHINE":
        t = v.step("L1", str(n), now=now)
    assert t.state == "VERIFY_RELAY"
    t = v.step("L1", "нет, всё ещё закрыто", now=now)
    assert v.calls["L1"].last_outcome.decision.reason_codes[0] == "EV_RETRIGGER_CONFIRMED_FAILED"


def test_script_detection_and_translation_hook(platform, now):
    from laundryops.llm.offline import detect_language, language_request
    assert detect_language("Машина не работает") == Language.RU
    assert detect_language("Машина не працює, гроші списані") == Language.UK
    assert detect_language("الغسالة لا تعمل") == Language.AR
    assert detect_language("मशीन चालू नहीं हो रही") == Language.HI
    assert detect_language("洗衣机打不开") == Language.ZH
    assert language_request("sprechen Sie englisch?") == Language.EN
    assert language_request("हिंदी में बात कर सकते हैं?") == Language.HI

    from laundryops.llm.offline import OfflineLLM

    class StubTranslate(OfflineLLM):
        name = "stub"
        def translate(self, text, language):
            return f"[{language.value}] " + text
    platform.llm = StubTranslate()
    v = platform.voice
    v.start("L2", "+4400000000", now)
    t = v.step("L2", "po polsku proszę", now=now)
    assert t.say.startswith("[pl] ")
