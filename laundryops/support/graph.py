"""LangGraph state machine for one ticket.

    intake -> triage -> diagnose -> decide -> (act | shadow) -> respond -> END

Each node is a small pure-ish function over ``AgentState``; the graph makes the control flow
inspectable and lets a reviewer see exactly which node produced which field.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from laundryops.domain.models import (
    ActionResult,
    Decision,
    Evidence,
    Language,
    Outcome,
    TicketIn,
    TicketOutcome,
    TriageResult,
    utcnow,
)
from laundryops.llm.offline import looks_like_injection
from laundryops.support.actions import ActionExecutor
from laundryops.support.diagnose import collect_evidence
from laundryops.support.facts import build_facts
from laundryops.support.policy import decide


class AgentState(TypedDict, total=False):
    ticket: TicketIn
    now: datetime
    started_ns: int
    injection: bool
    triage: TriageResult
    evidence: Evidence
    decision: Decision
    results: list[ActionResult]
    facts: dict[str, Any]
    reply: str
    outcome: TicketOutcome


class SupportGraph:
    def __init__(self, *, llm, directory, iot, payments, store, notifier, settings, settlement=None, shadow: bool = False):
        self.llm, self.directory, self.iot, self.payments = llm, directory, iot, payments
        self.store, self.notifier, self.s, self.settlement = store, notifier, settings, settlement
        self.shadow = shadow
        self.executor = ActionExecutor(directory=directory, iot=iot, payments=payments, store=store,
                                       notifier=notifier, settings=settings, settlement=settlement)
        self._graph = self._build()

    # ---- nodes ----------------------------------------------------------------------------
    def _intake(self, st: AgentState) -> AgentState:
        t = st["ticket"]
        self.store.audit("agent", "ticket_received", t.ticket_id, channel=t.channel.value, user_id=t.user_id, partner_id=t.partner_id)
        return {"started_ns": time.perf_counter_ns(), "now": st.get("now") or utcnow(),
                "injection": looks_like_injection(t.text)}

    def _triage(self, st: AgentState) -> AgentState:
        t = st["ticket"]
        who = self.directory.get_user(t.user_id) or self.directory.get_partner(t.partner_id)
        hint = who.language if who else None
        if t.context.get("language_hint"):
            hint = Language(t.context["language_hint"])
        tr = self.llm.triage(t.text, hint_language=hint, is_partner=t.channel.value == "partner_portal" or bool(t.partner_id))
        self.store.audit("llm", "triaged", t.ticket_id, model=self.llm.name, intent=tr.intent.value, language=tr.language.value,
                         confidence=tr.confidence, machine_ref=tr.machine_ref)
        return {"triage": tr}

    def _diagnose(self, st: AgentState) -> AgentState:
        ev = collect_evidence(st["ticket"], st["triage"], directory=self.directory, iot=self.iot, payments=self.payments,
                              store=self.store, settings=self.s, now=st["now"])
        self.store.audit("agent", "evidence_collected", st["ticket"].ticket_id,
                         user=bool(ev.user), machine=ev.machine.machine_id if ev.machine else None,
                         session=ev.session.session_id if ev.session else None, payments=len(ev.payments),
                         duplicate=ev.duplicate_payment.payment_id if ev.duplicate_payment else None,
                         snapshot=ev.snapshot.model_dump(mode="json") if ev.snapshot else None, notes=ev.notes)
        return {"evidence": ev}

    def _decide(self, st: AgentState) -> AgentState:
        d = decide(st["triage"], st["evidence"], self.s, st["now"], injection=st.get("injection", False))
        self.store.audit("policy", "decided", st["ticket"].ticket_id, outcome=d.outcome.value, reason_codes=d.reason_codes,
                         actions=[a.model_dump(mode="json") for a in d.actions])
        return {"decision": d}

    def _act(self, st: AgentState) -> AgentState:
        res = self.executor.execute(st["ticket"], st["evidence"], st["decision"].actions, st["now"])
        return {"results": res}

    def _shadow(self, st: AgentState) -> AgentState:
        self.store.audit("agent", "shadow_mode_no_execution", st["ticket"].ticket_id)
        return {"results": []}

    def _respond(self, st: AgentState) -> AgentState:
        d, ev = st["decision"], st["evidence"]
        results = st.get("results") or []
        facts = build_facts(ev, d, results if not self.shadow else None, self.shadow)
        lang = st["triage"].language if st["triage"].language != Language.UNKNOWN else (ev.user.language if ev.user else Language.EN)
        reply = self.llm.draft_reply(facts, lang)
        from laundryops.domain.models import NATIVE_LANGUAGES
        if lang not in NATIVE_LANGUAGES:
            reply = self.llm.translate(reply, lang)
        outcome = d.outcome
        if any(r.type.value == "escalate_human" for r in results):
            outcome = Outcome.ESCALATED
        ms = (time.perf_counter_ns() - st["started_ns"]) // 1_000_000
        out = TicketOutcome(
            ticket_id=st["ticket"].ticket_id, outcome=outcome, triage=st["triage"], decision=d, actions=results,
            reply=reply, reply_language=lang, handled_in_ms=int(ms), shadow=self.shadow,
            evidence_summary={
                "machine": ev.machine.machine_id if ev.machine else None,
                "session": ev.session.session_id if ev.session else None,
                "payments": [p.payment_id for p in ev.payments],
                "duplicate": ev.duplicate_payment.payment_id if ev.duplicate_payment else None,
                "snapshot": ev.snapshot.model_dump(mode="json") if ev.snapshot else None,
                "relay_failures_7d": ev.relay_failures_7d,
                "auto_refunds_30d": ev.auto_refunds_30d,
            },
        )
        return {"facts": facts, "reply": reply, "outcome": out}

    # ---- graph ------------------------------------------------------------------------------
    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("intake", self._intake)
        g.add_node("triage", self._triage)
        g.add_node("diagnose", self._diagnose)
        g.add_node("decide", self._decide)
        g.add_node("act", self._act)
        g.add_node("shadow", self._shadow)
        g.add_node("respond", self._respond)
        g.set_entry_point("intake")
        g.add_edge("intake", "triage")
        g.add_edge("triage", "diagnose")
        g.add_edge("diagnose", "decide")
        g.add_conditional_edges("decide", lambda st: "shadow" if self.shadow else "act", {"act": "act", "shadow": "shadow"})
        g.add_edge("act", "respond")
        g.add_edge("shadow", "respond")
        g.add_edge("respond", END)
        return g.compile()

    def run(self, ticket: TicketIn, now: datetime | None = None) -> TicketOutcome:
        final = self._graph.invoke({"ticket": ticket, "now": now or utcnow()})
        return final["outcome"]

    def mermaid(self) -> str:
        return self._graph.get_graph().draw_mermaid()
