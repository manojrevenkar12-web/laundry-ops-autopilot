"""Domain model shared by every module.

Money is always integer cents. Time is always timezone-aware UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------------
class Language(StrEnum):
    DE = "de"
    EN = "en"
    TR = "tr"
    FR = "fr"
    ES = "es"
    UNKNOWN = "unknown"


class Channel(StrEnum):
    CHAT = "chat"
    EMAIL = "email"
    VOICE = "voice"        # voice transcripts arrive as text
    PARTNER_PORTAL = "partner_portal"


class Intent(StrEnum):
    MACHINE_NOT_STARTED = "machine_not_started"     # paid, door stays locked / nothing happens
    CYCLE_ABORTED = "cycle_aborted"                 # machine stopped mid-cycle
    DOUBLE_CHARGE = "double_charge"
    CHARGED_NO_SESSION = "charged_no_session"       # money taken, never got a machine
    REFUND_STATUS = "refund_status"
    RESERVATION_CONFLICT = "reservation_conflict"   # reserved, someone else is using it
    APP_OR_PAYMENT_METHOD = "app_or_payment_method" # login, card, PayPal, top-up questions
    DAMAGE_CLAIM = "damage_claim"                   # clothes damaged
    PARTNER_PAYOUT = "partner_payout"               # building operator asks for their share
    MACHINE_REPORT = "machine_report"               # dirty / broken, no money involved
    GENERAL_QUESTION = "general_question"
    UNVERIFIABLE_COMPLAINT = "unverifiable_complaint"
    ESCALATION_REQUIRED = "escalation_required"     # legal threats, abuse, safety
    UNKNOWN = "unknown"


class ActionType(StrEnum):
    RETRIGGER_RELAY = "retrigger_relay"
    REFUND = "refund"
    GOODWILL_CREDIT = "goodwill_credit"
    OPEN_FAULT_TICKET = "open_fault_ticket"
    NOTIFY_BUILDING_MANAGER = "notify_building_manager"
    SEND_INFO = "send_info"
    PARTNER_STATEMENT = "partner_statement"
    ESCALATE_HUMAN = "escalate_human"


class Outcome(StrEnum):
    AUTO_RESOLVED = "auto_resolved"
    ESCALATED = "escalated"


class MachineKind(StrEnum):
    WASHER = "washer"
    DRYER = "dryer"


class CycleState(StrEnum):
    IDLE = "idle"
    RESERVED = "reserved"
    RUNNING = "running"
    ABORTED = "aborted"
    FINISHED = "finished"
    FAULT = "fault"


class PaymentStatus(StrEnum):
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"


class FaultSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FaultStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    FIXED = "fixed"


# ---------------------------------------------------------------------------------
# Fleet / world entities
# ---------------------------------------------------------------------------------
class Partner(BaseModel):
    partner_id: str
    name: str
    iban: str
    bic: str | None = None
    share_bps: int = 7500          # partner share of net revenue in basis points
    contact_email: str
    language: Language = Language.DE


class Site(BaseModel):
    site_id: str
    partner_id: str
    name: str
    city: str
    building_manager_email: str


class Machine(BaseModel):
    machine_id: str
    site_id: str
    kind: MachineKind
    label: str                      # what residents see, e.g. "Waschmaschine 3"
    price_cents: int


class User(BaseModel):
    user_id: str
    site_id: str
    language: Language
    display_name: str
    email: str


class Session(BaseModel):
    """A booked / paid machine cycle."""
    session_id: str
    user_id: str
    machine_id: str
    reserved_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    state: CycleState = CycleState.RESERVED
    price_cents: int = 0


class Payment(BaseModel):
    payment_id: str
    user_id: str
    session_id: str | None
    amount_cents: int
    status: PaymentStatus
    captured_at: datetime
    method: str = "card"
    refunded_cents: int = 0


class Refund(BaseModel):
    refund_id: str
    payment_id: str
    amount_cents: int
    status: RefundStatus
    created_at: datetime
    reason: str
    idempotency_key: str


class Credit(BaseModel):
    credit_id: str
    user_id: str
    amount_cents: int
    created_at: datetime
    reason: str


# ---------------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------------
class TelemetrySnapshot(BaseModel):
    machine_id: str
    observed_at: datetime
    online: bool
    door_locked: bool
    relay_engaged: bool
    power_draw_w: float
    cycle_state: CycleState
    active_session_id: str | None = None
    firmware: str = "wbox-3.4.1"
    signal_dbm: int = -71


class TelemetryEvent(BaseModel):
    machine_id: str
    at: datetime
    kind: str                   # relay_fail | relay_ok | power_drop | offline | online | cycle_abort | cycle_end | cycle_start
    detail: dict[str, Any] = Field(default_factory=dict)


class RelayResult(BaseModel):
    machine_id: str
    success: bool
    message: str
    at: datetime


# ---------------------------------------------------------------------------------
# Support
# ---------------------------------------------------------------------------------
class TicketIn(BaseModel):
    ticket_id: str
    user_id: str | None = None      # None for anonymous channels
    partner_id: str | None = None
    channel: Channel = Channel.CHAT
    text: str
    received_at: datetime = Field(default_factory=utcnow)


class TriageResult(BaseModel):
    intent: Intent
    language: Language
    confidence: float = Field(ge=0.0, le=1.0)
    machine_ref: str | None = None          # a machine id or label the user mentioned
    mentions_damage: bool = False
    mentions_legal_or_abuse: bool = False
    summary: str = ""


class Evidence(BaseModel):
    """Everything the policy engine is allowed to look at. Built from systems of record, never from the LLM."""
    user: User | None = None
    partner: Partner | None = None
    machine: Machine | None = None
    session: Session | None = None
    payments: list[Payment] = Field(default_factory=list)
    duplicate_payment: Payment | None = None
    refunds: list[Refund] = Field(default_factory=list)
    snapshot: TelemetrySnapshot | None = None
    events: list[TelemetryEvent] = Field(default_factory=list)
    relay_failures_7d: int = 0
    auto_refunds_30d: int = 0
    auto_refund_total_30d_cents: int = 0
    goodwill_credits_30d: int = 0
    notes: list[str] = Field(default_factory=list)


class PlannedAction(BaseModel):
    type: ActionType
    amount_cents: int = 0
    payment_id: str | None = None
    machine_id: str | None = None
    reason_code: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    outcome: Outcome
    actions: list[PlannedAction]
    reason_codes: list[str]
    explanation: str


class ActionResult(BaseModel):
    type: ActionType
    success: bool
    detail: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


class TicketOutcome(BaseModel):
    ticket_id: str
    outcome: Outcome
    triage: TriageResult
    decision: Decision
    actions: list[ActionResult]
    reply: str
    reply_language: Language
    handled_in_ms: int
    shadow: bool = False            # True = decision recorded, nothing executed, nothing sent
    evidence_summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------------
class MachineHealth(BaseModel):
    machine_id: str
    site_id: str
    score: int                      # 0..100
    severity: FaultSeverity
    signals: list[str]
    offline_minutes: int
    relay_failures_7d: int
    abort_rate_7d: float
    tickets_7d: int


class FaultTicket(BaseModel):
    fault_id: str
    machine_id: str
    site_id: str
    severity: FaultSeverity
    status: FaultStatus
    opened_at: datetime
    ack_due_at: datetime
    fix_due_at: datetime
    acknowledged_at: datetime | None = None
    fixed_at: datetime | None = None
    source: str                     # "agent" | "fleet_health" | "resident_report"
    signals: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------------
class StatementLine(BaseModel):
    site_id: str
    machine_id: str
    cycles: int
    gross_cents: int
    refunded_cents: int
    net_cents: int


class Statement(BaseModel):
    statement_id: str
    partner_id: str
    period_start: datetime
    period_end: datetime
    lines: list[StatementLine]
    gross_cents: int
    refunded_cents: int
    net_cents: int
    platform_fee_cents: int
    partner_share_cents: int
    generated_at: datetime


class PayoutInstruction(BaseModel):
    payout_id: str
    partner_id: str
    statement_id: str
    amount_cents: int
    end_to_end_id: str
    status: str = "created"         # created | sent | settled | returned
    sent_at: datetime | None = None
    settled_at: datetime | None = None
    return_reason: str | None = None
