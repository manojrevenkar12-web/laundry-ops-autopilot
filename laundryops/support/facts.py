"""Builds the ``facts`` dict the reply layer is allowed to use. Nothing else reaches the model."""
from __future__ import annotations

from typing import Any

from laundryops.domain.models import ActionResult, ActionType, Decision, Evidence, Outcome, PlannedAction

HANDOVER_REASON = {
    "ESC_DAMAGE_CLAIM": "damage", "ESC_NO_IDENTITY": "identity", "ESC_NO_EVIDENCE": "evidence",
    "ESC_INJECTION": "", "ESC_LEGAL_OR_ABUSE": "", "ESC_AMOUNT_OVER_CAP": "review", "ESC_CAP_COUNT_EXCEEDED": "review",
    "ESC_CAP_TOTAL_EXCEEDED": "review",
}


def build_facts(ev: Evidence, decision: Decision, results: list[ActionResult] | None, shadow: bool) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "name": ev.user.display_name.split()[0] if ev.user else (ev.partner.name if ev.partner else ""),
        "machine_label": ev.machine.label if ev.machine else None,
        "refund_eta_days": 5,
        "actions": [],
        "handover": decision.outcome == Outcome.ESCALATED,
        "handover_reason": HANDOVER_REASON.get(decision.reason_codes[0], "") if decision.reason_codes else "",
        "shadow": shadow,
    }
    if ev.machine:
        facts["price_cents"] = ev.machine.price_cents
    source: list[Any] = results if results is not None else decision.actions
    for a in source:
        if isinstance(a, ActionResult):
            if a.type == ActionType.ESCALATE_HUMAN:
                facts["handover"] = True
                continue
            d = a.detail
            entry = {"type": a.type.value, "success": a.success, "reason_code": d.get("reason_code", "")}
            if "amount_cents" in d:
                entry["amount_cents"] = d["amount_cents"]
            if "statement" in d:
                entry["statement"] = d["statement"]
            if a.type == ActionType.SEND_INFO:
                facts["info_kind"] = d.get("info_kind")
                for k in ("last_charge_cents", "refund"):
                    if k in d:
                        facts[k] = d[k]
                if "started_at" in d:
                    facts["cycle_started_at"] = d["started_at"][11:16]
            facts["actions"].append(entry)
        else:  # PlannedAction (shadow mode)
            p: PlannedAction = a
            if p.type == ActionType.ESCALATE_HUMAN:
                continue
            entry = {"type": p.type.value, "success": True, "reason_code": p.reason_code}
            if p.amount_cents:
                entry["amount_cents"] = p.amount_cents
            if p.type == ActionType.SEND_INFO:
                facts["info_kind"] = p.detail.get("info_kind")
                for k in ("last_charge_cents", "refund"):
                    if k in p.detail:
                        facts[k] = p.detail[k]
                if "started_at" in p.detail:
                    facts["cycle_started_at"] = p.detail["started_at"][11:16]
            facts["actions"].append(entry)
    return facts
