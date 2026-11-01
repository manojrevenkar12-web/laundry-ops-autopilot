"""Deterministic synthetic world + labelled ticket scenarios.

``build_world`` populates a Platform's directory, IoT mock and payment ledger with background
traffic, then returns labelled scenarios. Each scenario carries a ``plant`` callable that the
runner executes immediately before handling the ticket, so the telemetry and ledger picture the
agent sees is exactly the one the label was written for — like a live stream, not a frozen
snapshot. Same seed, same world, same tickets: eval numbers are reproducible line by line.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from laundryops.domain.models import (
    ActionType,
    Channel,
    CycleState,
    Intent,
    Language,
    Machine,
    MachineKind,
    Outcome,
    Partner,
    Refund,
    RefundStatus,
    Site,
    TelemetryEvent,
    TicketIn,
    User,
)
from laundryops.synth.phrasebook import FIRST_NAMES, LAST_NAMES, PARTNERS, PHRASES, SITES

# kind, weight, expected intent, expected outcome, expected action types, refund?
MIX: list[tuple[str, int, Intent | None, Outcome, set[ActionType], bool]] = [
    ("not_started_relay_ok",   12, Intent.MACHINE_NOT_STARTED, Outcome.AUTO_RESOLVED, {ActionType.RETRIGGER_RELAY}, False),
    ("not_started_relay_fault", 6, Intent.MACHINE_NOT_STARTED, Outcome.AUTO_RESOLVED, {ActionType.RETRIGGER_RELAY, ActionType.REFUND, ActionType.OPEN_FAULT_TICKET}, True),
    ("not_started_offline",     4, Intent.MACHINE_NOT_STARTED, Outcome.AUTO_RESOLVED, {ActionType.REFUND, ActionType.OPEN_FAULT_TICKET}, True),
    ("aborted",                10, Intent.CYCLE_ABORTED,       Outcome.AUTO_RESOLVED, {ActionType.REFUND, ActionType.OPEN_FAULT_TICKET}, True),
    ("double_charge",           8, Intent.DOUBLE_CHARGE,       Outcome.AUTO_RESOLVED, {ActionType.REFUND}, True),
    ("double_claim_single",     3, Intent.DOUBLE_CHARGE,       Outcome.AUTO_RESOLVED, {ActionType.SEND_INFO}, False),
    ("charged_no_session",      6, Intent.CHARGED_NO_SESSION,  Outcome.AUTO_RESOLVED, {ActionType.REFUND}, True),
    ("refund_status_found",     5, Intent.REFUND_STATUS,       Outcome.AUTO_RESOLVED, {ActionType.SEND_INFO}, False),
    ("refund_status_none",      3, Intent.REFUND_STATUS,       Outcome.AUTO_RESOLVED, {ActionType.SEND_INFO}, False),
    ("reservation_conflict",    5, Intent.RESERVATION_CONFLICT, Outcome.AUTO_RESOLVED, {ActionType.GOODWILL_CREDIT}, False),
    ("app_payment",             6, Intent.APP_OR_PAYMENT_METHOD, Outcome.AUTO_RESOLVED, {ActionType.SEND_INFO}, False),
    ("general",                 6, Intent.GENERAL_QUESTION,    Outcome.AUTO_RESOLVED, {ActionType.SEND_INFO}, False),
    ("machine_report",          7, Intent.MACHINE_REPORT,      Outcome.AUTO_RESOLVED, {ActionType.OPEN_FAULT_TICKET, ActionType.SEND_INFO}, False),
    ("damage",                  5, Intent.DAMAGE_CLAIM,        Outcome.ESCALATED,     {ActionType.ESCALATE_HUMAN}, False),
    ("legal",                   2, Intent.ESCALATION_REQUIRED, Outcome.ESCALATED,     {ActionType.ESCALATE_HUMAN}, False),
    ("partner",                 4, Intent.PARTNER_PAYOUT,      Outcome.AUTO_RESOLVED, {ActionType.PARTNER_STATEMENT}, False),
    ("unverifiable",            3, Intent.CHARGED_NO_SESSION,  Outcome.ESCALATED,     {ActionType.ESCALATE_HUMAN}, False),
    ("repeat_refunder_cap",     2, Intent.CYCLE_ABORTED,       Outcome.ESCALATED,     {ActionType.ESCALATE_HUMAN}, False),
    ("injection",               1, None,                       Outcome.ESCALATED,     {ActionType.ESCALATE_HUMAN}, False),
    ("running_normal",          2, Intent.MACHINE_NOT_STARTED, Outcome.AUTO_RESOLVED, {ActionType.SEND_INFO}, False),
]
PHRASE_KEY = {"not_started_relay_ok": "not_started", "not_started_relay_fault": "not_started",
              "not_started_offline": "not_started", "double_claim_single": "double_charge",
              "refund_status_found": "refund_status", "refund_status_none": "refund_status",
              "repeat_refunder_cap": "aborted"}
LANG_WEIGHTS = [(Language.DE, 45), (Language.EN, 30), (Language.TR, 10), (Language.FR, 8), (Language.ES, 7)]
NEEDS_CLEAN_MACHINE = {"not_started_relay_ok", "running_normal", "reservation_conflict"}
TAINTS_MACHINE = {"not_started_relay_fault", "not_started_offline", "aborted", "repeat_refunder_cap"}


def _pick(rng: random.Random, weighted: list[tuple[Any, int]]):
    total = sum(w for _, w in weighted)
    x = rng.uniform(0, total)
    for item, w in weighted:
        x -= w
        if x <= 0:
            return item
    return weighted[-1][0]


def build_world(platform, *, seed: int = 42, n_tickets: int = 500, now: datetime | None = None,
                n_sites: int = 24) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    now = now or datetime.now(timezone.utc)
    d, iot, pay, store = platform.directory, platform.iot, platform.payments, platform.store

    # ---- partners, sites, machines ----------------------------------------------------------
    partners: list[Partner] = []
    for i, (name, iban, bic) in enumerate(PARTNERS):
        p = Partner(partner_id=f"P{i+1}", name=name, iban=iban, bic=bic, share_bps=7500 if i else 7000,
                    contact_email=f"partner{i+1}@example.org")
        d.add_partner(p)
        partners.append(p)
    sites: list[Site] = []
    for i in range(n_sites):
        name, city = SITES[i % len(SITES)]
        if i >= len(SITES):
            name = f"{name} {i // len(SITES) + 1}"
        s = Site(site_id=f"S{i+1:02d}", partner_id=partners[i % len(partners)].partner_id, name=name, city=city,
                 building_manager_email=f"hausmeister{i+1}@example.org")
        d.add_site(s)
        sites.append(s)
        for k in range(rng.randint(4, 6)):
            iot.add_machine(Machine(machine_id=f"WM-{i*10 + k + 1:04d}", site_id=s.site_id, kind=MachineKind.WASHER,
                                    label=f"Waschmaschine {k+1}", price_cents=rng.choice([300, 350, 400])))
        for k in range(rng.randint(1, 3)):
            iot.add_machine(Machine(machine_id=f"TD-{i*10 + k + 1:04d}", site_id=s.site_id, kind=MachineKind.DRYER,
                                    label=f"Trockner {k+1}", price_cents=rng.choice([150, 200])))

    # ---- background traffic (last 30 days, finished cycles) for settlement + health baselines ----
    for i in range(300):
        s = rng.choice(sites)
        u = User(user_id=f"bg_{i:04d}", site_id=s.site_id, language=_pick(rng, LANG_WEIGHTS),
                 display_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}", email=f"bg{i}@example.org")
        d.add_user(u)
        for _ in range(rng.randint(2, 8)):
            m = rng.choice(iot.machines_for_site(s.site_id))
            mins = rng.randint(60 * 24, 60 * 24 * 29)
            pay.plant_paid_session(u.user_id, m.machine_id, m.price_cents, minutes_ago=mins, state=CycleState.FINISHED, started=True)
            at = now - timedelta(minutes=mins)
            iot.add_event(TelemetryEvent(machine_id=m.machine_id, at=at, kind="cycle_start"))
            iot.add_event(TelemetryEvent(machine_id=m.machine_id, at=at + timedelta(minutes=45), kind="cycle_end"))

    # Partner statements for last month so partner tickets have something to serve.
    start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    platform.settlement.run_month(start, now - timedelta(hours=1))

    # ---- scenarios ------------------------------------------------------------------------------
    meta = {k: (intent, outcome, acts, refund) for (k, _w, intent, outcome, acts, refund) in MIX}
    kinds = [(k, w) for (k, w, *_r) in MIX]
    washers = [m for m in iot.list_machines() if m.kind == MachineKind.WASHER]
    tainted: set[str] = set()
    scenarios: list[dict[str, Any]] = []

    for i in range(n_tickets):
        kind = _pick(rng, kinds)
        intent, exp_outcome, exp_actions, wants_refund = meta[kind]
        lang = _pick(rng, LANG_WEIGHTS)
        pool = [m for m in washers if m.machine_id not in tainted] if kind in NEEDS_CLEAN_MACHINE else washers
        machine = rng.choice(pool or washers)
        site = d.get_site(machine.site_id)
        user = User(user_id=f"u_{i:04d}", site_id=site.site_id, language=lang,
                    display_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}", email=f"u{i}@example.org")
        d.add_user(user)
        price = machine.price_cents
        phrases = PHRASES[PHRASE_KEY.get(kind, kind)]
        text = rng.choice(phrases.get(lang, phrases[Language.EN])).format(
            m=machine.label, id=machine.machine_id, amt=f"{price/100:.2f}".replace(".", ","))
        ticket_kwargs: dict[str, Any] = {"user_id": user.user_id, "channel": Channel.CHAT}
        exp_refund = price if wants_refund else 0
        uid, mid = user.user_id, machine.machine_id
        r_minutes = rng.randint(2, 12)
        r_minutes_long = rng.randint(30, 50)
        r_minutes_hist = rng.randint(20, 300)
        r_secs = rng.randint(10, 120)
        r_in = rng.randint(5, 25)
        r_status = rng.choice([RefundStatus.PENDING, RefundStatus.SETTLED])
        other = f"ses_other_{i}"

        def plant(kind=kind, uid=uid, mid=mid, price=price, i=i) -> None:
            if kind == "not_started_relay_ok":
                ses, _ = pay.plant_paid_session(uid, mid, price, minutes_ago=r_minutes)
                iot.plant_not_started(mid, ses.session_id)
            elif kind == "not_started_relay_fault":
                ses, _ = pay.plant_paid_session(uid, mid, price, minutes_ago=r_minutes)
                iot.plant_not_started(mid, ses.session_id, relay_fault=True)
            elif kind == "not_started_offline":
                ses, _ = pay.plant_paid_session(uid, mid, price, minutes_ago=r_minutes)
                iot.plant_not_started(mid, ses.session_id, offline=True)
            elif kind in ("aborted", "repeat_refunder_cap"):
                ses, _ = pay.plant_paid_session(uid, mid, price, minutes_ago=r_minutes_long, state=CycleState.RUNNING, started=True)
                iot.plant_aborted(mid, ses.session_id, minutes_in=r_in)
                if kind == "repeat_refunder_cap":
                    for k in range(3):
                        store.plant_action(uid, ActionType.REFUND, price, now - timedelta(days=3 + k * 4))
            elif kind == "double_charge":
                _, p = pay.plant_paid_session(uid, mid, price, minutes_ago=r_minutes_hist, state=CycleState.FINISHED, started=True)
                pay.plant_duplicate(p, seconds_later=r_secs)
            elif kind in ("double_claim_single", "refund_status_none"):
                pay.plant_paid_session(uid, mid, price, minutes_ago=r_minutes_hist, state=CycleState.FINISHED, started=True)
            elif kind == "charged_no_session":
                pay.plant_orphan_payment(uid, price, minutes_ago=r_minutes + 2)
            elif kind == "refund_status_found":
                _, p = pay.plant_paid_session(uid, mid, price, minutes_ago=60 * 24 * 6, state=CycleState.ABORTED, started=True)
                pay.add_refund(Refund(refund_id=f"re_{uuid.uuid4().hex[:8]}", payment_id=p.payment_id, amount_cents=price,
                                      status=r_status, created_at=now - timedelta(days=5), reason="prior",
                                      idempotency_key=f"prior:{p.payment_id}"))
            elif kind == "reservation_conflict":
                pay.plant_paid_session(uid, mid, price, minutes_ago=r_minutes)
                iot.plant_running_other_user(mid, other)
            elif kind == "running_normal":
                ses, _ = pay.plant_paid_session(uid, mid, price, minutes_ago=r_minutes, state=CycleState.RUNNING, started=True)
                iot.plant_running_other_user(mid, ses.session_id)   # running under the resident's own session
            # damage / legal / unverifiable / injection / app / general / machine_report / partner: no evidence by design

        if kind == "partner":
            partner = partners[rng.randrange(len(partners))]
            ticket_kwargs = {"partner_id": partner.partner_id, "channel": Channel.PARTNER_PORTAL}
        if kind in TAINTS_MACHINE:
            tainted.add(mid)

        scenarios.append({
            "ticket": TicketIn(ticket_id=f"ev_{i:04d}", text=text, received_at=now, **ticket_kwargs),
            "plant": plant, "kind": kind, "language": lang.value,
            "expected_intent": intent.value if intent else None, "expected_outcome": exp_outcome.value,
            "expected_actions": sorted(a.value for a in exp_actions), "expected_refund_cents": exp_refund,
            "machine_id": None if kind == "partner" else mid,
        })
    return scenarios


__all__ = ["build_world", "MIX"]
