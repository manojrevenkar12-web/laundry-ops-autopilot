"""Stage a fresh, un-handled scenario for a live demo (console hotline panel, CLI rehearsal).

The seeded world's scenarios are consumed by the seeding run itself, so a demo caller needs a
booking that nothing has touched yet. Each call creates a new resident with a phone number and
plants exactly the telemetry/ledger picture the scenario needs.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timezone

from laundryops.domain.models import CycleState, Language, Machine, MachineKind, Partner, Site, User

SCENARIOS = {
    "door_locked": "Paid 4 min ago, door stays locked, relay healthy -> agent re-triggers; say the door is still locked to see refund + fault",
    "relay_fault": "Paid 4 min ago, relay does not respond -> immediate refund + fault ticket",
    "aborted": "Cycle died halfway 20 min ago -> refund + fault ticket",
    "double_charge": "Two captures 40 s apart for one finished cycle -> duplicate refunded",
    "refund_status": "A refund was issued 5 days ago -> status read back",
    "unknown_caller": "Number not in the directory -> SMS link + warm transfer",
    "damage": "Resident with a normal finished cycle; say clothes were damaged -> hand-over with whisper",
}
_counter = itertools.count(1)
_SAY = {
    "door_locked": {"de": "Ich hab bezahlt, aber die Tür geht nicht auf. / die {n} / Nein, immer noch zu. / Nein danke.",
                    "en": "I paid but the door won't open. / number {n} / No, still locked. / No thanks."},
    "relay_fault": {"de": "Waschmaschine {n} startet nicht, Tür bleibt zu.", "en": "Machine {n} won't start, the door stays locked."},
    "aborted": {"de": "Waschmaschine {n} ist mittendrin stehen geblieben, die Wäsche ist noch nass.",
                "en": "Machine {n} stopped halfway, my clothes are still wet."},
    "double_charge": {"de": "Mir wurde für Maschine {n} doppelt abgebucht.", "en": "I was charged twice for machine {n}."},
    "refund_status": {"de": "Wo bleibt meine Rückerstattung?", "en": "Where is my refund?"},
    "unknown_caller": {"de": "Ich wurde doppelt abgebucht.", "en": "I was charged twice."},
    "damage": {"de": "Der Trockner hat meinen Pullover zerrissen.", "en": "The dryer tore my sweater."},
}


def stage(platform, scenario: str, language: str = "de", now: datetime | None = None) -> dict:
    if scenario not in SCENARIOS:
        raise KeyError(scenario)
    now = now or datetime.now(timezone.utc)
    d, iot, pay = platform.directory, platform.iot, platform.payments
    k = next(_counter)
    lang = Language(language) if language in ("de", "en", "tr", "fr", "es") else Language.DE
    # a dedicated demo site so staged machines never collide with seeded ones
    if "SDEMO" not in d.sites:
        if "PDEMO" not in d.partners:
            d.add_partner(Partner(partner_id="PDEMO", name="Hausverwaltung Demo GmbH", iban="DE89370400440532013000",
                                  contact_email="demo-partner@example.org", phone="+4989990000"))
        d.add_site(Site(site_id="SDEMO", partner_id="PDEMO", name="Demo-Wohnheim", city="München",
                        building_manager_email="hausmeister-demo@example.org"))
        for i in range(1, 7):
            iot.add_machine(Machine(machine_id=f"WM-{9000 + i:04d}", site_id="SDEMO", kind=MachineKind.WASHER,
                                    label=f"Waschmaschine {i}", price_cents=350))
    n = (k % 6) + 1
    machine = iot.get_machine(f"WM-{9000 + n:04d}")
    phone = f"+49176000{k:05d}"
    name = ["Lena Hoffmann", "Tom Reed", "Ayşe Kaya", "Luca Rossi", "Marie Dubois", "Diego García"][k % 6]
    user = User(user_id=f"demo_{k:04d}", site_id="SDEMO", language=lang, display_name=name, email=f"demo{k}@example.org",
                phone=phone if scenario != "unknown_caller" else None)
    d.add_user(user)
    # a second, older booking on another machine so "which machine?" is a real question in door_locked
    other = iot.get_machine(f"WM-{9000 + (n % 6) + 1:04d}")
    if scenario in ("door_locked", "relay_fault"):
        pay.plant_paid_session(user.user_id, other.machine_id, other.price_cents, minutes_ago=300, state=CycleState.FINISHED, started=True)
        ses, _ = pay.plant_paid_session(user.user_id, machine.machine_id, machine.price_cents, minutes_ago=4)
        iot.plant_not_started(machine.machine_id, ses.session_id, relay_fault=(scenario == "relay_fault"))
    elif scenario == "aborted":
        ses, _ = pay.plant_paid_session(user.user_id, machine.machine_id, machine.price_cents, minutes_ago=40, state=CycleState.RUNNING, started=True)
        iot.plant_aborted(machine.machine_id, ses.session_id, minutes_in=15)
    elif scenario == "double_charge":
        _, p = pay.plant_paid_session(user.user_id, machine.machine_id, machine.price_cents, minutes_ago=90, state=CycleState.FINISHED, started=True)
        pay.plant_duplicate(p, seconds_later=40)
    elif scenario == "refund_status":
        from laundryops.domain.models import Refund, RefundStatus
        from datetime import timedelta
        _, p = pay.plant_paid_session(user.user_id, machine.machine_id, machine.price_cents, minutes_ago=60 * 24 * 6, state=CycleState.ABORTED, started=True)
        pay.add_refund(Refund(refund_id=f"re_demo{k}", payment_id=p.payment_id, amount_cents=machine.price_cents, status=RefundStatus.PENDING,
                              created_at=now - timedelta(days=5), reason="prior", idempotency_key=f"demo:{p.payment_id}"))
    elif scenario in ("damage", "unknown_caller"):
        pay.plant_paid_session(user.user_id, machine.machine_id, machine.price_cents, minutes_ago=120, state=CycleState.FINISHED, started=True)
    say = _SAY[scenario].get(lang.value, _SAY[scenario]["en"]).format(n=n)
    return {"scenario": scenario, "description": SCENARIOS[scenario], "caller_name": name, "language": lang.value,
            "from_number": phone if scenario != "unknown_caller" else "+4400000000",
            "machine": machine.model_dump(mode="json"), "what_to_say": say}
