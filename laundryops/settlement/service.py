"""Partner settlement: statements, payouts, SEPA files, reconciliation and the status lookup
the support agent uses to answer "where is our share?" without a human.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

from laundryops.adapters import Directory, PaymentPort
from laundryops.config import Settings
from laundryops.domain.models import PayoutInstruction, Statement, StatementLine, utcnow
from laundryops.settlement.sepa import build_pain001, end_to_end_id
from laundryops.store.db import Store


class SettlementService:
    def __init__(self, *, directory: Directory, payments: PaymentPort, store: Store, settings: Settings, iot=None):
        self.directory, self.payments, self.store, self.s, self.iot = directory, payments, store, settings, iot

    # ---- statements -----------------------------------------------------------------------
    def compute_statement(self, partner_id: str, period_start: datetime, period_end: datetime,
                          machine_site: dict[str, str] | None = None) -> Statement:
        partner = self.directory.get_partner(partner_id)
        if partner is None:
            raise KeyError(partner_id)
        sites = {s.site_id for s in self.directory.sites_for_partner(partner_id)}
        machine_site = machine_site or self._machine_site_map()
        agg: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"cycles": 0, "gross": 0, "refunded": 0})
        for p in self.payments.payments_between(period_start, period_end):
            if not p.session_id:
                continue
            sess = self.payments.get_session(p.session_id)
            if not sess:
                continue
            site = machine_site.get(sess.machine_id)
            if site not in sites:
                continue
            a = agg[(site, sess.machine_id)]
            a["cycles"] += 1
            a["gross"] += p.amount_cents
            a["refunded"] += p.refunded_cents
        lines = [StatementLine(site_id=k[0], machine_id=k[1], cycles=v["cycles"], gross_cents=v["gross"],
                               refunded_cents=v["refunded"], net_cents=v["gross"] - v["refunded"])
                 for k, v in sorted(agg.items())]
        gross = sum(l.gross_cents for l in lines)
        refunded = sum(l.refunded_cents for l in lines)
        net = gross - refunded
        share = net * partner.share_bps // 10_000
        st = Statement(
            statement_id=f"stm_{partner_id}_{period_start:%Y%m}", partner_id=partner_id, period_start=period_start,
            period_end=period_end, lines=lines, gross_cents=gross, refunded_cents=refunded, net_cents=net,
            platform_fee_cents=net - share, partner_share_cents=share, generated_at=utcnow(),
        )
        self.store.save_statement(st)
        self.store.audit("settlement", "statement_generated", None, statement_id=st.statement_id, partner_id=partner_id,
                         net_cents=net, partner_share_cents=share)
        return st

    def _machine_site_map(self) -> dict[str, str]:
        if self.iot is None:
            return {}
        return {m.machine_id: m.site_id for m in self.iot.list_machines()}

    # ---- payouts ----------------------------------------------------------------------------
    def create_payout(self, st: Statement) -> PayoutInstruction | None:
        if st.partner_share_cents < self.s.payout_min_cents:
            self.store.audit("settlement", "payout_skipped_below_minimum", None, statement_id=st.statement_id)
            return None
        period = f"{st.period_start:%Y%m}"
        e2e = end_to_end_id(st.partner_id, period)
        if self.store.payout_by_e2e(e2e):
            return self.store.payout_by_e2e(e2e)     # idempotent per partner+period
        p = PayoutInstruction(payout_id=f"po_{uuid.uuid4().hex[:10]}", partner_id=st.partner_id, statement_id=st.statement_id,
                              amount_cents=st.partner_share_cents, end_to_end_id=e2e)
        self.store.save_payout(p)
        self.store.audit("settlement", "payout_created", None, payout_id=p.payout_id, end_to_end_id=e2e, amount_cents=p.amount_cents)
        return p

    def build_sepa_file(self, payouts: list[PayoutInstruction], execution_date: date | None = None) -> str:
        xml = build_pain001(payouts, self.directory.partners, debtor_name=self.s.debtor_name, debtor_iban=self.s.debtor_iban,
                            debtor_bic=self.s.debtor_bic, execution_date=execution_date or (utcnow() + timedelta(days=1)).date())
        now = utcnow()
        for p in payouts:
            p.status, p.sent_at = "sent", now
            self.store.save_payout(p)
        self.store.audit("settlement", "sepa_file_built", None, payouts=[p.payout_id for p in payouts],
                         total_cents=sum(p.amount_cents for p in payouts))
        return xml

    # ---- reconciliation -------------------------------------------------------------------
    def reconcile(self, bank_records: list[dict]) -> dict[str, list[str]]:
        """Apply a bank status feed (pain.002 / camt.054 style): [{end_to_end_id, status: ACSC|RJCT|RTRN, reason}]."""
        settled, returned, unknown = [], [], []
        now = utcnow()
        for rec in bank_records:
            p = self.store.payout_by_e2e(rec.get("end_to_end_id", ""))
            if not p:
                unknown.append(rec.get("end_to_end_id", "?"))
                continue
            if rec.get("status") == "ACSC":
                p.status, p.settled_at = "settled", now
                settled.append(p.payout_id)
            else:
                p.status, p.return_reason = "returned", rec.get("reason", "RJCT")
                returned.append(p.payout_id)
                partner = self.directory.get_partner(p.partner_id)
                self.store.audit("settlement", "payout_returned", None, payout_id=p.payout_id, reason=p.return_reason,
                                 action=f"ops: verify IBAN for {partner.name if partner else p.partner_id}")
            self.store.save_payout(p)
        self.store.audit("settlement", "reconciled", None, settled=settled, returned=returned, unknown=unknown)
        return {"settled": settled, "returned": returned, "unknown": unknown}

    # ---- lookups used by the support agent -----------------------------------------------------
    def partner_status(self, partner_id: str) -> dict:
        stmts = self.store.statements_for_partner(partner_id)
        payouts = self.store.payouts_for_partner(partner_id)
        if not stmts:
            return {"period": "-", "net_cents": 0, "partner_share_cents": 0, "payout_status": "no statement yet", "end_to_end_id": "-"}
        st = stmts[0]
        po = next((p for p in payouts if p.statement_id == st.statement_id), None)
        status = {"created": "scheduled", "sent": "sent to bank", "settled": "paid", "returned": "returned by bank – IBAN check"}
        return {
            "period": f"{st.period_start:%b %Y}", "net_cents": st.net_cents, "partner_share_cents": st.partner_share_cents,
            "gross_cents": st.gross_cents, "refunded_cents": st.refunded_cents, "statement_id": st.statement_id,
            "payout_status": status.get(po.status, po.status) if po else ("pending – below minimum" if st.partner_share_cents < self.s.payout_min_cents else "pending"),
            "end_to_end_id": po.end_to_end_id if po else "-",
            "sent_at": po.sent_at.isoformat() if po and po.sent_at else None,
        }

    def run_month(self, period_start: datetime, period_end: datetime) -> dict:
        """Statements + payouts + SEPA file for every partner in one go."""
        stmts = [self.compute_statement(pid, period_start, period_end) for pid in self.directory.partners]
        payouts = [p for p in (self.create_payout(s) for s in stmts) if p]
        xml = self.build_sepa_file(payouts) if payouts else ""
        return {"statements": [s.statement_id for s in stmts], "payouts": [p.payout_id for p in payouts],
                "total_cents": sum(p.amount_cents for p in payouts), "pain001": xml}
