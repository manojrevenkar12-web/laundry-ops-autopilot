"""SQLite system of record for everything the agent does.

Tickets, decisions, actions, faults, payouts and an append-only audit log. The mock IoT
and payment adapters keep their own state (they stand in for WeWash's systems); this store
is what the operator owns and what an auditor reads.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from laundryops.domain.models import (
    ActionResult,
    FaultSeverity,
    FaultStatus,
    FaultTicket,
    PayoutInstruction,
    Statement,
    TicketIn,
    TicketOutcome,
    utcnow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  ticket_id TEXT PRIMARY KEY, user_id TEXT, partner_id TEXT, channel TEXT, received_at TEXT,
  text TEXT, outcome TEXT, intent TEXT, language TEXT, handled_in_ms INTEGER, payload TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, type TEXT, success INTEGER,
  amount_cents INTEGER, payment_id TEXT, machine_id TEXT, user_id TEXT, at TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_user ON actions(user_id, type, at);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, actor TEXT, event TEXT, ticket_id TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS faults (
  fault_id TEXT PRIMARY KEY, machine_id TEXT, site_id TEXT, severity TEXT, status TEXT,
  opened_at TEXT, ack_due_at TEXT, fix_due_at TEXT, acknowledged_at TEXT, fixed_at TEXT,
  source TEXT, signals TEXT
);
CREATE TABLE IF NOT EXISTS statements (
  statement_id TEXT PRIMARY KEY, partner_id TEXT, period_start TEXT, period_end TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS calls (
  call_id TEXT PRIMARY KEY, from_number TEXT, user_id TEXT, partner_id TEXT, language TEXT, started_at TEXT,
  ended_at TEXT, turns INTEGER, outcome TEXT, handoff_reason TEXT, tickets TEXT, transcript TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
  reference TEXT PRIMARY KEY, user_id TEXT, partner_id TEXT, channel TEXT, reason TEXT, tickets TEXT,
  status TEXT, created_at TEXT, updated_at TEXT, note TEXT, call_id TEXT
);
CREATE TABLE IF NOT EXISTS payouts (
  payout_id TEXT PRIMARY KEY, partner_id TEXT, statement_id TEXT, amount_cents INTEGER,
  end_to_end_id TEXT, status TEXT, sent_at TEXT, settled_at TEXT, return_reason TEXT
);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    # ---- audit -----------------------------------------------------------------
    def audit(self, actor: str, event: str, ticket_id: str | None = None, **payload: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit(at, actor, event, ticket_id, payload) VALUES (?,?,?,?,?)",
                (utcnow().isoformat(), actor, event, ticket_id, json.dumps(payload, default=str)),
            )
            self._conn.commit()

    def audit_trail(self, ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT at, actor, event, payload FROM audit WHERE ticket_id=? ORDER BY id", (ticket_id,)
            ).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]

    # ---- tickets ---------------------------------------------------------------
    def save_ticket(self, ticket: TicketIn, outcome: TicketOutcome) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ticket.ticket_id, ticket.user_id, ticket.partner_id, ticket.channel.value,
                    ticket.received_at.isoformat(), ticket.text, outcome.outcome.value,
                    outcome.triage.intent.value, outcome.triage.language.value, outcome.handled_in_ms,
                    outcome.model_dump_json(),
                ),
            )
            for a in outcome.actions:
                self._conn.execute(
                    "INSERT INTO actions(ticket_id,type,success,amount_cents,payment_id,machine_id,user_id,at,detail)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        ticket.ticket_id, a.type.value, int(a.success), a.detail.get("amount_cents", 0),
                        a.detail.get("payment_id"), a.detail.get("machine_id"), ticket.user_id,
                        a.at.isoformat(), json.dumps(a.detail, default=str),
                    ),
                )
            self._conn.commit()

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["outcome_detail"] = json.loads(d.pop("payload"))
        return d

    def list_tickets(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ticket_id,user_id,partner_id,channel,received_at,outcome,intent,language,handled_in_ms,text"
                " FROM tickets ORDER BY received_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def tickets_for_machine_7d(self, machine_id: str, now: datetime | None = None) -> int:
        now = now or utcnow()
        since = (now - timedelta(days=7)).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(DISTINCT ticket_id) AS n FROM actions WHERE machine_id=? AND at>=?",
                (machine_id, since),
            ).fetchone()
        return int(row["n"])

    def recent_retrigger(self, user_id: str, machine_id: str, now: datetime | None = None, minutes: int = 15) -> bool:
        """Did we actuate this machine's relay for this user in the last N minutes (any result)?"""
        now = now or utcnow()
        since = (now - timedelta(minutes=minutes)).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM actions WHERE user_id=? AND machine_id=? AND type='retrigger_relay' AND at>=?",
                (user_id, machine_id, since),
            ).fetchone()
        return int(row["n"]) > 0

    # ---- human review queue (resident not satisfied / asked for a person) -------------
    def create_review(self, *, user_id: str | None, partner_id: str | None, channel: str, reason: str,
                      tickets: list[str], call_id: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) AS n FROM reviews WHERE reference LIKE ?", (f"WR-{now:%y%m%d}-%",)).fetchone()["n"]
            ref = f"WR-{now:%y%m%d}-{n + 1:03d}"
            self._conn.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ref, user_id, partner_id, channel, reason, json.dumps(tickets), "open", now.isoformat(), now.isoformat(), None, call_id),
            )
            self._conn.commit()
        return self.get_review(ref)

    def get_review(self, reference: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM reviews WHERE reference=?", (reference.upper().strip(),)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tickets"] = json.loads(d["tickets"] or "[]")
        return d

    def reviews_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT reference FROM reviews WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
        return [self.get_review(r["reference"]) for r in rows]

    def list_reviews(self, status: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT reference FROM reviews" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(q, (status,) if status else ()).fetchall()
        return [self.get_review(r["reference"]) for r in rows]

    def update_review(self, reference: str, status: str, note: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            self._conn.execute("UPDATE reviews SET status=?, note=COALESCE(?, note), updated_at=? WHERE reference=?",
                               (status, note, utcnow().isoformat(), reference.upper().strip()))
            self._conn.commit()
        return self.get_review(reference)

    # ---- calls -------------------------------------------------------------------
    def save_call(self, c: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (c["call_id"], c.get("from_number"), c.get("user_id"), c.get("partner_id"), c.get("language"),
                 c.get("started_at"), c.get("ended_at"), c.get("turns", 0), c.get("outcome"), c.get("handoff_reason"),
                 json.dumps(c.get("tickets", [])), json.dumps(c.get("transcript", []), ensure_ascii=False)),
            )
            self._conn.commit()

    def list_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tickets"] = json.loads(d["tickets"] or "[]")
            d["transcript"] = json.loads(d["transcript"] or "[]")
            out.append(d)
        return out

    def call_metrics(self) -> dict[str, Any]:
        with self._lock:
            tot = self._conn.execute("SELECT COUNT(*) AS n, AVG(turns) AS t FROM calls WHERE ended_at IS NOT NULL").fetchone()
            contained = self._conn.execute("SELECT COUNT(*) AS n FROM calls WHERE outcome='contained'").fetchone()["n"]
            reasons = self._conn.execute(
                "SELECT handoff_reason, COUNT(*) AS n FROM calls WHERE outcome='handoff' GROUP BY handoff_reason").fetchall()
        n = int(tot["n"] or 0)
        return {"calls": n, "contained": contained, "containment_rate": round(contained / n, 4) if n else 0.0,
                "avg_turns": round(float(tot["t"] or 0), 2), "handoff_reasons": {r["handoff_reason"]: r["n"] for r in reasons}}

    # ---- money counters used by guardrails ---------------------------------------
    def user_auto_money_30d(self, user_id: str, now: datetime | None = None) -> tuple[int, int, int]:
        """Returns (refund_count, refund_total_cents, goodwill_credit_count) in the last 30 days."""
        now = now or utcnow()
        since = (now - timedelta(days=30)).isoformat()
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(amount_cents),0) AS total FROM actions"
                " WHERE user_id=? AND type='refund' AND success=1 AND at>=?", (user_id, since)
            ).fetchone()
            c = self._conn.execute(
                "SELECT COUNT(*) AS n FROM actions WHERE user_id=? AND type='goodwill_credit' AND success=1 AND at>=?",
                (user_id, since),
            ).fetchone()
        return int(r["n"]), int(r["total"]), int(c["n"])

    def plant_action(self, user_id: str, type_, amount_cents: int, at: datetime) -> None:
        """Seed a historical successful action (used by tests and the synthetic world for cap scenarios)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO actions(ticket_id,type,success,amount_cents,payment_id,machine_id,user_id,at,detail)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (f"seed_{user_id}_{at.timestamp():.0f}", getattr(type_, "value", str(type_)), 1, amount_cents, None, None,
                 user_id, at.isoformat(), "{}"),
            )
            self._conn.commit()

    # ---- faults ------------------------------------------------------------------
    def upsert_fault(self, f: FaultTicket) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO faults VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f.fault_id, f.machine_id, f.site_id, f.severity.value, f.status.value,
                    _iso(f.opened_at), _iso(f.ack_due_at), _iso(f.fix_due_at), _iso(f.acknowledged_at),
                    _iso(f.fixed_at), f.source, json.dumps(f.signals),
                ),
            )
            self._conn.commit()

    def open_fault_for_machine(self, machine_id: str) -> FaultTicket | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM faults WHERE machine_id=? AND status!='fixed' ORDER BY opened_at DESC LIMIT 1",
                (machine_id,),
            ).fetchone()
        return self._fault_from_row(row) if row else None

    def list_faults(self, status: str | None = None) -> list[FaultTicket]:
        q = "SELECT * FROM faults" + (" WHERE status=?" if status else "") + " ORDER BY opened_at DESC"
        with self._lock:
            rows = self._conn.execute(q, (status,) if status else ()).fetchall()
        return [self._fault_from_row(r) for r in rows]

    @staticmethod
    def _fault_from_row(r: sqlite3.Row) -> FaultTicket:
        return FaultTicket(
            fault_id=r["fault_id"], machine_id=r["machine_id"], site_id=r["site_id"],
            severity=FaultSeverity(r["severity"]), status=FaultStatus(r["status"]),
            opened_at=_parse(r["opened_at"]), ack_due_at=_parse(r["ack_due_at"]), fix_due_at=_parse(r["fix_due_at"]),
            acknowledged_at=_parse(r["acknowledged_at"]), fixed_at=_parse(r["fixed_at"]),
            source=r["source"], signals=json.loads(r["signals"] or "[]"),
        )

    # ---- settlement --------------------------------------------------------------
    def save_statement(self, s: Statement) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO statements VALUES (?,?,?,?,?)",
                (s.statement_id, s.partner_id, _iso(s.period_start), _iso(s.period_end), s.model_dump_json()),
            )
            self._conn.commit()

    def statements_for_partner(self, partner_id: str) -> list[Statement]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM statements WHERE partner_id=? ORDER BY period_start DESC", (partner_id,)
            ).fetchall()
        return [Statement.model_validate_json(r["payload"]) for r in rows]

    def save_payout(self, p: PayoutInstruction) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO payouts VALUES (?,?,?,?,?,?,?,?,?)",
                (p.payout_id, p.partner_id, p.statement_id, p.amount_cents, p.end_to_end_id, p.status,
                 _iso(p.sent_at), _iso(p.settled_at), p.return_reason),
            )
            self._conn.commit()

    def payouts_for_partner(self, partner_id: str) -> list[PayoutInstruction]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM payouts WHERE partner_id=? ORDER BY payout_id DESC", (partner_id,)
            ).fetchall()
        return [self._payout_from_row(r) for r in rows]

    def payout_by_e2e(self, end_to_end_id: str) -> PayoutInstruction | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM payouts WHERE end_to_end_id=?", (end_to_end_id,)).fetchone()
        return self._payout_from_row(row) if row else None

    def list_payouts(self) -> list[PayoutInstruction]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM payouts ORDER BY payout_id").fetchall()
        return [self._payout_from_row(r) for r in rows]

    @staticmethod
    def _payout_from_row(r: sqlite3.Row) -> PayoutInstruction:
        return PayoutInstruction(
            payout_id=r["payout_id"], partner_id=r["partner_id"], statement_id=r["statement_id"],
            amount_cents=r["amount_cents"], end_to_end_id=r["end_to_end_id"], status=r["status"],
            sent_at=_parse(r["sent_at"]), settled_at=_parse(r["settled_at"]), return_reason=r["return_reason"],
        )

    # ---- metrics -----------------------------------------------------------------
    def metrics(self) -> dict[str, Any]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"]
            auto = self._conn.execute("SELECT COUNT(*) AS n FROM tickets WHERE outcome='auto_resolved'").fetchone()["n"]
            refunds = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(amount_cents),0) AS total FROM actions WHERE type='refund' AND success=1"
            ).fetchone()
            relays = self._conn.execute(
                "SELECT SUM(success) AS ok, COUNT(*) AS n FROM actions WHERE type='retrigger_relay'"
            ).fetchone()
            by_intent = self._conn.execute(
                "SELECT intent, COUNT(*) AS n, SUM(outcome='auto_resolved') AS auto FROM tickets GROUP BY intent"
            ).fetchall()
            p50 = self._conn.execute(
                "SELECT handled_in_ms FROM tickets ORDER BY handled_in_ms LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM tickets)"
            ).fetchone()
            open_faults = self._conn.execute("SELECT COUNT(*) AS n FROM faults WHERE status!='fixed'").fetchone()["n"]
            open_reviews = self._conn.execute("SELECT COUNT(*) AS n FROM reviews WHERE status='open'").fetchone()["n"]
        return {
            "tickets": total,
            "auto_resolved": auto,
            "deflection_rate": round(auto / total, 4) if total else 0.0,
            "refunds_issued": refunds["n"],
            "refund_total_cents": refunds["total"],
            "relay_retriggers": relays["n"] or 0,
            "relay_retrigger_success": relays["ok"] or 0,
            "p50_handling_ms": p50["handled_in_ms"] if p50 else 0,
            "open_faults": open_faults,
            "open_reviews": open_reviews,
            "by_intent": [dict(r) for r in by_intent],
        }
