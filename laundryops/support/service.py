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

    def handle(self, ticket: TicketIn, now: datetime | None = None) -> TicketOutcome:
        out = self.support.run(ticket, now)
        self.store.save_ticket(ticket, out)
        return out
