"""Command line: ``laundryops eval``, ``laundryops serve``, ``laundryops demo``, ``laundryops graph``."""
from __future__ import annotations

import argparse
import json
import sys

from laundryops.config import Settings


def _eval(args: argparse.Namespace) -> int:
    from laundryops.evaluation import run_eval, write_report
    from laundryops.store.db import Store
    from laundryops.support.service import Platform

    settings = Settings(db_path=":memory:", llm_mode=args.llm)
    platform = Platform(settings=settings, store=Store(":memory:"), shadow=args.shadow)
    result = run_eval(n=args.n, seed=args.seed, settings=settings, platform=platform, shadow=args.shadow, noise=args.noise,
                      channel=args.channel)
    suffix = ("_voice" if args.channel == "voice" else "") + ("_noisy" if args.noise else "")
    md, js, rows = write_report(result, args.out, suffix=suffix)
    s = result["summary"]
    print(f"deflection={s['deflection_rate']:.1%} (ceiling {s['policy_ceiling_deflection']:.1%}) "
          f"outcome_acc={s['outcome_accuracy']:.1%} intent_acc={s['intent_accuracy']:.1%} "
          f"wrong_refund={s['wrong_refund_rate']:.2%} unsafe={s['unsafe_action_rate']:.2%} "
          f"p95={s['p95_ms']}ms  gate={'PASS' if s['gate']['passed'] else 'FAIL'}")
    print(f"report: {md}  json: {js}  rows: {rows}")
    return 0 if s["gate"]["passed"] or not args.gate else 1


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("laundryops.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _demo(args: argparse.Namespace) -> int:
    """Walk one ticket of each major kind through the agent and print the full trace."""
    from datetime import datetime, timezone

    from laundryops.store.db import Store
    from laundryops.support.service import Platform
    from laundryops.synth import build_world

    settings = Settings(db_path=":memory:", llm_mode=args.llm)
    platform = Platform(settings=settings, store=Store(":memory:"), shadow=args.shadow)
    now = datetime.now(timezone.utc)
    scenarios = build_world(platform, seed=args.seed, n_tickets=200, now=now)
    seen: set[str] = set()
    for sc in scenarios:
        if sc["kind"] in seen:
            continue
        seen.add(sc["kind"])
        sc["plant"]()
        out = platform.handle(sc["ticket"], now)
        print("=" * 100)
        print(f"[{sc['kind']}] ({sc['language']}) {sc['ticket'].text}")
        print(f"  triage   : {out.triage.intent.value} conf={out.triage.confidence:.2f} machine_ref={out.triage.machine_ref}")
        print(f"  decision : {out.outcome.value}  {out.decision.reason_codes}")
        for a in out.actions:
            print(f"  action   : {a.type.value:22s} {'ok ' if a.success else 'FAIL'} {json.dumps(a.detail, default=str)[:140]}")
        print("  reply    : " + out.reply.replace("\n", "\n             "))
    print("=" * 100)
    print(json.dumps(platform.store.metrics(), indent=1))
    return 0


def _call(args: argparse.Namespace) -> int:
    """Interactive hotline rehearsal in the terminal: stage a scenario, then type what the caller says."""
    from laundryops.demo_stage import SCENARIOS, stage
    from laundryops.store.db import Store
    from laundryops.support.service import Platform

    platform = Platform(settings=Settings(db_path=":memory:", llm_mode=args.llm), store=Store(":memory:"))
    info = stage(platform, args.scenario, args.lang)
    print(f"[{args.scenario}] {info['description']}")
    print(f"caller: {info['caller_name']} {info['from_number']} · machine {info['machine']['label']} ({info['machine']['machine_id']})")
    print(f"try saying: {info['what_to_say']}\n")
    turn = platform.voice.start("CLI1", info["from_number"])
    print("AGENT :", turn.say)
    while not turn.done:
        try:
            text = input("CALLER: ")
        except EOFError:
            break
        turn = platform.voice.step("CLI1", text)
        print("AGENT :", turn.say, f"   [{turn.state}]")
        if turn.sms:
            print("  (SMS to caller:", turn.sms + ")")
        if turn.handoff:
            print("  (warm transfer — whisper to human:", turn.whisper + ")")
    cs = platform.voice.calls["CLI1"]
    if cs.last_outcome:
        print("\nticket:", cs.last_outcome.decision.reason_codes, [(a.type.value, a.success, a.detail.get("amount_cents")) for a in cs.last_outcome.actions])
    print("call:", cs.outcome, "turns:", cs.turns)
    return 0


def _graph(args: argparse.Namespace) -> int:
    from laundryops.store.db import Store
    from laundryops.support.service import Platform

    print(Platform(settings=Settings(db_path=":memory:"), store=Store(":memory:")).support.mermaid())
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="laundryops")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eval", help="run the labelled 500-ticket evaluation and write evals/report.md")
    e.add_argument("--n", type=int, default=500)
    e.add_argument("--seed", type=int, default=42)
    e.add_argument("--llm", default="offline", choices=["offline", "openrouter"])
    e.add_argument("--shadow", action="store_true")
    e.add_argument("--out", default="evals")
    e.add_argument("--gate", action="store_true", help="exit 1 if the CI gate fails")
    e.add_argument("--noise", type=float, default=0.0, help="probability of each text perturbation (typos, no accents, filler)")
    e.add_argument("--channel", default="chat", choices=["chat", "voice"], help="voice = drive every scenario through the hotline dialog")
    e.set_defaults(fn=_eval)
    s = sub.add_parser("serve", help="start the API + ops console")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=_serve)
    d = sub.add_parser("demo", help="print a full trace for one ticket of each kind")
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--llm", default="offline", choices=["offline", "openrouter"])
    d.add_argument("--shadow", action="store_true")
    d.set_defaults(fn=_demo)
    c = sub.add_parser("call", help="rehearse the hotline in the terminal against a staged scenario")
    c.add_argument("--scenario", default="door_locked")
    c.add_argument("--lang", default="de", choices=["de", "en", "tr", "fr", "es"])
    c.add_argument("--llm", default="offline", choices=["offline", "openrouter"])
    c.set_defaults(fn=_call)
    g = sub.add_parser("graph", help="print the LangGraph flow as Mermaid")
    g.set_defaults(fn=_graph)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
