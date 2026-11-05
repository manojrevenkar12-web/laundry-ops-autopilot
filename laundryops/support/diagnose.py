"""Evidence collection: everything the policy engine may look at, pulled from systems of record.

The LLM's triage result is used only to *steer* which machine we look at. Every fact in the
Evidence object comes from the IoT box, the payment ledger or the directory — never from the
ticket text.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from laundryops.adapters import Directory, PaymentPort, TelemetryPort
from laundryops.config import Settings
from laundryops.domain.models import CycleState, Evidence, Payment, TicketIn, TriageResult
from laundryops.store.db import Store

DUPLICATE_WINDOW_S = 180


def _find_duplicate(payments: list[Payment]) -> Payment | None:
    """Two captures of the same amount within a short window for the same session (or no session)."""
    for i, a in enumerate(payments):
        for b in payments[i + 1:]:
            same_target = (a.session_id == b.session_id)
            close = abs((b.captured_at - a.captured_at).total_seconds()) <= DUPLICATE_WINDOW_S
            if same_target and close and a.amount_cents == b.amount_cents and b.refunded_cents == 0:
                return b
    return None


def _resolve_machine_id(triage: TriageResult, iot: TelemetryPort, site_id: str | None) -> str | None:
    ref = triage.machine_ref
    if not ref:
        return None
    if ref.startswith("label:") and site_id:
        n = ref.split(":", 1)[1]
        for m in iot.machines_for_site(site_id):
            if m.label.rstrip().endswith(f" {n}"):
                return m.machine_id
        return None
    return ref if iot.get_machine(ref) else None


def collect_evidence(ticket: TicketIn, triage: TriageResult, *, directory: Directory, iot: TelemetryPort,
                     payments: PaymentPort, store: Store, settings: Settings, now: datetime) -> Evidence:
    ev = Evidence()
    ev.user = directory.get_user(ticket.user_id)
    ev.partner = directory.get_partner(ticket.partner_id)
    if ev.user is None:
        ev.notes.append("no_identity")
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    if ev.user:
        ev.payments = payments.payments_for_user(ev.user.user_id, since_24h)
        ev.refunds = payments.refunds_for_user(ev.user.user_id, since_30d)
        ev.duplicate_payment = _find_duplicate(ev.payments)
        sessions = payments.sessions_for_user(ev.user.user_id, since_24h)
        machine_id = _resolve_machine_id(triage, iot, ev.user.site_id)
        # Prefer the session on the machine the resident named; otherwise their most recent session.
        session = None
        if machine_id:
            session = next((s for s in reversed(sessions) if s.machine_id == machine_id), None)
        if session is None and sessions:
            session = sessions[-1]
            machine_id = machine_id or session.machine_id
        ev.session = session
        if machine_id:
            ev.machine = iot.get_machine(machine_id)
            ev.snapshot = iot.get_snapshot(machine_id)
            ev.events = iot.get_events(machine_id, since_7d)
            ev.relay_failures_7d = sum(1 for e in ev.events if e.kind == "relay_fail")
        n, total, credits = store.user_auto_money_30d(ev.user.user_id, now)
        ev.auto_refunds_30d, ev.auto_refund_total_30d_cents, ev.goodwill_credits_30d = n, total, credits
    else:
        # Anonymous channels can still name a machine for a fault report.
        machine_id = triage.machine_ref if triage.machine_ref and not triage.machine_ref.startswith("label:") else None
        if machine_id:
            ev.machine = iot.get_machine(machine_id)
            ev.snapshot = iot.get_snapshot(machine_id)

    # A voice verify turn: we actuated the relay for this session minutes ago and the caller says the door is
    # still locked. The justification is our own failed actuation (in the audit store), not the caller's word.
    if ticket.context.get("confirmed_failure") and ev.user and ev.machine and ev.session:
        if store.recent_retrigger(ev.user.user_id, ev.machine.machine_id, now, minutes=15):
            ev.notes.append("caller_confirmed_failure_after_retrigger")
        else:
            ev.notes.append("confirmed_failure_without_retrigger")
    if ev.snapshot and ev.session and ev.snapshot.active_session_id == ev.session.session_id:
        ev.notes.append("snapshot_matches_session")
    if ev.snapshot and ev.snapshot.cycle_state == CycleState.ABORTED:
        ev.notes.append("cycle_aborted")
    return ev
