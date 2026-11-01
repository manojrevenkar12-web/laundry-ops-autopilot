"""Fleet health: turn box telemetry into fault tickets *before* residents complain.

Three jobs, all deterministic and schedulable (cron every 10 minutes in production):

1. ``assess``   — score every machine from telemetry + ticket history.
2. ``dispatch`` — open/dedupe fault tickets for unhealthy machines, track SLA clocks, escalate breaches.
3. ``proactive_sweep`` — find paid sessions the box already knows failed (abort, offline, relay
   fault) and push them through the same support pipeline as a resident ticket would be, so the
   refund lands before the complaint is written. Same policy, same guardrails, same audit trail.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Callable

from laundryops.adapters import Directory, Notifier, PaymentPort, TelemetryPort
from laundryops.config import Settings
from laundryops.domain.models import (
    Channel,
    CycleState,
    FaultSeverity,
    FaultStatus,
    FaultTicket,
    MachineHealth,
    TicketIn,
    TicketOutcome,
    utcnow,
)
from laundryops.store.db import Store

_SEV_ORDER = {FaultSeverity.LOW: 0, FaultSeverity.MEDIUM: 1, FaultSeverity.HIGH: 2, FaultSeverity.CRITICAL: 3}


class FleetService:
    def __init__(self, *, directory: Directory, iot: TelemetryPort, payments: PaymentPort, store: Store,
                 notifier: Notifier, settings: Settings):
        self.directory, self.iot, self.payments, self.store, self.notifier, self.s = directory, iot, payments, store, notifier, settings

    # ---- 1. assess ----------------------------------------------------------------------------
    def assess(self, now: datetime | None = None) -> list[MachineHealth]:
        now = now or utcnow()
        since_7d = now - timedelta(days=7)
        out: list[MachineHealth] = []
        for m in self.iot.list_machines():
            snap = self.iot.get_snapshot(m.machine_id)
            events = self.iot.get_events(m.machine_id, since_7d)
            relay_fail = sum(1 for e in events if e.kind == "relay_fail")
            starts = sum(1 for e in events if e.kind == "cycle_start")
            aborts = sum(1 for e in events if e.kind == "cycle_abort")
            abort_rate = aborts / starts if starts else (1.0 if aborts else 0.0)
            offline_min = 0
            if snap and not snap.online:
                offline_min = int((now - snap.observed_at).total_seconds() // 60)
            tickets = self.store.tickets_for_machine_7d(m.machine_id, now)
            signals: list[str] = []
            score = 100
            if offline_min >= self.s.offline_minutes_critical:
                signals.append(f"offline_{offline_min}min"); score -= 60
            elif offline_min > 0:
                signals.append(f"offline_{offline_min}min"); score -= 25
            if relay_fail >= self.s.relay_fault_threshold_7d:
                signals.append(f"relay_failures_7d={relay_fail}"); score -= 45
            elif relay_fail:
                signals.append(f"relay_failures_7d={relay_fail}"); score -= 10
            if abort_rate >= self.s.abort_rate_warning and aborts:
                signals.append(f"abort_rate_7d={abort_rate:.0%}"); score -= 30
            if tickets >= self.s.tickets_7d_warning:
                signals.append(f"tickets_7d={tickets}"); score -= 15
            if snap and snap.cycle_state == CycleState.FAULT:
                signals.append("box_reports_fault"); score -= 40
            score = max(0, score)
            sev = (FaultSeverity.CRITICAL if score < 30 else FaultSeverity.HIGH if score < 60
                   else FaultSeverity.MEDIUM if score < 85 else FaultSeverity.LOW)
            out.append(MachineHealth(machine_id=m.machine_id, site_id=m.site_id, score=score, severity=sev, signals=signals,
                                     offline_minutes=offline_min, relay_failures_7d=relay_fail,
                                     abort_rate_7d=round(abort_rate, 3), tickets_7d=tickets))
        return sorted(out, key=lambda h: h.score)

    # ---- 2. dispatch ----------------------------------------------------------------------------
    def dispatch(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        opened, escalated_breaches = [], []
        for h in self.assess(now):
            if _SEV_ORDER[h.severity] < _SEV_ORDER[FaultSeverity.HIGH]:
                continue
            if self.store.open_fault_for_machine(h.machine_id):
                continue
            f = FaultTicket(
                fault_id=f"flt_{uuid.uuid4().hex[:8]}", machine_id=h.machine_id, site_id=h.site_id, severity=h.severity,
                status=FaultStatus.OPEN, opened_at=now, ack_due_at=now + timedelta(hours=self.s.fault_ack_sla_hours),
                fix_due_at=now + timedelta(hours=self.s.fault_fix_sla_hours), source="fleet_health", signals=h.signals,
            )
            self.store.upsert_fault(f)
            site = self.directory.get_site(h.site_id)
            machine = self.iot.get_machine(h.machine_id)
            if site and machine:
                self.notifier.send(site.building_manager_email, "email",
                                   f"[{h.severity.value.upper()}] {machine.label} ({h.machine_id}) braucht einen Techniker",
                                   f"Signale: {', '.join(h.signals)}. Bitte bis {f.ack_due_at:%d.%m. %H:%M} bestätigen.",
                                   fault_id=f.fault_id)
            self.store.audit("fleet", "fault_opened", None, fault_id=f.fault_id, machine_id=h.machine_id, signals=h.signals)
            opened.append(f.fault_id)
        # SLA clocks
        for f in self.store.list_faults():
            if f.status == FaultStatus.FIXED:
                continue
            breach = None
            if f.status == FaultStatus.OPEN and now > f.ack_due_at:
                breach = "ack_sla_breached"
            elif now > f.fix_due_at:
                breach = "fix_sla_breached"
            if breach and breach not in f.signals:
                f.signals.append(breach)
                self.store.upsert_fault(f)
                self.notifier.send("ops-lead@operator.example", "email", f"SLA breach {f.fault_id} ({f.machine_id})", breach, fault_id=f.fault_id)
                self.store.audit("fleet", "sla_breach", None, fault_id=f.fault_id, breach=breach)
                escalated_breaches.append(f.fault_id)
        return {"opened": opened, "sla_breaches": escalated_breaches}

    def acknowledge(self, fault_id: str, now: datetime | None = None) -> FaultTicket | None:
        return self._transition(fault_id, FaultStatus.ACKNOWLEDGED, now)

    def fix(self, fault_id: str, now: datetime | None = None) -> FaultTicket | None:
        return self._transition(fault_id, FaultStatus.FIXED, now)

    def _transition(self, fault_id: str, status: FaultStatus, now: datetime | None) -> FaultTicket | None:
        now = now or utcnow()
        f = next((x for x in self.store.list_faults() if x.fault_id == fault_id), None)
        if not f:
            return None
        f.status = status
        if status == FaultStatus.ACKNOWLEDGED:
            f.acknowledged_at = now
        if status == FaultStatus.FIXED:
            f.fixed_at = now
        self.store.upsert_fault(f)
        self.store.audit("fleet", f"fault_{status.value}", None, fault_id=fault_id)
        return f

    # ---- 3. proactive -------------------------------------------------------------------------
    def proactive_sweep(self, handle: Callable[[TicketIn, datetime], TicketOutcome], now: datetime | None = None,
                        lookback_minutes: int = 90) -> list[TicketOutcome]:
        """Push box-detected failures through the support pipeline before anyone complains."""
        now = now or utcnow()
        since = now - timedelta(minutes=lookback_minutes)
        outcomes: list[TicketOutcome] = []
        for m in self.iot.list_machines():
            snap = self.iot.get_snapshot(m.machine_id)
            if not snap or not snap.active_session_id:
                continue
            sess = self.payments.get_session(snap.active_session_id)
            if not sess or sess.reserved_at < since or sess.state == CycleState.FINISHED:
                continue
            paid = [p for p in self.payments.payments_for_session(sess.session_id) if p.amount_cents > p.refunded_cents]
            if not paid:
                continue
            failed = (snap.cycle_state == CycleState.ABORTED or (not snap.online)
                      or any(e.kind == "relay_fail" for e in self.iot.get_events(m.machine_id, sess.reserved_at)))
            if not failed:
                continue
            if self.store.get_ticket(f"pro_{sess.session_id}"):
                continue
            user = self.directory.get_user(sess.user_id)
            text = {"de": f"[proaktiv] Störung an {m.label} während Buchung {sess.session_id}",
                    "tr": f"[proaktif] {m.label} rezervasyon {sess.session_id} sırasında arıza",
                    "fr": f"[proactif] Panne sur {m.label} pendant la réservation {sess.session_id}",
                    "es": f"[proactivo] Avería en {m.label} durante la reserva {sess.session_id}",
                    }.get(user.language.value if user else "en", f"[proactive] Fault on {m.label} during booking {sess.session_id}")
            t = TicketIn(ticket_id=f"pro_{sess.session_id}", user_id=sess.user_id, channel=Channel.CHAT, text=text, received_at=now)
            out = handle(t, now)
            if user and out.outcome.value == "auto_resolved":
                self.notifier.send(user.email, "push", "Automatische Erstattung / automatic refund", out.reply, ticket_id=t.ticket_id)
            self.store.audit("fleet", "proactive_ticket", t.ticket_id, session_id=sess.session_id, outcome=out.outcome.value)
            outcomes.append(out)
        return outcomes

    def notify_residents_of_outage(self, machine_id: str, now: datetime | None = None, horizon_hours: int = 6) -> int:
        """Tell residents with upcoming reservations on a broken machine, and point them to a working one."""
        now = now or utcnow()
        m = self.iot.get_machine(machine_id)
        if not m:
            return 0
        alt = next((x for x in self.iot.machines_for_site(m.site_id) if x.machine_id != machine_id and x.kind == m.kind
                    and (self.iot.get_snapshot(x.machine_id) or None) and self.iot.get_snapshot(x.machine_id).online
                    and not self.store.open_fault_for_machine(x.machine_id)), None)
        n = 0
        for u in [u for u in self.directory.users.values() if u.site_id == m.site_id]:
            for s in self.payments.sessions_for_user(u.user_id, now - timedelta(hours=1)):
                if s.machine_id == machine_id and s.state == CycleState.RESERVED and s.reserved_at <= now + timedelta(hours=horizon_hours):
                    body = f"{m.label} ist außer Betrieb. " + (f"Bitte nutzen Sie {alt.label}." if alt else "") + " Ihre Buchung wird automatisch erstattet."
                    self.notifier.send(u.email, "push", "Maschine außer Betrieb / machine out of order", body, machine_id=machine_id)
                    n += 1
        return n
