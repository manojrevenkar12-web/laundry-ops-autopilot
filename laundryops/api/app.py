"""FastAPI surface: ticket intake, ops console data, fleet and settlement endpoints.

Run: ``uvicorn laundryops.api.app:app --reload`` then open http://127.0.0.1:8000/console
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

from laundryops import __version__
from laundryops.config import Settings
from laundryops.domain.models import Channel, TicketIn
from laundryops.store.db import Store
from laundryops.support.service import Platform
from laundryops.synth import build_world

app = FastAPI(title="laundry-ops-autopilot", version=__version__,
              description="Autonomous support & remediation, fleet health and partner settlement for shared laundry rooms.")

_settings = Settings()
platform = Platform(settings=_settings, store=Store(_settings.db_path))


class TicketRequest(BaseModel):
    text: str
    user_id: str | None = None
    partner_id: str | None = None
    channel: Channel = Channel.CHAT
    ticket_id: str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "llm": platform.llm.name, "shadow": platform.shadow}


# ---- support ----------------------------------------------------------------------------
@app.post("/tickets")
def create_ticket(req: TicketRequest) -> dict[str, Any]:
    tid = req.ticket_id or f"t_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    out = platform.handle_message(TicketIn(ticket_id=tid, user_id=req.user_id, partner_id=req.partner_id, channel=req.channel, text=req.text))
    return out.model_dump(mode="json")


@app.get("/tickets")
def list_tickets(limit: int = Query(200, le=1000)) -> list[dict[str, Any]]:
    return platform.store.list_tickets(limit)


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str) -> dict[str, Any]:
    t = platform.store.get_ticket(ticket_id)
    if not t:
        raise HTTPException(404, "ticket not found")
    t["audit"] = platform.store.audit_trail(ticket_id)
    return t


class NotHappy(BaseModel):
    reason: str = "Resident not satisfied with the automated answer"


@app.post("/tickets/{ticket_id}/not-happy")
def ticket_not_happy(ticket_id: str, req: NotHappy) -> dict[str, Any]:
    """The resident says the answer did not help: log a human-review ticket, tell them the reference."""
    from laundryops.domain.models import Language
    from laundryops.support.review import open_review

    t = platform.store.get_ticket(ticket_id)
    if not t:
        raise HTTPException(404, "ticket not found")
    lang = Language.parse(t.get("language"))
    review, line = open_review(platform, user_id=t.get("user_id"), partner_id=t.get("partner_id"), channel=t.get("channel") or "chat",
                               reason=req.reason, tickets=[ticket_id], language=lang)
    return {"review": review, "message": line.format(ref=review["reference"])}


@app.get("/reviews")
def list_reviews(status: str | None = None) -> list[dict[str, Any]]:
    return platform.store.list_reviews(status)


@app.get("/reviews/{reference}")
def get_review(reference: str) -> dict[str, Any]:
    """Resident-facing status lookup by reference (also answered on the hotline and in chat)."""
    r = platform.store.get_review(reference)
    if not r:
        raise HTTPException(404, "no ticket with that reference")
    return {"reference": r["reference"], "status": r["status"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


class ReviewUpdate(BaseModel):
    status: str
    note: str | None = None


@app.post("/reviews/{reference}/status")
def update_review(reference: str, req: ReviewUpdate) -> dict[str, Any]:
    if req.status not in ("open", "in_review", "resolved"):
        raise HTTPException(400, "status must be open | in_review | resolved")
    r = platform.store.update_review(reference, req.status, req.note)
    if not r:
        raise HTTPException(404, "no ticket with that reference")
    who = platform.directory.get_user(r["user_id"]) or platform.directory.get_partner(r["partner_id"])
    to = (getattr(who, "phone", None) or getattr(who, "email", None) or getattr(who, "contact_email", None)) if who else None
    if to:
        platform.notifier.send(to, "sms" if to.startswith("+") else "email", f"Ticket {reference}",
                               f"Status Ihres Tickets {reference}: {req.status}. / Your ticket {reference} is now: {req.status}.", reference=reference)
    platform.store.audit("ops", "review_status", None, reference=reference, status=req.status)
    return r


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    return platform.store.metrics()


@app.get("/graph", response_class=PlainTextResponse)
def graph() -> str:
    return platform.support.mermaid()


# ---- fleet ------------------------------------------------------------------------------
@app.get("/fleet/health")
def fleet_health() -> list[dict[str, Any]]:
    return [h.model_dump(mode="json") for h in platform.fleet.assess()]


@app.post("/fleet/dispatch")
def fleet_dispatch() -> dict[str, Any]:
    return platform.fleet.dispatch()


@app.post("/fleet/proactive")
def fleet_proactive() -> dict[str, Any]:
    outs = platform.fleet.proactive_sweep(platform.handle)
    return {"handled": len(outs), "auto_resolved": sum(o.outcome.value == "auto_resolved" for o in outs),
            "tickets": [o.ticket_id for o in outs]}


@app.get("/fleet/faults")
def faults(status: str | None = None) -> list[dict[str, Any]]:
    return [f.model_dump(mode="json") for f in platform.store.list_faults(status)]


@app.post("/fleet/faults/{fault_id}/ack")
def ack_fault(fault_id: str) -> dict[str, Any]:
    f = platform.fleet.acknowledge(fault_id)
    if not f:
        raise HTTPException(404, "fault not found")
    return f.model_dump(mode="json")


@app.post("/fleet/faults/{fault_id}/fix")
def fix_fault(fault_id: str) -> dict[str, Any]:
    f = platform.fleet.fix(fault_id)
    if not f:
        raise HTTPException(404, "fault not found")
    return f.model_dump(mode="json")


@app.post("/fleet/outage/{machine_id}/notify")
def notify_outage(machine_id: str) -> dict[str, Any]:
    return {"notified": platform.fleet.notify_residents_of_outage(machine_id)}


# ---- settlement --------------------------------------------------------------------------
@app.post("/settlement/run")
def settlement_run(days: int = 30) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    res = platform.settlement.run_month((now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0), now)
    res["pain001_bytes"] = len(res.pop("pain001"))
    return res


@app.get("/settlement/partners/{partner_id}")
def partner_status(partner_id: str) -> dict[str, Any]:
    if not platform.directory.get_partner(partner_id):
        raise HTTPException(404, "partner not found")
    return platform.settlement.partner_status(partner_id)


@app.get("/settlement/payouts")
def payouts() -> list[dict[str, Any]]:
    return [p.model_dump(mode="json") for p in platform.store.list_payouts()]


@app.get("/settlement/sepa", response_class=PlainTextResponse)
def sepa_file() -> str:
    pending = [p for p in platform.store.list_payouts() if p.status == "created"]
    if not pending:
        raise HTTPException(404, "no payouts in 'created' state")
    return platform.settlement.build_sepa_file(pending)


class BankRecord(BaseModel):
    end_to_end_id: str
    status: str
    reason: str | None = None


@app.post("/settlement/reconcile")
def reconcile(records: list[BankRecord]) -> dict[str, Any]:
    return platform.settlement.reconcile([r.model_dump() for r in records])


# ---- voice: Twilio speech webhooks (day-one integration) ---------------------------------
def _twiml(turn, call_id: str) -> Response:
    from laundryops.voice.twiml import twiml_for_turn

    base = _settings.voice_public_base_url.rstrip("/")
    xml = twiml_for_turn(turn, call_id, turn_url=f"{base}/voice/turn", human_number=_settings.voice_human_number,
                         whisper_url=f"{base}/voice/whisper?call={call_id}", sms_from=_settings.voice_sms_from)
    return Response(content=xml, media_type="application/xml")


@app.post("/voice/incoming")
async def voice_incoming(request: Request) -> Response:
    """Twilio Voice webhook for an inbound call. Greets the caller and opens the first <Gather>."""
    form = await request.form()
    call_id = str(form.get("CallSid") or f"call_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
    turn = platform.voice.start(call_id=call_id, from_number=str(form.get("From") or "") or None)
    return _twiml(turn, call_id)


@app.post("/voice/turn")
async def voice_turn(request: Request, call: str = Query(...)) -> Response:
    """<Gather> callback: SpeechResult (or Digits) for one caller turn; empty when the caller said nothing."""
    form = await request.form()
    turn = platform.voice.step(call, utterance=str(form.get("SpeechResult") or ""), dtmf=str(form.get("Digits") or "") or None)
    return _twiml(turn, call)


@app.post("/voice/whisper")
@app.get("/voice/whisper")
def voice_whisper(call: str = Query(...)) -> Response:
    """Read to the human agent before the caller is bridged: who is calling, what the agent already did."""
    from laundryops.voice.twiml import twiml_whisper

    cs = platform.voice.calls.get(call)
    text = platform.voice._whisper(cs) if cs else "Hotline transfer, no context available."
    return Response(content=twiml_whisper(text), media_type="application/xml")


@app.websocket("/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    """Twilio Media Streams: real-time μ-law in/out with VAD, barge-in and the STT/TTS providers from settings."""
    from laundryops.voice.media_stream import MediaStreamSession
    from laundryops.voice.providers import DeepgramSTT, ElevenLabsTTS, ScriptedSTT, ToneTTS

    stt = DeepgramSTT(_settings.deepgram_api_key) if _settings.stt_provider == "deepgram" and _settings.deepgram_api_key else ScriptedSTT()
    tts = (ElevenLabsTTS(_settings.elevenlabs_api_key, _settings.elevenlabs_voice_id)
           if _settings.tts_provider == "elevenlabs" and _settings.elevenlabs_api_key else ToneTTS())
    session = MediaStreamSession(platform.voice, stt, tts)
    await ws.accept()
    try:
        while not session.done:
            msg = await ws.receive_json()
            for out in session.on_message(msg):
                await ws.send_json(out)
    except WebSocketDisconnect:
        pass


# ---- voice: browser demo (no telephony, no keys) --------------------------------------------
class DemoStart(BaseModel):
    from_number: str | None = None
    call_id: str | None = None


class DemoTurn(BaseModel):
    call_id: str
    text: str = ""
    dtmf: str | None = None


def _turn_json(turn, call_id: str) -> dict[str, Any]:
    return {"call_id": call_id, "say": turn.say, "listen": turn.listen, "done": turn.done, "handoff": turn.handoff,
            "whisper": turn.whisper, "sms": turn.sms, "language": turn.language.value, "locale": turn.locale,
            "state": turn.state, "ticket_id": turn.ticket_id}


@app.post("/voice/demo/start")
def voice_demo_start(req: DemoStart) -> dict[str, Any]:
    call_id = req.call_id or f"demo_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    turn = platform.voice.start(call_id=call_id, from_number=req.from_number)
    return _turn_json(turn, call_id)


@app.post("/voice/demo/turn")
def voice_demo_turn(req: DemoTurn) -> dict[str, Any]:
    turn = platform.voice.step(req.call_id, utterance=req.text, dtmf=req.dtmf)
    return _turn_json(turn, req.call_id)


@app.get("/voice/calls")
def voice_calls(limit: int = 50) -> dict[str, Any]:
    return {"metrics": platform.store.call_metrics(), "calls": platform.store.list_calls(limit)}


# ---- demo + console ----------------------------------------------------------------------
@app.post("/demo/seed")
def demo_seed(n: int = Query(120, le=2000), seed: int = 42) -> dict[str, Any]:
    """Populate the world and stream n labelled tickets through the agent so the console has data."""
    now = datetime.now(timezone.utc)
    scenarios = build_world(platform, seed=seed, n_tickets=n, now=now)
    for sc in scenarios:
        sc["plant"]()
        platform.handle(sc["ticket"], now)
    platform.fleet.dispatch(now)
    return {"seeded_tickets": len(scenarios), "metrics": platform.store.metrics()}


@app.post("/demo/stage")
def demo_stage(scenario: str = Query("door_locked"), language: str = Query("de")) -> dict[str, Any]:
    """Create a fresh, un-handled demo resident + booking for the hotline / ticket demo. Returns who to call as."""
    from laundryops.demo_stage import SCENARIOS, stage

    if scenario not in SCENARIOS:
        raise HTTPException(400, f"unknown scenario; one of {sorted(SCENARIOS)}")
    return stage(platform, scenario, language)


@app.get("/demo/scenarios")
def demo_scenarios() -> dict[str, str]:
    from laundryops.demo_stage import SCENARIOS
    return SCENARIOS


@app.get("/demo/users")
def demo_users(limit: int = 20) -> list[dict[str, Any]]:
    users = [u for u in platform.directory.users.values() if not u.user_id.startswith("bg_")] or list(platform.directory.users.values())
    return [u.model_dump(mode="json") for u in users[-limit:]]


@app.get("/console", response_class=HTMLResponse)
def console() -> str:
    return (Path(__file__).parent / "static" / "console.html").read_text(encoding="utf-8")
