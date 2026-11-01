"""Port for the IoT box that sits between the platform and a washer/dryer.

A real implementation talks to WeWash's box backend (or any retrofit box with a relay and a
power meter). The agent only ever uses this interface, so swapping the mock for the real
thing is a one-file change.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from laundryops.domain.models import Machine, RelayResult, TelemetryEvent, TelemetrySnapshot


class TelemetryPort(Protocol):
    def get_machine(self, machine_id: str) -> Machine | None: ...
    def list_machines(self) -> list[Machine]: ...
    def machines_for_site(self, site_id: str) -> list[Machine]: ...
    def get_snapshot(self, machine_id: str) -> TelemetrySnapshot | None: ...
    def get_events(self, machine_id: str, since: datetime) -> list[TelemetryEvent]: ...
    def retrigger_relay(self, machine_id: str, session_id: str) -> RelayResult: ...
