"""FastAPI surface: ticket intake, ops console data, fleet and settlement endpoints.

Run: ``uvicorn laundryops.api.app:app --reload`` then open http://127.0.0.1:8000/console
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from laundryops import __version__
from laundryops.config import Settings
from laundryops.domain.models import Channel, TicketIn
from laundryops.store.db import Store
from laundryops.support.service import Platform
from laundryops.synth import build_world

app = FastAPI(title="laundry-ops-autopilot", version=__version__,
              description="Autonomous support & remediation, fleet health and partner settlement for shared laundry rooms.")

_settings = Settings()
platform = Platform(settings=_settings, store=Store(_settings.db_path))


class TicketRequest(BaseModel):
    text: str
    user_id: str | None = None
    partner_id: str | None = None
    channel: Channel = Channel.CHAT
    ticket_id: str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "llm": platform.llm.name, "shadow": platform.shadow}


# ---- support ----------------------------------------------------------------------------
@app.post("/tickets")
def create_ticket(req: TicketRequest) -> dict[str, Any]:
    tid = req.ticket_id or f"t_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    out = platform.handle(TicketIn(ticket_id=tid, user_id=req.user_id, partner_id=req.partner_id, channel=req.channel, text=req.text))
    return out.model_dump(mode="json")


@app.get("/tickets")
def list_tickets(limit: int = Query(200, le=1000)) -> list[dict[str, Any]]:
    return platform.store.list_tickets(limit)


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str) -> dict[str, Any]:
    t = platform.store.get_ticket(ticket_id)
    if not t:
        raise HTTPException(404, "ticket not found")
    t["audit"] = platform.store.audit_trail(ticket_id)
    return t


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    return platform.store.metrics()


@app.get("/graph", response_class=PlainTextResponse)
def graph() -> str:
    return platform.support.mermaid()


# ---- fleet ------------------------------------------------------------------------------
@app.get("/fleet/health")
def fleet_health() -> list[dict[str, Any]]:
    return [h.model_dump(mode="json") for h in platform.fleet.assess()]


@app.post("/fleet/dispatch")
def fleet_dispatch() -> dict[str, Any]:
    return platform.fleet.dispatch()


@app.post("/fleet/proactive")
def fleet_proactive() -> dict[str, Any]:
    outs = platform.fleet.proactive_sweep(platform.handle)
    return {"handled": len(outs), "auto_resolved": sum(o.outcome.value == "auto_resolved" for o in outs),
            "tickets": [o.ticket_id for o in outs]}


@app.get("/fleet/faults")
def faults(status: str | None = None) -> list[dict[str, Any]]:
    return [f.model_dump(mode="json") for f in platform.store.list_faults(status)]


@app.post("/fleet/faults/{fault_id}/ack")
def ack_fault(fault_id: str) -> dict[str, Any]:
    f = platform.fleet.acknowledge(fault_id)
    if not f:
        raise HTTPException(404, "fault not found")
    return f.model_dump(mode="json")


@app.post("/fleet/faults/{fault_id}/fix")
def fix_fault(fault_id: str) -> dict[str, Any]:
    f = platform.fleet.fix(fault_id)
    if not f:
        raise HTTPException(404, "fault not found")
    return f.model_dump(mode="json")


@app.post("/fleet/outage/{machine_id}/notify")
def notify_outage(machine_id: str) -> dict[str, Any]:
    return {"notified": platform.fleet.notify_residents_of_outage(machine_id)}


# ---- settlement --------------------------------------------------------------------------
@app.post("/settlement/run")
def settlement_run(days: int = 30) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    res = platform.settlement.run_month((now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0), now)
    res["pain001_bytes"] = len(res.pop("pain001"))
    return res


@app.get("/settlement/partners/{partner_id}")
def partner_status(partner_id: str) -> dict[str, Any]:
    if not platform.directory.get_partner(partner_id):
        raise HTTPException(404, "partner not found")
    return platform.settlement.partner_status(partner_id)


@app.get("/settlement/payouts")
def payouts() -> list[dict[str, Any]]:
    return [p.model_dump(mode="json") for p in platform.store.list_payouts()]


@app.get("/settlement/sepa", response_class=PlainTextResponse)
def sepa_file() -> str:
    pending = [p for p in platform.store.list_payouts() if p.status == "created"]
    if not pending:
        raise HTTPException(404, "no payouts in 'created' state")
    return platform.settlement.build_sepa_file(pending)


class BankRecord(BaseModel):
    end_to_end_id: str
    status: str
    reason: str | None = None


@app.post("/settlement/reconcile")
def reconcile(records: list[BankRecord]) -> dict[str, Any]:
    return platform.settlement.reconcile([r.model_dump() for r in records])


# ---- demo + console ----------------------------------------------------------------------
@app.post("/demo/seed")
def demo_seed(n: int = Query(120, le=2000), seed: int = 42) -> dict[str, Any]:
    """Populate the world and stream n labelled tickets through the agent so the console has data."""
    now = datetime.now(timezone.utc)
    scenarios = build_world(platform, seed=seed, n_tickets=n, now=now)
    for sc in scenarios:
        sc["plant"]()
        platform.handle(sc["ticket"], now)
    platform.fleet.dispatch(now)
    return {"seeded_tickets": len(scenarios), "metrics": platform.store.metrics()}


@app.get("/demo/users")
def demo_users(limit: int = 20) -> list[dict[str, Any]]:
    return [u.model_dump(mode="json") for u in list(platform.directory.users.values())[-limit:]]


@app.get("/console", response_class=HTMLResponse)
def console() -> str:
    return (Path(__file__).parent / "static" / "console.html").read_text(encoding="utf-8")
