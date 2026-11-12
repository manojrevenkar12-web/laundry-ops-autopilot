"""Facade used by the API, the CLI and the eval harness."""
from __future__ import annotations

from datetime import datetime

from laundryops.adapters import Directory, MockMollie, MockWeWashBox, Notifier
from laundryops.config import Settings, settings as default_settings
from laundryops.domain.models import TicketIn, TicketOutcome
from laundryops.llm import build_llm
from laundryops.store.db import Store
from laundryops.support.graph import SupportGraph


class Platform:
    """Everything wired together. One instance per process (or per eval run)."""

    def __init__(self, *, settings: Settings | None = None, store: Store | None = None, llm=None,
                 directory: Directory | None = None, iot: MockWeWashBox | None = None,
                 payments: MockMollie | None = None, notifier: Notifier | None = None, shadow: bool = False):
        from laundryops.fleet.service import FleetService
        from laundryops.settlement.service import SettlementService

        self.s = settings or default_settings
        self.store = store or Store(self.s.db_path)
        self.llm = llm or build_llm(self.s)
        self.directory = directory or Directory()
        self.iot = iot or MockWeWashBox()
        self.payments = payments or MockMollie()
        self.notifier = notifier or Notifier()
        self.settlement = SettlementService(directory=self.directory, payments=self.payments, store=self.store,
                                            settings=self.s, iot=self.iot)
        self.fleet = FleetService(directory=self.directory, iot=self.iot, payments=self.payments, store=self.store,
                                  notifier=self.notifier, settings=self.s)
        self.shadow = shadow
        self.support = SupportGraph(llm=self.llm, directory=self.directory, iot=self.iot, payments=self.payments,
                                    store=self.store, notifier=self.notifier, settings=self.s,
                                    settlement=self.settlement, shadow=shadow)
        from laundryops.voice.dialog import VoiceDialog
        self.voice = VoiceDialog(self, human_number=self.s.voice_human_number)

    def handle(self, ticket: TicketIn, now: datetime | None = None) -> TicketOutcome:
        out = self.support.run(ticket, now)
        self.store.save_ticket(ticket, out)
        return out

    def handle_message(self, ticket: TicketIn, now: datetime | None = None) -> TicketOutcome:
        """Chat/e-mail entry point: a status question about a review reference, or an unhappy follow-up, is answered
        before the ticket pipeline runs; everything else goes through ``handle``."""
        from laundryops.domain.models import Decision, Language, Outcome, TriageResult, Intent
        from laundryops.llm.offline import detect_language
        from laundryops.support.review import find_reference, is_dissatisfied, open_review, status_line

        who = self.directory.get_user(ticket.user_id) or self.directory.get_partner(ticket.partner_id)
        lang = detect_language(ticket.text, who.language if who else None)
        ref = find_reference(ticket.text)
        if ref:
            r, line = status_line(self, ref, lang)
            reply = line.format(ref=ref)
            out = self._info_outcome(ticket, lang, reply, ["ACT_INFO_REVIEW_STATUS"])
        elif is_dissatisfied(ticket.text) and ticket.user_id:
            prev = [t["ticket_id"] for t in self.store.list_tickets(50) if t["user_id"] == ticket.user_id][:3]
            review, line = open_review(self, user_id=ticket.user_id, partner_id=ticket.partner_id, channel=ticket.channel.value,
                                       reason=ticket.text, tickets=prev or [ticket.ticket_id], language=lang, now=now)
            out = self._info_outcome(ticket, lang, line.format(ref=review["reference"]), ["ESC_CUSTOMER_REQUEST"], escalated=True)
        else:
            return self.handle(ticket, now)
        self.store.save_ticket(ticket, out)
        return out

    def _info_outcome(self, ticket: TicketIn, lang, reply: str, codes: list[str], escalated: bool = False) -> TicketOutcome:
        from laundryops.domain.models import ActionResult, ActionType, Decision, Intent, Outcome, TriageResult
        outcome = Outcome.ESCALATED if escalated else Outcome.AUTO_RESOLVED
        act = ActionType.ESCALATE_HUMAN if escalated else ActionType.SEND_INFO
        return TicketOutcome(ticket_id=ticket.ticket_id, outcome=outcome,
                             triage=TriageResult(intent=Intent.ESCALATION_REQUIRED if escalated else Intent.REFUND_STATUS,
                                                 language=lang, confidence=1.0, summary=ticket.text[:140]),
                             decision=Decision(outcome=outcome, actions=[], reason_codes=codes, explanation=codes[0]),
                             actions=[ActionResult(type=act, success=True, detail={"reason_code": codes[0]})],
                             reply=reply, reply_language=lang, handled_in_ms=1)
