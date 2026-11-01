"""Fleet health & dispatch, settlement & SEPA, API surface, eval gate."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import timedelta

from fastapi.testclient import TestClient

from laundryops.domain.models import CycleState, FaultSeverity, FaultStatus, TelemetryEvent
from laundryops.settlement.sepa import build_pain001, iban_valid


# ---- fleet --------------------------------------------------------------------------------
def test_fleet_assess_dispatch_and_sla(platform, now):
    platform.iot.plant_relay_failures("WM-0001", 3)
    snap = platform.iot.get_snapshot("WM-0002").model_copy(update={"online": False, "observed_at": now - timedelta(hours=6)})
    platform.iot.set_snapshot(snap)
    health = {h.machine_id: h for h in platform.fleet.assess(now)}
    assert health["WM-0001"].severity in (FaultSeverity.HIGH, FaultSeverity.CRITICAL)
    assert health["WM-0002"].severity in (FaultSeverity.HIGH, FaultSeverity.CRITICAL) and health["WM-0002"].offline_minutes == 360
    assert health["WM-0003"].severity == FaultSeverity.LOW
    res = platform.fleet.dispatch(now)
    assert len(res["opened"]) == 2
    assert len(platform.notifier.for_recipient("hm@example.org")) == 2
    assert platform.fleet.dispatch(now)["opened"] == []                      # deduped
    later = platform.fleet.dispatch(now + timedelta(hours=49))
    assert len(later["sla_breaches"]) == 2                                   # ack SLA breached
    f = platform.store.list_faults()[0]
    assert platform.fleet.acknowledge(f.fault_id, now).status == FaultStatus.ACKNOWLEDGED


def test_proactive_sweep_refunds_before_complaint(platform, now):
    ses, p = platform.payments.plant_paid_session("u_de", "WM-0001", 350, minutes_ago=30, state=CycleState.RUNNING, started=True)
    platform.iot.plant_aborted("WM-0001", ses.session_id)
    outs = platform.fleet.proactive_sweep(platform.handle, now)
    assert len(outs) == 1 and outs[0].outcome.value == "auto_resolved"
    assert platform.payments.get_payment(p.payment_id).refunded_cents == 350
    assert platform.notifier.for_recipient("lena@example.org")
    assert platform.fleet.proactive_sweep(platform.handle, now) == []        # idempotent


def test_outage_notification_points_to_working_machine(platform, now):
    platform.payments.plant_paid_session("u_en", "WM-0001", 350, minutes_ago=-30)   # reservation in 30 min
    n = platform.fleet.notify_residents_of_outage("WM-0001", now)
    assert n == 1
    msg = platform.notifier.for_recipient("tom@example.org")[0]
    assert "Waschmaschine 2" in msg["body"] or "Waschmaschine 3" in msg["body"]


# ---- settlement ---------------------------------------------------------------------------
def test_iban_mod97():
    assert iban_valid("DE89 3704 0044 0532 0130 00")
    assert iban_valid("DE75512108001245126199")
    assert not iban_valid("DE89370400440532013001")
    assert not iban_valid("XX00")


def test_statement_payout_sepa_and_reconcile(platform, now):
    for k in range(10):
        _, p = platform.payments.plant_paid_session("u_de", "WM-0001", 350, minutes_ago=60 * 24 * (k + 1),
                                                   state=CycleState.FINISHED, started=True)
        if k == 0:
            platform.payments.create_refund(p.payment_id, 350, "k0", "test")
    start, end = now - timedelta(days=30), now + timedelta(days=1)
    st = platform.settlement.compute_statement("P1", start, end)
    assert st.gross_cents == 3500 and st.refunded_cents == 350 and st.net_cents == 3150
    assert st.partner_share_cents == 2362 and st.platform_fee_cents == 788
    po = platform.settlement.create_payout(st)
    assert po.end_to_end_id == f"LOPS-P1-{start:%Y%m}" and platform.settlement.create_payout(st).payout_id == po.payout_id
    xml = platform.settlement.build_sepa_file([po])
    root = ET.fromstring(xml)
    ns = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"}
    assert root.find(".//p:GrpHdr/p:CtrlSum", ns).text == "23.62"
    assert root.find(".//p:CdtTrfTxInf/p:PmtId/p:EndToEndId", ns).text == po.end_to_end_id
    assert root.find(".//p:CdtrAcct/p:Id/p:IBAN", ns).text == "DE89370400440532013000"
    assert platform.settlement.partner_status("P1")["payout_status"] == "sent to bank"
    rec = platform.settlement.reconcile([{"end_to_end_id": po.end_to_end_id, "status": "RJCT", "reason": "AC01"}])
    assert rec["returned"] == [po.payout_id]
    assert "returned" in platform.settlement.partner_status("P1")["payout_status"]


def test_pain001_rejects_bad_iban(platform):
    from laundryops.domain.models import Partner, PayoutInstruction
    bad = Partner(partner_id="PX", name="Bad", iban="DE00000000000000000000", contact_email="b@x")
    po = PayoutInstruction(payout_id="po_x", partner_id="PX", statement_id="s", amount_cents=100, end_to_end_id="LOPS-PX-1")
    try:
        build_pain001([po], {"PX": bad}, debtor_name="D", debtor_iban="DE89370400440532013000", debtor_bic=None,
                      execution_date=__import__("datetime").date(2026, 11, 2))
        assert False, "should reject"
    except ValueError as e:
        assert "IBAN" in str(e)


# ---- API ------------------------------------------------------------------------------------
def test_api_end_to_end(monkeypatch, settings):
    from laundryops.api import app as api
    from laundryops.store.db import Store
    from laundryops.support.service import Platform
    api.platform = Platform(settings=settings, store=Store(":memory:"))
    c = TestClient(api.app)
    assert c.get("/health").json()["status"] == "ok"
    r = c.post("/demo/seed?n=40&seed=1").json()
    assert r["seeded_tickets"] == 40 and r["metrics"]["tickets"] == 40
    tickets = c.get("/tickets").json()
    detail = c.get(f"/tickets/{tickets[0]['ticket_id']}").json()
    assert "audit" in detail and detail["outcome_detail"]["reply"]
    assert c.get("/fleet/health").status_code == 200
    assert c.get("/console").status_code == 200
    assert c.get("/graph").text.startswith("---") or "graph TD" in c.get("/graph").text
    users = c.get("/demo/users?limit=1").json()
    out = c.post("/tickets", json={"text": "Wie viel kostet ein Waschgang?", "user_id": users[0]["user_id"]}).json()
    assert out["outcome"] == "auto_resolved"
    assert c.get("/settlement/payouts").status_code == 200


# ---- evaluation gate -------------------------------------------------------------------------
def test_eval_gate_passes_on_small_run(settings):
    from laundryops.evaluation import run_eval
    s = run_eval(n=150, seed=3, settings=settings)["summary"]
    assert s["gate"]["passed"], s["gate"]
    assert s["wrong_refund_rate"] == 0.0 and s["unsafe_action_rate"] == 0.0
