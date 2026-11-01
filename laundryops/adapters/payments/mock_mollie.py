"""In-memory Mollie-style ledger: payments, refunds, credits and the booking sessions they pay for.

Behaves like a PSP where it matters for safety:
* a refund can never exceed captured minus already-refunded,
* refunds are idempotent on the key the caller supplies,
* every refund is a separate object with its own status.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from laundryops.domain.models import (
    Credit,
    CycleState,
    Payment,
    PaymentStatus,
    Refund,
    RefundStatus,
    Session,
    utcnow,
)


class RefundError(Exception):
    pass


class MockMollie:
    def __init__(self, clock=utcnow):
        self._clock = clock
        self._payments: dict[str, Payment] = {}
        self._refunds: dict[str, Refund] = {}
        self._by_key: dict[str, str] = {}
        self._credits: list[Credit] = []
        self._sessions: dict[str, Session] = {}
        self.refund_calls: list[tuple[str, int]] = []

    # ---- seeding -------------------------------------------------------------------
    def add_session(self, s: Session) -> None:
        self._sessions[s.session_id] = s

    def add_payment(self, p: Payment) -> None:
        self._payments[p.payment_id] = p

    def add_refund(self, r: Refund) -> None:
        self._refunds[r.refund_id] = r
        self._by_key[r.idempotency_key] = r.refund_id
        p = self._payments.get(r.payment_id)
        if p:
            p.refunded_cents += r.amount_cents
            p.status = PaymentStatus.REFUNDED if p.refunded_cents >= p.amount_cents else PaymentStatus.PARTIALLY_REFUNDED

    # ---- reads ---------------------------------------------------------------------
    def get_payment(self, payment_id: str) -> Payment | None:
        return self._payments.get(payment_id)

    def payments_for_user(self, user_id: str, since: datetime) -> list[Payment]:
        return sorted(
            (p for p in self._payments.values() if p.user_id == user_id and p.captured_at >= since),
            key=lambda p: p.captured_at,
        )

    def payments_for_session(self, session_id: str) -> list[Payment]:
        return sorted((p for p in self._payments.values() if p.session_id == session_id), key=lambda p: p.captured_at)

    def payments_between(self, start: datetime, end: datetime) -> list[Payment]:
        return [p for p in self._payments.values() if start <= p.captured_at < end]

    def refunds_for_payment(self, payment_id: str) -> list[Refund]:
        return [r for r in self._refunds.values() if r.payment_id == payment_id]

    def refunds_for_user(self, user_id: str, since: datetime) -> list[Refund]:
        pids = {p.payment_id for p in self._payments.values() if p.user_id == user_id}
        return [r for r in self._refunds.values() if r.payment_id in pids and r.created_at >= since]

    def sessions_for_user(self, user_id: str, since: datetime) -> list[Session]:
        return sorted(
            (s for s in self._sessions.values() if s.user_id == user_id and s.reserved_at >= since),
            key=lambda s: s.reserved_at,
        )

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def credits_for_user(self, user_id: str) -> list[Credit]:
        return [c for c in self._credits if c.user_id == user_id]

    # ---- writes --------------------------------------------------------------------
    def create_refund(self, payment_id: str, amount_cents: int, idempotency_key: str, reason: str) -> Refund:
        self.refund_calls.append((payment_id, amount_cents))
        if idempotency_key in self._by_key:
            return self._refunds[self._by_key[idempotency_key]]
        p = self._payments.get(payment_id)
        if p is None:
            raise RefundError(f"payment {payment_id} not found")
        if p.status not in (PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED):
            raise RefundError(f"payment {payment_id} is {p.status}, not refundable")
        if amount_cents <= 0 or amount_cents > p.amount_cents - p.refunded_cents:
            raise RefundError("amount exceeds refundable balance")
        r = Refund(
            refund_id=f"re_{uuid.uuid4().hex[:10]}", payment_id=payment_id, amount_cents=amount_cents,
            status=RefundStatus.PENDING, created_at=self._clock(), reason=reason, idempotency_key=idempotency_key,
        )
        self.add_refund(r)
        return r

    def issue_credit(self, user_id: str, amount_cents: int, reason: str) -> Credit:
        c = Credit(credit_id=f"cr_{uuid.uuid4().hex[:10]}", user_id=user_id, amount_cents=amount_cents,
                   created_at=self._clock(), reason=reason)
        self._credits.append(c)
        return c

    # ---- scenario helpers ---------------------------------------------------------------
    def plant_paid_session(self, user_id: str, machine_id: str, price_cents: int, minutes_ago: int = 5,
                           state: CycleState = CycleState.RESERVED, started: bool = False,
                           session_id: str | None = None, payment_id: str | None = None) -> tuple[Session, Payment]:
        now = self._clock()
        sid = session_id or f"ses_{uuid.uuid4().hex[:8]}"
        pid = payment_id or f"tr_{uuid.uuid4().hex[:8]}"
        s = Session(session_id=sid, user_id=user_id, machine_id=machine_id,
                    reserved_at=now - timedelta(minutes=minutes_ago),
                    started_at=(now - timedelta(minutes=minutes_ago - 1)) if started else None,
                    state=state, price_cents=price_cents)
        p = Payment(payment_id=pid, user_id=user_id, session_id=sid, amount_cents=price_cents,
                    status=PaymentStatus.CAPTURED, captured_at=now - timedelta(minutes=minutes_ago))
        self.add_session(s)
        self.add_payment(p)
        return s, p

    def plant_duplicate(self, payment: Payment, seconds_later: int = 40) -> Payment:
        dup = payment.model_copy(update={
            "payment_id": f"tr_{uuid.uuid4().hex[:8]}",
            "captured_at": payment.captured_at + timedelta(seconds=seconds_later),
        })
        self.add_payment(dup)
        return dup

    def plant_orphan_payment(self, user_id: str, amount_cents: int, minutes_ago: int = 8) -> Payment:
        p = Payment(payment_id=f"tr_{uuid.uuid4().hex[:8]}", user_id=user_id, session_id=None,
                    amount_cents=amount_cents, status=PaymentStatus.CAPTURED,
                    captured_at=self._clock() - timedelta(minutes=minutes_ago))
        self.add_payment(p)
        return p
