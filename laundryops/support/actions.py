"""Action executor with last-line guardrails.

The policy engine decides; this module executes, and re-checks every precondition against
live data immediately before doing anything irreversible. In shadow mode nothing here runs.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from laundryops.adapters import Directory, Notifier, PaymentPort, RefundError, TelemetryPort
from laundryops.config import Settings
from laundryops.domain.models import (
    ActionResult,
    ActionType,
    CycleState,
    Evidence,
    FaultSeverity,
    FaultStatus,
    FaultTicket,
    PlannedAction,
    TicketIn,
)
from laundryops.store.db import Store

SEVERITY_BY_SIGNAL = {
    "cycle_abort": FaultSeverity.HIGH, "box_offline": FaultSeverity.HIGH, "relay_suspect": FaultSeverity.HIGH,
    "relay_fail": FaultSeverity.MEDIUM, "resident_report": FaultSeverity.MEDIUM, "session_stale": FaultSeverity.LOW,
}


class GuardrailViolation(Exception):
    pass


class ActionExecutor:
    def __init__(self, *, directory: Directory, iot: TelemetryPort, payments: PaymentPort, store: Store,
                 notifier: Notifier, settings: Settings, settlement=None):
        self.directory, self.iot, self.payments, self.store = directory, iot, payments, store
        self.notifier, self.s, self.settlement = notifier, settings, settlement

    # ---- public -------------------------------------------------------------------------------
    def execute(self, ticket: TicketIn, ev: Evidence, planned: list[PlannedAction], now: datetime) -> list[ActionResult]:
        results: list[ActionResult] = []
        for a in planned:
            try:
                results.extend(self._run(ticket, ev, a, now))
            except (GuardrailViolation, RefundError) as e:
                self.store.audit("executor", "guardrail_blocked", ticket.ticket_id, action=a.type.value, error=str(e))
                results.append(ActionResult(type=a.type, success=False, detail={"blocked": str(e)}))
                results.append(ActionResult(type=ActionType.ESCALATE_HUMAN, success=True,
                                            detail={"why": "guardrail", "error": str(e)}))
        return results

    # ---- individual actions ---------------------------------------------------------------------
    def _run(self, ticket: TicketIn, ev: Evidence, a: PlannedAction, now: datetime) -> list[ActionResult]:
        if a.type == ActionType.REFUND:
            return [self._refund(ticket, ev, a, now)]
        if a.type == ActionType.RETRIGGER_RELAY:
            return self._retrigger(ticket, ev, a, now)
        if a.type == ActionType.GOODWILL_CREDIT:
            c = self.payments.issue_credit(ev.user.user_id, a.amount_cents, a.reason_code)
            self.store.audit("executor", "credit_issued", ticket.ticket_id, credit_id=c.credit_id, amount_cents=c.amount_cents)
            return [ActionResult(type=a.type, success=True, detail={"amount_cents": c.amount_cents, "credit_id": c.credit_id,
                                                                     "reason_code": a.reason_code})]
        if a.type == ActionType.OPEN_FAULT_TICKET:
            return [self._fault(ticket, ev, a, now)]
        if a.type == ActionType.SEND_INFO:
            return [ActionResult(type=a.type, success=True, detail=dict(a.detail) | {"reason_code": a.reason_code})]
        if a.type == ActionType.PARTNER_STATEMENT:
            return [self._partner_statement(ticket, ev, a)]
        if a.type == ActionType.ESCALATE_HUMAN:
            self.store.audit("executor", "escalated", ticket.ticket_id, why=a.detail.get("why"), reason_code=a.reason_code)
            return [ActionResult(type=a.type, success=True, detail=dict(a.detail) | {"reason_code": a.reason_code})]
        return [ActionResult(type=a.type, success=False, detail={"blocked": "unknown action"})]

    def _refund(self, ticket: TicketIn, ev: Evidence, a: PlannedAction, now: datetime) -> ActionResult:
        # Guardrail 1: the amount must be the live ledger balance of that exact payment, for that exact user.
        p = self.payments.get_payment(a.payment_id or "")
        if p is None or ev.user is None or p.user_id != ev.user.user_id:
            raise GuardrailViolation("refund target does not belong to the identified user")
        refundable = p.amount_cents - p.refunded_cents
        if a.amount_cents <= 0 or a.amount_cents > refundable:
            raise GuardrailViolation(f"amount {a.amount_cents} exceeds refundable {refundable}")
        # Guardrail 2: hard ceiling and per-user caps, re-read from the store at execution time.
        if a.amount_cents > self.s.max_auto_refund_cents:
            raise GuardrailViolation("amount above automatic ceiling")
        n, total, _ = self.store.user_auto_money_30d(ev.user.user_id, now)
        if n + 1 > self.s.max_auto_refunds_per_user_30d or total + a.amount_cents > self.s.max_auto_refund_total_per_user_30d_cents:
            raise GuardrailViolation("per-user 30-day refund cap")
        # Guardrail 3: idempotent on ticket+payment so a retry can never double-refund.
        key = f"{ticket.ticket_id}:{p.payment_id}"
        r = self.payments.create_refund(p.payment_id, a.amount_cents, key, a.reason_code)
        self.store.audit("executor", "refund_created", ticket.ticket_id, refund_id=r.refund_id, payment_id=p.payment_id,
                         amount_cents=r.amount_cents, evidence=a.detail.get("evidence"), idempotency_key=key)
        return ActionResult(type=ActionType.REFUND, success=True, detail={
            "amount_cents": r.amount_cents, "payment_id": p.payment_id, "refund_id": r.refund_id,
            "reason_code": a.reason_code, "evidence": a.detail.get("evidence")})

    def _retrigger(self, ticket: TicketIn, ev: Evidence, a: PlannedAction, now: datetime) -> list[ActionResult]:
        # Guardrail: re-read the box right before actuating; refuse if the picture changed.
        snap = self.iot.get_snapshot(a.machine_id or "")
        if snap is None or not snap.online:
            raise GuardrailViolation("box not reachable at actuation time")
        if snap.cycle_state == CycleState.RUNNING and snap.active_session_id != a.detail.get("session_id"):
            raise GuardrailViolation("machine is running another session; will not actuate")
        if snap.power_draw_w >= 5:
            raise GuardrailViolation("machine already drawing power; will not actuate")
        res = self.iot.retrigger_relay(a.machine_id, a.detail["session_id"])
        self.store.audit("executor", "relay_retrigger", ticket.ticket_id, machine_id=a.machine_id, success=res.success, message=res.message)
        out = [ActionResult(type=ActionType.RETRIGGER_RELAY, success=res.success,
                            detail={"machine_id": a.machine_id, "message": res.message, "reason_code": a.reason_code})]
        if res.success:
            return out
        # Contingency decided by the policy engine up front.
        if a.detail.get("on_failure") == "refund_and_fault":
            out.append(self._refund(ticket, ev, PlannedAction(type=ActionType.REFUND, amount_cents=a.amount_cents,
                                                              payment_id=a.payment_id, reason_code="ACT_REFUND_FULL",
                                                              detail={"evidence": "EV_RELAY_FAIL"}), now))
            out.append(self._fault(ticket, ev, PlannedAction(type=ActionType.OPEN_FAULT_TICKET, machine_id=a.machine_id,
                                                             reason_code="ACT_FAULT_TICKET", detail={"signal": "relay_fail"}), now))
        else:
            out.append(ActionResult(type=ActionType.ESCALATE_HUMAN, success=True,
                                    detail={"why": "relay failed and refund blocked by cap", "block": a.detail.get("contingency_block")}))
        return out

    def _fault(self, ticket: TicketIn, ev: Evidence, a: PlannedAction, now: datetime) -> ActionResult:
        machine = self.iot.get_machine(a.machine_id or "")
        if machine is None:
            return ActionResult(type=a.type, success=False, detail={"blocked": "unknown machine"})
        existing = self.store.open_fault_for_machine(machine.machine_id)
        signal = a.detail.get("signal", "agent")
        if existing:
            if signal not in existing.signals:
                existing.signals.append(signal)
                self.store.upsert_fault(existing)
            self.store.audit("executor", "fault_deduped", ticket.ticket_id, fault_id=existing.fault_id, machine_id=machine.machine_id)
            return ActionResult(type=a.type, success=True, detail={"fault_id": existing.fault_id, "machine_id": machine.machine_id,
                                                                    "deduped": True, "reason_code": a.reason_code})
        sev = SEVERITY_BY_SIGNAL.get(signal, FaultSeverity.MEDIUM)
        f = FaultTicket(
            fault_id=f"flt_{uuid.uuid4().hex[:8]}", machine_id=machine.machine_id, site_id=machine.site_id, severity=sev,
            status=FaultStatus.OPEN, opened_at=now, ack_due_at=now + timedelta(hours=self.s.fault_ack_sla_hours),
            fix_due_at=now + timedelta(hours=self.s.fault_fix_sla_hours), source="agent", signals=[signal],
        )
        self.store.upsert_fault(f)
        site = self.directory.get_site(machine.site_id)
        if site:
            self.notifier.send(site.building_manager_email, "email", f"[{sev.value.upper()}] Störung {machine.label} ({machine.machine_id})",
                               f"Signal: {signal}. Bitte bis {f.ack_due_at:%d.%m. %H:%M} bestätigen.", fault_id=f.fault_id)
        self.store.audit("executor", "fault_opened", ticket.ticket_id, fault_id=f.fault_id, machine_id=machine.machine_id, severity=sev.value)
        return ActionResult(type=a.type, success=True, detail={"fault_id": f.fault_id, "machine_id": machine.machine_id,
                                                                "severity": sev.value, "reason_code": a.reason_code})

    def _partner_statement(self, ticket: TicketIn, ev: Evidence, a: PlannedAction) -> ActionResult:
        if self.settlement is None or ev.partner is None:
            return ActionResult(type=a.type, success=False, detail={"blocked": "settlement module not wired"})
        info = self.settlement.partner_status(ev.partner.partner_id)
        self.store.audit("executor", "partner_statement_served", ticket.ticket_id, partner_id=ev.partner.partner_id)
        return ActionResult(type=a.type, success=True, detail={"statement": info, "reason_code": a.reason_code})
