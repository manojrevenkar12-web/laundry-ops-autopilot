from __future__ import annotations

from datetime import datetime, timezone

import pytest

from laundryops.config import Settings
from laundryops.domain.models import Language, Machine, MachineKind, Partner, Site, User
from laundryops.store.db import Store
from laundryops.support.service import Platform


@pytest.fixture
def now() -> datetime:
    # The mock adapters plant data relative to the wall clock, so the test clock is the wall clock too.
    return datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def settings() -> Settings:
    return Settings(db_path=":memory:", llm_mode="offline")


@pytest.fixture
def platform(settings: Settings) -> Platform:
    p = Platform(settings=settings, store=Store(":memory:"))
    d, iot = p.directory, p.iot
    d.add_partner(Partner(partner_id="P1", name="Hausverwaltung Nord GmbH", iban="DE89370400440532013000",
                          bic="COBADEFFXXX", contact_email="hv@example.org"))
    d.add_site(Site(site_id="S1", partner_id="P1", name="Nord", city="München", building_manager_email="hm@example.org"))
    for i in range(1, 4):
        iot.add_machine(Machine(machine_id=f"WM-{i:04d}", site_id="S1", kind=MachineKind.WASHER,
                                label=f"Waschmaschine {i}", price_cents=350))
    iot.add_machine(Machine(machine_id="TD-0001", site_id="S1", kind=MachineKind.DRYER, label="Trockner 1", price_cents=200))
    d.add_user(User(user_id="u_de", site_id="S1", language=Language.DE, display_name="Lena Hoffmann", email="lena@example.org"))
    d.add_user(User(user_id="u_en", site_id="S1", language=Language.EN, display_name="Tom Reed", email="tom@example.org"))
    d.add_user(User(user_id="u_tr", site_id="S1", language=Language.TR, display_name="Ayşe Kaya", email="ayse@example.org"))
    return p
