"""In-memory stand-in for a WeWash-Box style IoT backend.

It holds a fleet, a live snapshot per machine and an event log, and simulates the one
actuation the agent is allowed to perform: re-triggering the relay that releases the door /
powers the machine for a paid session. Scenario planting (``plant_*``) lets the eval harness
create the exact telemetry picture a ticket should be judged against.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from laundryops.domain.models import (
    CycleState,
    Machine,
    RelayResult,
    TelemetryEvent,
    TelemetrySnapshot,
    utcnow,
)


class MockWeWashBox:
    def __init__(self, machines: list[Machine] | None = None, clock=utcnow):
        self._clock = clock
        self._machines: dict[str, Machine] = {m.machine_id: m for m in (machines or [])}
        self._snapshots: dict[str, TelemetrySnapshot] = {}
        self._events: dict[str, list[TelemetryEvent]] = {}
        self._relay_fault: dict[str, bool] = {}       # hardware fault: retrigger will fail
        self.relay_calls: list[tuple[str, str]] = []  # (machine_id, session_id) for assertions
        for m in self._machines.values():
            self._snapshots[m.machine_id] = self._idle_snapshot(m.machine_id)

    # ---- fleet -----------------------------------------------------------------
    def add_machine(self, m: Machine) -> None:
        self._machines[m.machine_id] = m
        self._snapshots.setdefault(m.machine_id, self._idle_snapshot(m.machine_id))

    def get_machine(self, machine_id: str) -> Machine | None:
        return self._machines.get(machine_id)

    def list_machines(self) -> list[Machine]:
        return list(self._machines.values())

    def machines_for_site(self, site_id: str) -> list[Machine]:
        return [m for m in self._machines.values() if m.site_id == site_id]

    # ---- telemetry ---------------------------------------------------------------
    def get_snapshot(self, machine_id: str) -> TelemetrySnapshot | None:
        return self._snapshots.get(machine_id)

    def get_events(self, machine_id: str, since: datetime) -> list[TelemetryEvent]:
        return [e for e in self._events.get(machine_id, []) if e.at >= since]

    def set_snapshot(self, snap: TelemetrySnapshot) -> None:
        self._snapshots[snap.machine_id] = snap

    def add_event(self, ev: TelemetryEvent) -> None:
        self._events.setdefault(ev.machine_id, []).append(ev)

    def set_relay_fault(self, machine_id: str, faulty: bool) -> None:
        self._relay_fault[machine_id] = faulty

    # ---- actuation ---------------------------------------------------------------
    def retrigger_relay(self, machine_id: str, session_id: str) -> RelayResult:
        now = self._clock()
        self.relay_calls.append((machine_id, session_id))
        snap = self._snapshots.get(machine_id)
        if snap is None:
            return RelayResult(machine_id=machine_id, success=False, message="unknown machine", at=now)
        if not snap.online:
            self.add_event(TelemetryEvent(machine_id=machine_id, at=now, kind="relay_fail", detail={"why": "offline"}))
            return RelayResult(machine_id=machine_id, success=False, message="box offline", at=now)
        if self._relay_fault.get(machine_id):
            self.add_event(TelemetryEvent(machine_id=machine_id, at=now, kind="relay_fail", detail={"why": "no_power_draw"}))
            return RelayResult(machine_id=machine_id, success=False, message="relay engaged but no power draw", at=now)
        self._snapshots[machine_id] = snap.model_copy(update={
            "observed_at": now, "door_locked": False, "relay_engaged": True, "power_draw_w": 480.0,
            "cycle_state": CycleState.RUNNING, "active_session_id": session_id,
        })
        self.add_event(TelemetryEvent(machine_id=machine_id, at=now, kind="relay_ok", detail={"session_id": session_id}))
        return RelayResult(machine_id=machine_id, success=True, message="relay re-triggered, power draw 480 W", at=now)

    # ---- scenario planting (used by seed/eval, never by the agent) -------------------
    def _idle_snapshot(self, machine_id: str) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            machine_id=machine_id, observed_at=self._clock(), online=True, door_locked=True,
            relay_engaged=False, power_draw_w=1.2, cycle_state=CycleState.IDLE,
        )

    def plant_not_started(self, machine_id: str, session_id: str, relay_fault: bool = False, offline: bool = False) -> None:
        now = self._clock()
        self._snapshots[machine_id] = TelemetrySnapshot(
            machine_id=machine_id, observed_at=now - timedelta(minutes=1 if not offline else 35),
            online=not offline, door_locked=True, relay_engaged=False, power_draw_w=0.8,
            cycle_state=CycleState.RESERVED, active_session_id=session_id,
        )
        if offline:
            self.add_event(TelemetryEvent(machine_id=machine_id, at=now - timedelta(minutes=35), kind="offline"))
        self.set_relay_fault(machine_id, relay_fault)

    def plant_aborted(self, machine_id: str, session_id: str, minutes_in: int = 12) -> None:
        now = self._clock()
        self.add_event(TelemetryEvent(machine_id=machine_id, at=now - timedelta(minutes=minutes_in + 20),
                                      kind="cycle_start", detail={"session_id": session_id}))
        self.add_event(TelemetryEvent(machine_id=machine_id, at=now - timedelta(minutes=20),
                                      kind="power_drop", detail={"from_w": 510.0, "to_w": 0.0}))
        self.add_event(TelemetryEvent(machine_id=machine_id, at=now - timedelta(minutes=20),
                                      kind="cycle_abort", detail={"session_id": session_id, "minutes_in": minutes_in}))
        self._snapshots[machine_id] = TelemetrySnapshot(
            machine_id=machine_id, observed_at=now, online=True, door_locked=False, relay_engaged=False,
            power_draw_w=0.0, cycle_state=CycleState.ABORTED, active_session_id=session_id,
        )

    def plant_running_other_user(self, machine_id: str, other_session_id: str) -> None:
        now = self._clock()
        self._snapshots[machine_id] = TelemetrySnapshot(
            machine_id=machine_id, observed_at=now, online=True, door_locked=True, relay_engaged=True,
            power_draw_w=620.0, cycle_state=CycleState.RUNNING, active_session_id=other_session_id,
        )

    def plant_relay_failures(self, machine_id: str, n: int) -> None:
        now = self._clock()
        for i in range(n):
            self.add_event(TelemetryEvent(machine_id=machine_id, at=now - timedelta(days=i + 1), kind="relay_fail",
                                          detail={"why": "no_power_draw"}))
