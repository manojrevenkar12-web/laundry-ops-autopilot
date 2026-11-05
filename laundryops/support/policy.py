"""The policy engine: plain, deterministic code that decides what happens to a ticket.

Inputs: the triage hint, the evidence bundle, the settings. Output: a Decision with a list of
planned actions and machine-readable reason codes. No model is involved, so the same ticket
and the same evidence always produce the same decision, and an auditor can read why.

Principles
----------
* Money only moves on ledger evidence (duplicate capture, orphan capture, a cycle the box
  says failed). The resident's claim steers where we look; it never authorises a refund.
* Amounts are always the ledger amount of a specific payment, never a number from the text.
* Hardware is only actuated when the box says the paid session never started and the box is
  healthy. One retry. Repeat relay failures mark the machine suspect instead.
* Every refund is checked against per-user caps; the cap is a hard boundary, not a hint.
* Identity, damage, legal and "we cannot verify" always go to a human.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from laundryops.config import Settings
from laundryops.domain.models import (
    ActionType,
    CycleState,
    Decision,
    Evidence,
    Intent,
    Outcome,
    Payment,
    PlannedAction,
    RefundStatus,
    TriageResult,
)

MONEY_OR_HARDWARE_INTENTS = {
    Intent.MACHINE_NOT_STARTED, Intent.CYCLE_ABORTED, Intent.DOUBLE_CHARGE, Intent.CHARGED_NO_SESSION,
    Intent.REFUND_STATUS, Intent.RESERVATION_CONFLICT, Intent.UNVERIFIABLE_COMPLAINT, Intent.UNKNOWN,
}


def _escalate(codes: list[str], explanation: str, extra: list[PlannedAction] | None = None, reason: str = "") -> Decision:
    actions = list(extra or [])
    actions.append(PlannedAction(type=ActionType.ESCALATE_HUMAN, reason_code=codes[0], detail={"why": reason}))
    return Decision(outcome=Outcome.ESCALATED, actions=actions, reason_codes=codes, explanation=explanation)


def _refundable(p: Payment) -> int:
    return max(0, p.amount_cents - p.refunded_cents)


def _cap_check(ev: Evidence, s: Settings, planned_cents: int) -> str | None:
    """Return a reason code if the planned refund would breach a cap, else None."""
    if planned_cents > s.max_auto_refund_cents:
        return "ESC_AMOUNT_OVER_CAP"
    if ev.auto_refunds_30d + 1 > s.max_auto_refunds_per_user_30d:
        return "ESC_CAP_COUNT_EXCEEDED"
    if ev.auto_refund_total_30d_cents + planned_cents > s.max_auto_refund_total_per_user_30d_cents:
        return "ESC_CAP_TOTAL_EXCEEDED"
    return None


def _fault_action(ev: Evidence, signal: str) -> PlannedAction:
    return PlannedAction(type=ActionType.OPEN_FAULT_TICKET, machine_id=ev.machine.machine_id if ev.machine else None,
                        reason_code="ACT_FAULT_TICKET", detail={"signal": signal})


def decide(triage: TriageResult, ev: Evidence, s: Settings, now: datetime, *, injection: bool = False) -> Decision:
    codes: list[str] = []

    # ---- 1. hard escalations -----------------------------------------------------------
    if injection:
        return _escalate(["ESC_INJECTION"], "Message contains instruction-like content; routed to a human.", reason="injection")
    if triage.mentions_legal_or_abuse or triage.intent == Intent.ESCALATION_REQUIRED:
        return _escalate(["ESC_LEGAL_OR_ABUSE"], "Legal threat / abuse / safety wording.", reason="legal")
    if triage.mentions_damage or triage.intent == Intent.DAMAGE_CLAIM:
        return _escalate(["ESC_DAMAGE_CLAIM"], "Damage claims are reviewed by a person.", reason="damage")

    # ---- 2. partner channel --------------------------------------------------------------
    if triage.intent == Intent.PARTNER_PAYOUT and ev.user is not None and ev.partner is None:
        # An identified resident using partner-ish words ("where is my money") is asking about their own refund.
        triage = triage.model_copy(update={"intent": Intent.REFUND_STATUS})
    if triage.intent == Intent.PARTNER_PAYOUT or ev.partner is not None:
        if ev.partner is None:
            return _escalate(["ESC_NO_IDENTITY"], "Payout question without an identified partner.", reason="identity")
        return Decision(outcome=Outcome.AUTO_RESOLVED, reason_codes=["ACT_PARTNER_STATEMENT"],
                        explanation="Partner identified; statement and payout status served from settlement.",
                        actions=[PlannedAction(type=ActionType.PARTNER_STATEMENT, reason_code="ACT_PARTNER_STATEMENT",
                                               detail={"partner_id": ev.partner.partner_id})])

    # ---- 3. no-money intents -------------------------------------------------------------
    if triage.intent == Intent.GENERAL_QUESTION:
        return Decision(outcome=Outcome.AUTO_RESOLVED, reason_codes=["ACT_INFO_FAQ"], explanation="FAQ answer.",
                        actions=[PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_FAQ", detail={"info_kind": "faq_general"})])
    if triage.intent == Intent.APP_OR_PAYMENT_METHOD:
        return Decision(outcome=Outcome.AUTO_RESOLVED, reason_codes=["ACT_INFO_APP"], explanation="App / payment-method guidance.",
                        actions=[PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_APP", detail={"info_kind": "faq_app"})])
    if triage.intent == Intent.MACHINE_REPORT:
        if ev.machine:
            return Decision(outcome=Outcome.AUTO_RESOLVED, reason_codes=["ACT_FAULT_TICKET", "ACT_INFO_REPORT"],
                            explanation="Resident report logged as a fault ticket.",
                            actions=[_fault_action(ev, "resident_report"),
                                     PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_REPORT", detail={"info_kind": "report_thanks"})])
        return Decision(outcome=Outcome.AUTO_RESOLVED, reason_codes=["ACT_INFO_REPORT_WHICH"],
                        explanation="Report without a resolvable machine; asked which one.",
                        actions=[PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_REPORT_WHICH", detail={"info_kind": "report_which"})])

    # ---- 4. everything below needs an identified resident --------------------------------
    if ev.user is None:
        return _escalate(["ESC_NO_IDENTITY"], "Money or hardware question from an unidentified sender.", reason="identity")

    # ---- 5. evidence-driven remediation (independent of what the resident claimed) --------
    actions: list[PlannedAction] = []
    planned_refund_cents = 0

    # 5a duplicate capture -> refund the duplicate
    if ev.duplicate_payment and _refundable(ev.duplicate_payment) > 0:
        amt = _refundable(ev.duplicate_payment)
        actions.append(PlannedAction(type=ActionType.REFUND, amount_cents=amt, payment_id=ev.duplicate_payment.payment_id,
                                     reason_code="ACT_REFUND_DUPLICATE", detail={"evidence": "EV_DUPLICATE_CAPTURE"}))
        codes += ["EV_DUPLICATE_CAPTURE", "ACT_REFUND_DUPLICATE"]
        planned_refund_cents += amt

    # 5b orphan capture (money, no session) -> refund
    orphan = next((p for p in ev.payments if p.session_id is None and _refundable(p) > 0), None)
    if orphan and triage.intent in (Intent.CHARGED_NO_SESSION, Intent.MACHINE_NOT_STARTED, Intent.UNKNOWN,
                                    Intent.REFUND_STATUS, Intent.DOUBLE_CHARGE):
        amt = _refundable(orphan)
        actions.append(PlannedAction(type=ActionType.REFUND, amount_cents=amt, payment_id=orphan.payment_id,
                                     reason_code="ACT_REFUND_ORPHAN", detail={"evidence": "EV_ORPHAN_CAPTURE"}))
        codes += ["EV_ORPHAN_CAPTURE", "ACT_REFUND_ORPHAN"]
        planned_refund_cents += amt

    # 5c the resident's paid session on a machine we can see
    session_payment = None
    if ev.session:
        session_payment = next((p for p in ev.payments if p.session_id == ev.session.session_id and _refundable(p) > 0), None)
    snap = ev.snapshot
    if ev.session and snap and session_payment and ev.session.state != CycleState.FINISHED:
        fresh = (now - ev.session.reserved_at) <= timedelta(minutes=s.relay_retrigger_window_min)
        aborted = snap.cycle_state == CycleState.ABORTED or any(
            e.kind == "cycle_abort" and e.detail.get("session_id") == ev.session.session_id for e in ev.events)
        started_event = any(e.kind == "cycle_start" and e.detail.get("session_id") == ev.session.session_id for e in ev.events)
        never_started = (ev.session.started_at is None and ev.session.state == CycleState.RESERVED and not started_event
                         and snap.active_session_id in (None, ev.session.session_id) and not snap.relay_engaged
                         and snap.cycle_state in (CycleState.RESERVED, CycleState.IDLE) and snap.power_draw_w < 5)
        running_for_user = (snap.active_session_id == ev.session.session_id and snap.cycle_state == CycleState.RUNNING)
        running_for_other = (snap.cycle_state == CycleState.RUNNING and snap.active_session_id
                             and snap.active_session_id != ev.session.session_id)
        amt = _refundable(session_payment)

        if "caller_confirmed_failure_after_retrigger" in ev.notes:
            codes.append("EV_RETRIGGER_CONFIRMED_FAILED")
            actions.append(PlannedAction(type=ActionType.REFUND, amount_cents=amt, payment_id=session_payment.payment_id,
                                         reason_code="ACT_REFUND_FULL", detail={"evidence": "EV_RETRIGGER_CONFIRMED_FAILED"}))
            actions.append(_fault_action(ev, "relay_fail"))
            codes += ["ACT_REFUND_FULL", "ACT_FAULT_TICKET"]
            planned_refund_cents += amt
        elif aborted:
            codes.append("EV_CYCLE_ABORT")
            actions.append(PlannedAction(type=ActionType.REFUND, amount_cents=amt, payment_id=session_payment.payment_id,
                                         reason_code="ACT_REFUND_FULL", detail={"evidence": "EV_CYCLE_ABORT"}))
            actions.append(_fault_action(ev, "cycle_abort"))
            codes += ["ACT_REFUND_FULL", "ACT_FAULT_TICKET"]
            planned_refund_cents += amt
        elif never_started and not snap.online:
            codes.append("EV_BOX_OFFLINE")
            actions.append(PlannedAction(type=ActionType.REFUND, amount_cents=amt, payment_id=session_payment.payment_id,
                                         reason_code="ACT_REFUND_FULL", detail={"evidence": "EV_BOX_OFFLINE"}))
            actions.append(_fault_action(ev, "box_offline"))
            codes += ["ACT_REFUND_FULL", "ACT_FAULT_TICKET"]
            planned_refund_cents += amt
        elif never_started and (ev.relay_failures_7d >= s.relay_fault_threshold_7d or not fresh):
            why = "EV_RELAY_SUSPECT" if ev.relay_failures_7d >= s.relay_fault_threshold_7d else "EV_SESSION_STALE"
            codes.append(why)
            actions.append(PlannedAction(type=ActionType.REFUND, amount_cents=amt, payment_id=session_payment.payment_id,
                                         reason_code="ACT_REFUND_FULL", detail={"evidence": why}))
            actions.append(_fault_action(ev, "relay_suspect" if why == "EV_RELAY_SUSPECT" else "session_stale"))
            codes += ["ACT_REFUND_FULL", "ACT_FAULT_TICKET"]
            planned_refund_cents += amt
        elif never_started and fresh:
            # Try the cheap fix first; the executor refunds + opens a fault if the relay does not respond.
            codes += ["EV_RELAY_NOT_ENGAGED", "ACT_RETRIGGER"]
            contingency_cap = _cap_check(ev, s, planned_refund_cents + amt)
            actions.append(PlannedAction(
                type=ActionType.RETRIGGER_RELAY, machine_id=snap.machine_id, payment_id=session_payment.payment_id,
                amount_cents=amt, reason_code="ACT_RETRIGGER",
                detail={"session_id": ev.session.session_id,
                        "on_failure": "escalate" if contingency_cap else "refund_and_fault",
                        "contingency_block": contingency_cap}))
        elif running_for_other and ev.session.state == CycleState.RESERVED:
            codes.append("EV_RUNNING_OTHER_SESSION")
            actions.append(PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_CONFLICT", detail={"info_kind": "conflict"}))
            if ev.goodwill_credits_30d < s.goodwill_credits_per_user_30d:
                actions.append(PlannedAction(type=ActionType.GOODWILL_CREDIT, amount_cents=s.goodwill_credit_cents,
                                             reason_code="ACT_GOODWILL", detail={"evidence": "EV_RUNNING_OTHER_SESSION"}))
                codes.append("ACT_GOODWILL")
            else:
                codes.append("GOODWILL_CAP_REACHED")
        elif running_for_user and triage.intent in (Intent.MACHINE_NOT_STARTED, Intent.CYCLE_ABORTED, Intent.UNKNOWN):
            codes += ["EV_NO_ANOMALY_RUNNING", "ACT_INFO_RUNNING"]
            actions.append(PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_RUNNING",
                                         detail={"info_kind": "running", "started_at": (ev.session.started_at or snap.observed_at).isoformat()}))

    # ---- 6. refund-status question with nothing to fix ----------------------------------------
    if triage.intent == Intent.REFUND_STATUS and not actions:
        if ev.refunds:
            r = sorted(ev.refunds, key=lambda x: x.created_at)[-1]
            codes += ["EV_REFUND_FOUND", "ACT_INFO_REFUND_STATUS"]
            actions.append(PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_REFUND_STATUS",
                                         detail={"info_kind": "refund_found", "refund": {
                                             "amount_cents": r.amount_cents, "date": r.created_at.date().isoformat(),
                                             "status": r.status.value if isinstance(r.status, RefundStatus) else str(r.status)}}))
        else:
            last = ev.payments[-1].amount_cents if ev.payments else 0
            codes += ["EV_NO_REFUND", "ACT_INFO_NO_REFUND"]
            actions.append(PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_NO_REFUND",
                                         detail={"info_kind": "no_refund_found", "last_charge_cents": last}))

    # ---- 7. "double charge" claimed, ledger shows one capture ------------------------------------
    if triage.intent == Intent.DOUBLE_CHARGE and not actions and ev.payments:
        codes += ["EV_SINGLE_CAPTURE", "ACT_INFO_SINGLE_CHARGE"]
        actions.append(PlannedAction(type=ActionType.SEND_INFO, reason_code="ACT_INFO_SINGLE_CHARGE",
                                     detail={"info_kind": "single_charge", "last_charge_cents": ev.payments[-1].amount_cents}))

    # ---- 8. nothing verifiable -> human ----------------------------------------------------------
    if not actions:
        return _escalate(["ESC_NO_EVIDENCE"], "Claim could not be matched to ledger or telemetry evidence.", reason="evidence")

    # ---- 9. caps on anything that moves money now -----------------------------------------------
    if planned_refund_cents:
        block = _cap_check(ev, s, planned_refund_cents)
        if block:
            keep = [a for a in actions if a.type == ActionType.OPEN_FAULT_TICKET]
            return _escalate([block] + codes, f"Refund of {planned_refund_cents} ct blocked by cap {block}; human review.",
                             extra=keep, reason="cap")

    return Decision(outcome=Outcome.AUTO_RESOLVED, actions=actions, reason_codes=codes,
                    explanation="; ".join(codes))
