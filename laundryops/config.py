"""Runtime configuration.

Every number that governs money or hardware actuation lives here so an operator can
read the whole risk envelope in one place. Nothing in the LLM layer can change these.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOPS_", env_file=".env", extra="ignore")

    # ---- LLM runtime -------------------------------------------------------------
    llm_mode: str = Field(default="offline", description="offline | openrouter")
    openrouter_api_key: str | None = None
    openrouter_model: str = "anthropic/claude-sonnet-4.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_fallback_to_offline: bool = True
    llm_timeout_s: float = 30.0

    # ---- Storage -------------------------------------------------------------------
    db_path: str = "data/laundryops.sqlite"

    # ---- Refund policy (cents) -----------------------------------------------------
    max_auto_refund_cents: int = 1000           # anything above goes to a human
    max_auto_refunds_per_user_30d: int = 3      # repeat refunders go to a human
    max_auto_refund_total_per_user_30d_cents: int = 2000
    goodwill_credit_cents: int = 150            # reservation conflicts etc.
    goodwill_credits_per_user_30d: int = 1
    dual_control_above_cents: int = 2500        # never reachable by the agent; documented ceiling

    # ---- Hardware actuation policy -------------------------------------------------
    relay_retrigger_window_min: int = 20        # paid session must be this fresh
    relay_max_retries: int = 1
    relay_fault_threshold_7d: int = 2           # consecutive relay failures -> machine suspect

    # ---- Fleet health --------------------------------------------------------------
    offline_minutes_critical: int = 240
    abort_rate_warning: float = 0.15
    tickets_7d_warning: int = 3
    fault_ack_sla_hours: int = 48
    fault_fix_sla_hours: int = 120

    # ---- Settlement ----------------------------------------------------------------
    platform_fee_bps: int = 2500                # 25 % platform share of net revenue
    payout_min_cents: int = 1000
    debtor_name: str = "WeWash GmbH (demo)"
    debtor_iban: str = "DE02120300000000202051"
    debtor_bic: str = "BYLADEM1001"


settings = Settings()
