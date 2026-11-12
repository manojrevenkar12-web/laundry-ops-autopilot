"""The hotline dialog: a small, explicit state machine on top of the same support pipeline.

    GREET ──▶ PROBLEM ──┬─▶ ASK_MACHINE ──▶ (handle) ──┐
                        └─▶ (handle) ────────────────────┤
                                                         ▼
                    ┌──────────── VERIFY_RELAY ◀── relay re-triggered?
                    ▼                 │ "no" → follow-up ticket (confirmed failure) → refund + fault
             ANYTHING_ELSE ◀──────────┘
                    │ yes → PROBLEM      no → DONE      policy says human → HANDOFF (warm transfer + whisper)

Transport-agnostic: ``start`` and ``step`` take text and return a ``Turn`` describing what to
say and whether to listen. Twilio TwiML, the media-stream pipeline and the browser demo are all
thin wrappers around it. Every call is persisted with its transcript, tickets and outcome.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from laundryops.domain.models import ActionType, Channel, Intent, Language, Outcome, TicketIn, TicketOutcome, utcnow
from laundryops.domain.models import NATIVE_LANGUAGES
from laundryops.llm.offline import detect_language, extract_machine_ref, language_request, language_scores
from laundryops.voice.numbers import parse_machine_number, spoken_numbers_to_digits
from laundryops.voice.prompts import HINTS, SPEECH_LOCALE, say
from laundryops.voice.speech import shape_for_voice

MACHINE_INTENTS = {Intent.MACHINE_NOT_STARTED, Intent.CYCLE_ABORTED, Intent.RESERVATION_CONFLICT, Intent.MACHINE_REPORT}
DTMF_MENU: dict[str, dict[Language, str]] = {
    "1": {Language.DE: "Die Maschine startet nicht, die Tür bleibt verschlossen.", Language.EN: "The machine won't start, the door stays locked.",
          Language.TR: "Makine başlamıyor, kapı kilitli.", Language.FR: "La machine ne démarre pas, la porte reste verrouillée.",
          Language.ES: "La máquina no arranca, la puerta sigue bloqueada."},
    "2": {Language.DE: "Der Waschgang wurde mittendrin abgebrochen.", Language.EN: "The cycle stopped halfway.",
          Language.TR: "Yıkama yarıda durdu.", Language.FR: "Le cycle s'est arrêté en plein milieu.", Language.ES: "El ciclo se detuvo a mitad."},
    "3": {Language.DE: "Mir wurde doppelt abgebucht.", Language.EN: "I was charged twice.", Language.TR: "İki kez para çekildi.",
          Language.FR: "J'ai été prélevé deux fois.", Language.ES: "Me cobraron dos veces."},
    "4": {Language.DE: "Wo bleibt meine Rückerstattung?", Language.EN: "Where is my refund?", Language.TR: "İadem nerede?",
          Language.FR: "Où est mon remboursement ?", Language.ES: "¿Dónde está mi reembolso?"},
}
MAX_PROBLEMS_PER_CALL = 3
MAX_SILENCES = 2


@dataclass
class Turn:
    say: str
    listen: bool = True
    language: Language = Language.DE
    locale: str = "de-DE"
    hints: str = ""
    done: bool = False
    handoff: bool = False
    whisper: str = ""              # what the human agent hears before the caller is connected
    sms: str | None = None         # text to send to the caller's number, if any
    state: str = ""
    ticket_id: str | None = None


@dataclass
class CallState:
    call_id: str
    from_number: str | None
    started_at: datetime
    language: Language = Language.UNKNOWN
    language_locked: bool = False
    user_id: str | None = None
    partner_id: str | None = None
    state: str = "GREET"
    pending_text: str = ""
    silences: int = 0
    problems: int = 0
    turns: int = 0
    tickets: list[str] = field(default_factory=list)
    last_outcome: TicketOutcome | None = None
    verify_machine_id: str | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    outcome: str = "open"          # contained | handoff | abandoned
    handoff_reason: str | None = None
    ended_at: datetime | None = None


class VoiceDialog:
    def __init__(self, platform, human_number: str = "+4989000000", clock=utcnow):
        self.p = platform
        self.human_number = human_number
        self.clock = clock
        self.calls: dict[str, CallState] = {}

    # ---- public --------------------------------------------------------------------
    def start(self, call_id: str | None = None, from_number: str | None = None, now: datetime | None = None) -> Turn:
        now = now or self.clock()
        cs = CallState(call_id=call_id or f"call_{uuid.uuid4().hex[:10]}", from_number=from_number, started_at=now)
        user = self.p.directory.user_by_phone(from_number)
        partner = self.p.directory.partner_by_phone(from_number) if not user else None
        if user:
            cs.user_id, cs.language = user.user_id, user.language
            text = say("greet_known", cs.language, name=user.display_name.split()[0])
        elif partner:
            cs.partner_id, cs.language = partner.partner_id, partner.language
            text = say("greet_known", cs.language, name=partner.name)
        else:
            cs.language = Language.DE
            text = say("greet_unknown", Language.DE)
        cs.state = "PROBLEM"
        self.calls[cs.call_id] = cs
        self.p.store.audit("voice", "call_started", None, call_id=cs.call_id, from_number=from_number, user_id=cs.user_id,
                           partner_id=cs.partner_id)
        self._persist(cs)
        return self._turn(cs, text)

    def step(self, call_id: str, utterance: str | None = None, dtmf: str | None = None, now: datetime | None = None) -> Turn:
        cs = self.calls.get(call_id)
        if cs is None:
            return Turn(say="", listen=False, done=True)
        if cs.state in ("HANDOFF", "DONE"):       # a finished call stays finished; late audio changes nothing
            return Turn(say="", listen=False, done=True, handoff=cs.state == "HANDOFF", language=cs.language, state=cs.state)
        now = now or self.clock()
        cs.turns += 1
        if dtmf and dtmf in DTMF_MENU and cs.state in ("PROBLEM", "ANYTHING_ELSE"):
            utterance = DTMF_MENU[dtmf].get(cs.language if cs.language != Language.UNKNOWN else Language.DE)
            cs.state = "PROBLEM"
        text = (utterance or "").strip()
        if text:
            cs.transcript.append({"who": "caller", "text": text})
        if not text:
            return self._silence(cs)
        cs.silences = 0
        if not dtmf:
            self._detect_language(cs, text)

        if cs.state == "PROBLEM":
            return self._problem(cs, text, now)
        if cs.state == "ASK_MACHINE":
            n = parse_machine_number(text)
            if n is None:
                return self._turn(cs, say("ask_repeat", cs.language))
            return self._handle(cs, f"{cs.pending_text} (Maschine {n})", now)
        if cs.state == "VERIFY_RELAY":
            return self._verify(cs, text, now)
        if cs.state == "ANYTHING_ELSE":
            if self._matches(cs, text, "no"):
                return self._finish(cs, "contained")
            if self._matches(cs, text, "yes"):
                cs.state = "PROBLEM"
                return self._turn(cs, say("greet_known", cs.language, name="").replace("Hallo , ", "").replace("Hi , ", "")
                                  .replace("Merhaba , ", "").replace("Bonjour , ", "").replace("Hola , ", "").strip())
            cs.state = "PROBLEM"
            return self._problem(cs, text, now)
        return self._finish(cs, "contained")

    # ---- internals -----------------------------------------------------------------
    @staticmethod
    def _language_request(text: str) -> Language | None:
        return language_request(text)

    def _detect_language(self, cs: CallState, text: str) -> None:
        """First real sentence sets the language; later sentences can still switch it on strong evidence."""
        asked = self._language_request(text)
        if asked:
            cs.language, cs.language_locked = asked, True
            return
        hint = cs.language if cs.language != Language.UNKNOWN else None
        if cs.language_locked:
            scores = language_scores(text)
            best = max(scores, key=scores.get)
            if best != cs.language and len(text.split()) >= 5 and scores[best] - scores.get(cs.language, 0) >= 3:
                cs.language = best
            return
        detected = detect_language(text, hint)
        if len(text.split()) >= 3:
            cs.language, cs.language_locked = detected, True
        elif cs.language == Language.UNKNOWN:
            cs.language = detected

    def _silence(self, cs: CallState) -> Turn:
        cs.silences += 1
        if cs.silences > MAX_SILENCES:
            sms = None
            if cs.from_number:
                sms = say("callback", cs.language)
                self.p.notifier.send(cs.from_number, "sms", "Rückruf / callback", sms, call_id=cs.call_id)
            cs.outcome, cs.handoff_reason = "abandoned", "silence"
            return self._finish(cs, "abandoned", say("callback", cs.language), sms=sms)
        return self._turn(cs, say("ask_repeat", cs.language))

    def _problem(self, cs: CallState, text: str, now: datetime) -> Turn:
        if cs.problems >= MAX_PROBLEMS_PER_CALL:
            return self._handoff(cs, "too_many_requests", say("handoff", cs.language))
        text = spoken_numbers_to_digits(text)
        tr = self.p.llm.triage(text, hint_language=cs.language, is_partner=bool(cs.partner_id))
        # "Do you speak English?" / small talk: switch language if asked and simply ask again what they need.
        if tr.intent in (Intent.UNKNOWN, Intent.GENERAL_QUESTION) and (self._language_request(text) or len(text.split()) <= 4) \
                and tr.intent != Intent.GENERAL_QUESTION:
            cs.silences += 1
            if cs.silences > MAX_SILENCES:
                return self._handoff(cs, "unclear", say("handoff", cs.language))
            return self._turn(cs, say("what_else", cs.language))
        needs_machine = tr.intent in MACHINE_INTENTS and extract_machine_ref(text) is None
        if needs_machine and cs.user_id and not self._single_recent_session(cs.user_id, now):
            cs.state, cs.pending_text = "ASK_MACHINE", text
            return self._turn(cs, say("ask_machine", cs.language))
        return self._handle(cs, text, now)

    def _single_recent_session(self, user_id: str, now: datetime) -> bool:
        from datetime import timedelta
        sessions = self.p.payments.sessions_for_user(user_id, now - timedelta(hours=24))
        return len(sessions) == 1

    def _handle(self, cs: CallState, text: str, now: datetime, context: dict[str, Any] | None = None) -> Turn:
        cs.problems += 1
        tid = f"{cs.call_id}_{cs.problems}"
        ctx = dict(context or {})
        if cs.language != Language.UNKNOWN:
            ctx["language_hint"] = cs.language.value
        ticket = TicketIn(ticket_id=tid, user_id=cs.user_id, partner_id=cs.partner_id, channel=Channel.VOICE, text=text,
                          received_at=now, context=ctx)
        out = self.p.handle(ticket, now)
        cs.tickets.append(tid)
        cs.last_outcome = out
        cs.language = out.reply_language if out.reply_language != Language.UNKNOWN else cs.language
        spoken = shape_for_voice(out.reply, cs.language)
        if out.outcome == Outcome.ESCALATED:
            reason = out.decision.reason_codes[0] if out.decision.reason_codes else "ESC_UNKNOWN"
            if reason == "ESC_NO_IDENTITY":
                sms = None
                if cs.from_number:
                    sms = "Bitte melden Sie sich in der App, dann können wir sofort helfen. / Please sign in to the app so we can help right away."
                    self.p.notifier.send(cs.from_number, "sms", "App-Link", sms, call_id=cs.call_id)
                return self._handoff(cs, reason, say("handoff_identity", cs.language), sms=sms, ticket_id=tid)
            prefix = (spoken + " ") if spoken else ""
            return self._handoff(cs, reason, prefix + say("handoff", cs.language), ticket_id=tid)
        relay_ok = any(a.type == ActionType.RETRIGGER_RELAY and a.success for a in out.actions)
        if relay_ok:
            cs.state = "VERIFY_RELAY"
            cs.verify_machine_id = out.evidence_summary.get("machine")
            return self._turn(cs, spoken + " " + self._verify_question(cs.language), ticket_id=tid)
        cs.state = "ANYTHING_ELSE"
        return self._turn(cs, spoken + " " + say("anything_else", cs.language), ticket_id=tid)

    def _verify(self, cs: CallState, text: str, now: datetime) -> Turn:
        if self._matches(cs, text, "no") or re.search(r"(nicht|immer noch|still|hâlâ|toujours|todavía|sigue)", text.lower()):
            follow = {Language.DE: "Die Tür ist nach der Freischaltung immer noch verschlossen.",
                      Language.EN: "The door is still locked after the re-release.",
                      Language.TR: "Yeniden açmadan sonra kapı hâlâ kilitli.",
                      Language.FR: "La porte est toujours verrouillée après la réactivation.",
                      Language.ES: "La puerta sigue bloqueada después de la reactivación."}.get(cs.language, "The door is still locked after the re-release.")
            machine = self.p.iot.get_machine(cs.verify_machine_id or "")
            if machine:
                follow += f" ({machine.machine_id})"
            cs.problems -= 1          # the follow-up belongs to the same problem
            return self._handle(cs, follow, now, context={"confirmed_failure": True})
        if self._matches(cs, text, "yes") or re.search(r"(offen|open|açık|ouverte|abierta|läuft|running|çalışıyor|tourne|funciona)", text.lower()):
            cs.state = "ANYTHING_ELSE"
            return self._turn(cs, say("anything_else", cs.language))
        return self._turn(cs, say("ask_repeat", cs.language))

    @staticmethod
    def _verify_question(language: Language) -> str:
        return {Language.DE: "Ist die Tür jetzt offen?", Language.EN: "Is the door open now?", Language.TR: "Kapı şimdi açık mı?",
                Language.FR: "La porte est-elle ouverte maintenant ?", Language.ES: "¿Está la puerta abierta ahora?"}.get(language, "Is the door open now?")

    _EXTRA = {"yes": r"(sì|sim|tak|ja|da|ναι|да|так|نعم|بله|ہاں|हाँ|হ্যাঁ|是|はい|네|vâng|yes)",
              "no": r"(no|não|nie|nee|nu|όχι|нет|ні|لا|نه|نہیں|नहीं|না|不|いいえ|아니요|không)"}

    def _matches(self, cs: CallState, text: str, key: str) -> bool:
        """Yes/no in any language we know — callers answer short questions in whatever comes first."""
        t = text.lower().strip()
        if cs.language not in NATIVE_LANGUAGES and re.search(r"(?<!\w)" + self._EXTRA[key] + r"(?!\w)", t):
            return True
        langs = [cs.language] + [l for l in (Language.DE, Language.EN, Language.TR, Language.FR, Language.ES) if l != cs.language]
        for lang in langs:
            if lang == Language.UNKNOWN:
                continue
            if re.search(r"(?<!\w)(" + say(key, lang) + r")(?!\w)", t):
                return True
        return False

    def _handoff(self, cs: CallState, reason: str, text: str, sms: str | None = None, ticket_id: str | None = None) -> Turn:
        cs.state, cs.outcome, cs.handoff_reason, cs.ended_at = "HANDOFF", "handoff", reason, self.clock()
        whisper = self._whisper(cs)
        self.p.store.audit("voice", "call_handoff", ticket_id, call_id=cs.call_id, reason=reason, whisper=whisper)
        self._persist(cs)
        text = self._localize(cs, text)
        cs.transcript.append({"who": "agent", "text": text})
        return Turn(say=text, listen=False, language=cs.language, locale=SPEECH_LOCALE.get(cs.language, "de-DE"), done=True,
                    handoff=True, whisper=whisper, sms=sms, state=cs.state, ticket_id=ticket_id)

    def _whisper(self, cs: CallState) -> str:
        """Context the human agent hears before the caller is bridged: who, what, what was already done."""
        who = "unknown caller"
        if cs.user_id:
            u = self.p.directory.get_user(cs.user_id)
            who = f"{u.display_name}, site {u.site_id}" if u else cs.user_id
        elif cs.partner_id:
            who = f"partner {cs.partner_id}"
        parts = [f"Hotline transfer. {who}. Language {cs.language.value}."]
        out = cs.last_outcome
        if out:
            parts.append(f"Intent {out.triage.intent.value}. Reason {', '.join(out.decision.reason_codes[:3])}.")
            done = [a.type.value for a in out.actions if a.success and a.type != ActionType.ESCALATE_HUMAN]
            if done:
                parts.append("Already done: " + ", ".join(done) + ".")
            if out.evidence_summary.get("machine"):
                parts.append(f"Machine {out.evidence_summary['machine']}.")
        return " ".join(parts)

    def _finish(self, cs: CallState, outcome: str, text: str | None = None, sms: str | None = None) -> Turn:
        cs.state, cs.outcome, cs.ended_at = "DONE", outcome, self.clock()
        text = self._localize(cs, text or say("goodbye", cs.language))
        self.p.store.audit("voice", "call_ended", None, call_id=cs.call_id, outcome=outcome, turns=cs.turns, tickets=cs.tickets)
        self._persist(cs)
        cs.transcript.append({"who": "agent", "text": text})
        return Turn(say=text, listen=False, language=cs.language, locale=SPEECH_LOCALE.get(cs.language, "de-DE"), done=True,
                    sms=sms, state=cs.state)

    def _localize(self, cs: CallState, text: str) -> str:
        if cs.language in NATIVE_LANGUAGES or cs.language == Language.UNKNOWN:
            return text
        return self.p.llm.translate(text, cs.language)

    def _turn(self, cs: CallState, text: str, ticket_id: str | None = None) -> Turn:
        text = self._localize(cs, text)
        cs.transcript.append({"who": "agent", "text": text})
        self._persist(cs)
        lang = cs.language if cs.language != Language.UNKNOWN else Language.DE
        return Turn(say=text, listen=True, language=lang, locale=SPEECH_LOCALE.get(lang, "de-DE"), hints=HINTS.get(lang, ""),
                    state=cs.state, ticket_id=ticket_id)

    def _persist(self, cs: CallState) -> None:
        self.p.store.save_call({
            "call_id": cs.call_id, "from_number": cs.from_number, "user_id": cs.user_id, "partner_id": cs.partner_id,
            "language": cs.language.value, "started_at": cs.started_at.isoformat(),
            "ended_at": cs.ended_at.isoformat() if cs.ended_at else None, "turns": cs.turns,
            "outcome": cs.outcome if cs.ended_at else "open", "handoff_reason": cs.handoff_reason,
            "tickets": cs.tickets, "transcript": cs.transcript,
        })
