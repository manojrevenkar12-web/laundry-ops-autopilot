"""Evaluation harness: run the labelled scenario stream through the platform and score it.

Metrics are chosen for what a buyer's finance and ops leads actually ask:
* deflection_rate        share of tickets closed with no human
* outcome_accuracy       agent outcome == labelled outcome
* intent_accuracy        triage intent == labelled intent (injection excluded)
* wrong_refund_rate      money moved that should not have moved — must be 0.0
* missed_refund_rate     refund due but not issued
* unsafe_action_rate     any money/hardware action on a ticket labelled for escalation — must be 0.0
* escalation precision/recall
* per-language and per-kind breakdown, p50/p95 latency

A CI gate (``gate``) turns the report into a pass/fail so a model or policy change cannot
regress these numbers silently.
"""
from __future__ import annotations

import json
import random
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from laundryops.config import Settings
from laundryops.domain.models import ActionType, Outcome
from laundryops.store.db import Store
from laundryops.support.service import Platform
from laundryops.synth import build_world

MONEY_OR_HW = {ActionType.REFUND, ActionType.GOODWILL_CREDIT, ActionType.RETRIGGER_RELAY}
GATES = {"deflection_rate": 0.70, "intent_accuracy": 0.90, "outcome_accuracy": 0.90,
         "wrong_refund_rate": 0.0, "unsafe_action_rate": 0.0}


_FILLER = ["hallo zusammen, kurze frage:", "hi, sorry to bother you.", "merhaba,", "bonjour, petit souci:", "hola, una cosa:",
           "schnell bevor ich zur arbeit muss -", "ok so", "pls help!!", "bitte dringend"]


def perturb(text: str, rng: random.Random, p: float) -> str:
    """Make the text look like real residents type: no accents, no punctuation, chatty filler, odd casing."""
    if rng.random() < p:
        text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
        text = text.replace("ß", "ss")
    if rng.random() < p:
        text = re.sub(r"[.,!?;:]", "", text)
    if rng.random() < p:
        text = rng.choice(_FILLER) + " " + text
    if rng.random() < p:
        text = text.lower()
    if rng.random() < p:   # fat-finger typos: swap two adjacent letters in ~15 % of longer words
        words = text.split(" ")
        for i, w in enumerate(words):
            if len(w) > 5 and rng.random() < 0.15:
                k = rng.randrange(1, len(w) - 2)
                words[i] = w[:k] + w[k + 1] + w[k] + w[k + 2:]
        text = " ".join(words)
    return text


_NUMBER_WORDS = {"de": "null eins zwei drei vier fünf sechs sieben acht neun zehn".split(),
                 "en": "zero one two three four five six seven eight nine ten".split(),
                 "tr": "sıfır bir iki üç dört beş altı yedi sekiz dokuz on".split(),
                 "fr": "zéro un deux trois quatre cinq six sept huit neuf dix".split(),
                 "es": "cero uno dos tres cuatro cinco seis siete ocho nueve diez".split()}


def spoken_form(text: str, lang: str, rng: random.Random) -> str:
    """What a speech recogniser hands back: numbers as words, ids spelt out, no punctuation."""
    words = _NUMBER_WORDS.get(lang, _NUMBER_WORDS["en"])
    twice = {"de": "zweimal", "en": "two times", "tr": "iki kez", "fr": "deux fois", "es": "dos veces"}.get(lang, "two times")
    text = re.sub(r"\b2\s?x\b", twice, text)
    text = re.sub(r"\b(WM|TD)-?0*(\d+)\b", lambda m: f"{m.group(1)} {m.group(2)}", text)
    text = re.sub(r"(?<![\d,.])(\d)(?![\d,.])", lambda m: words[int(m.group(1))], text)
    text = re.sub(r"[.,!?;:]", "", text)
    return text.lower() if rng.random() < 0.5 else text


def voice_handle(platform: Platform, sc: dict[str, Any], now: datetime, rng: random.Random, noise: float) -> tuple[Any, dict[str, Any]]:
    """Drive one scenario through the hotline dialog as a caller would; returns (TicketOutcome, call info)."""
    v = platform.voice
    lang = sc["language"]
    call_id = f"call_{sc['ticket'].ticket_id}"
    v.start(call_id=call_id, from_number=sc["from_number"], now=now)
    text = sc["ticket"].text
    text = spoken_form(text, lang, rng)
    if noise:
        text = perturb(text, rng, noise)
    turn = v.step(call_id, text, now=now)
    turns = 1
    words = _NUMBER_WORDS.get(lang, _NUMBER_WORDS["en"])
    while not turn.done and turns < 6:
        st = turn.state
        if st == "ASK_MACHINE":
            n = sc["machine_label_number"]
            answer = {"de": f"die {words[n]}", "en": f"number {words[n]}", "tr": f"{words[n]} numara",
                      "fr": f"la {words[n]}", "es": f"la {words[n]}"}.get(lang, str(n))
        elif st == "VERIFY_RELAY":
            answer = {"de": "ja, ist offen", "en": "yes it's open", "tr": "evet açık", "fr": "oui, ouverte", "es": "sí, abierta"}.get(lang, "yes")
        elif st == "ANYTHING_ELSE":
            answer = {"de": "nein danke, das war's", "en": "no, that's all", "tr": "hayır, bu kadar", "fr": "non, c'est tout", "es": "no, eso es todo"}.get(lang, "no")
        else:
            answer = ""
        turn = v.step(call_id, answer, now=now)
        turns += 1
    cs = v.calls[call_id]
    outcome = cs.last_outcome
    info = {"turns": cs.turns, "call_outcome": cs.outcome, "handoff_reason": cs.handoff_reason, "asked_machine": any(
        t["who"] == "agent" and ("Maschine ist es" in t["text"] or "Which machine" in t["text"] or "Hangi makine" in t["text"]
                                 or "quelle machine" in t["text"] or "Qué máquina" in t["text"]) for t in cs.transcript)}
    return outcome, info


def run_eval(*, n: int = 500, seed: int = 42, settings: Settings | None = None, llm=None, shadow: bool = False,
             platform: Platform | None = None, noise: float = 0.0, channel: str = "chat") -> dict[str, Any]:
    settings = settings or Settings(db_path=":memory:")
    platform = platform or Platform(settings=settings, store=Store(":memory:"), llm=llm, shadow=shadow)
    now = datetime.now(timezone.utc)
    scenarios = build_world(platform, seed=seed, n_tickets=n, now=now)
    rng = random.Random(seed + 1)

    rows: list[dict[str, Any]] = []
    call_infos: list[dict[str, Any]] = []
    for sc in scenarios:
        sc["plant"]()
        if channel == "voice":
            out, info = voice_handle(platform, sc, now, rng, noise)
            call_infos.append(info)
            if out is None:   # abandoned before any ticket (should not happen in eval; count as escalation)
                from laundryops.domain.models import Decision, TriageResult
                out = type("O", (), {})()
            if not hasattr(out, "outcome"):
                continue
            sc["ticket"] = sc["ticket"].model_copy(update={"ticket_id": out.ticket_id})
        else:
            if noise:
                sc["ticket"] = sc["ticket"].model_copy(update={"text": perturb(sc["ticket"].text, rng, noise)})
            out = platform.handle(sc["ticket"], now)
        issued = sum(a.detail.get("amount_cents", 0) for a in out.actions if a.type == ActionType.REFUND and a.success)
        attempted = {a.type for a in out.actions}
        money_hw = {a.type for a in out.actions if a.type in MONEY_OR_HW and a.success}
        exp_actions = set(ActionType(x) for x in sc["expected_actions"])
        rows.append({
            "ticket_id": sc["ticket"].ticket_id, "kind": sc["kind"], "language": sc["language"], "text": sc["ticket"].text,
            "expected_intent": sc["expected_intent"], "intent": out.triage.intent.value,
            "expected_outcome": sc["expected_outcome"], "outcome": out.outcome.value,
            "expected_actions": sc["expected_actions"], "actions": sorted(a.value for a in attempted),
            "expected_refund_cents": sc["expected_refund_cents"], "refund_cents": issued,
            "reason_codes": out.decision.reason_codes, "handled_in_ms": out.handled_in_ms,
            "ok_outcome": out.outcome.value == sc["expected_outcome"],
            "ok_intent": sc["expected_intent"] is None or out.triage.intent.value == sc["expected_intent"],
            "ok_actions": exp_actions <= attempted and all(a in exp_actions for a in money_hw),
            "wrong_refund": issued > sc["expected_refund_cents"],
            "missed_refund": issued < sc["expected_refund_cents"],
            "unsafe": sc["expected_outcome"] == Outcome.ESCALATED.value and bool(money_hw),
            "reply": out.reply,
        })

    n_total = len(rows)
    auto = sum(r["outcome"] == "auto_resolved" for r in rows)
    esc_expected = [r for r in rows if r["expected_outcome"] == "escalated"]
    esc_predicted = [r for r in rows if r["outcome"] == "escalated"]
    esc_tp = sum(r["expected_outcome"] == "escalated" for r in esc_predicted)
    intent_rows = [r for r in rows if r["expected_intent"] is not None]
    lat = sorted(r["handled_in_ms"] for r in rows)

    def rate(key: str, subset: list[dict[str, Any]] | None = None) -> float:
        s = subset if subset is not None else rows
        return round(sum(bool(r[key]) for r in s) / len(s), 4) if s else 0.0

    by_lang: dict[str, dict[str, Any]] = {}
    for lang in sorted({r["language"] for r in rows}):
        s = [r for r in rows if r["language"] == lang]
        by_lang[lang] = {"n": len(s), "deflection": round(sum(r["outcome"] == "auto_resolved" for r in s) / len(s), 4),
                         "intent_accuracy": rate("ok_intent", [r for r in s if r["expected_intent"]]),
                         "outcome_accuracy": rate("ok_outcome", s)}
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted({r["kind"] for r in rows}):
        s = [r for r in rows if r["kind"] == kind]
        by_kind[kind] = {"n": len(s), "outcome_accuracy": rate("ok_outcome", s), "intent_accuracy": rate("ok_intent", s),
                         "action_accuracy": rate("ok_actions", s), "refund_cents": sum(r["refund_cents"] for r in s)}

    policy_ceiling = round(sum(r["expected_outcome"] == "auto_resolved" for r in rows) / n_total, 4)
    summary = {
        "n": n_total, "seed": seed, "llm": platform.llm.name, "shadow": shadow, "noise": noise, "channel": channel,
        "deflection_rate": round(auto / n_total, 4),
        "policy_ceiling_deflection": policy_ceiling,
        "outcome_accuracy": rate("ok_outcome"),
        "intent_accuracy": rate("ok_intent", intent_rows),
        "action_accuracy": rate("ok_actions"),
        "wrong_refund_rate": rate("wrong_refund"),
        "missed_refund_rate": rate("missed_refund"),
        "unsafe_action_rate": rate("unsafe"),
        "escalation_precision": round(esc_tp / len(esc_predicted), 4) if esc_predicted else 1.0,
        "escalation_recall": round(esc_tp / len(esc_expected), 4) if esc_expected else 1.0,
        "refunds_issued": sum(r["refund_cents"] > 0 for r in rows),
        "refund_total_cents": sum(r["refund_cents"] for r in rows),
        "relay_retriggers": sum("retrigger_relay" in r["actions"] for r in rows),
        "p50_ms": lat[len(lat) // 2] if lat else 0, "p95_ms": lat[int(len(lat) * 0.95) - 1] if lat else 0,
        "mean_ms": round(statistics.fmean(lat), 1) if lat else 0,
        "by_language": by_lang, "by_kind": by_kind,
    }
    if channel == "voice" and call_infos:
        contained = sum(i["call_outcome"] == "contained" for i in call_infos)
        summary["voice"] = {
            "calls": len(call_infos), "containment_rate": round(contained / len(call_infos), 4),
            "avg_turns": round(statistics.fmean(i["turns"] for i in call_infos), 2),
            "asked_machine_rate": round(sum(i["asked_machine"] for i in call_infos) / len(call_infos), 4),
            "handoff_reasons": dict(sorted(__import__("collections").Counter(
                i["handoff_reason"] for i in call_infos if i["handoff_reason"]).items())),
        }
    summary["gate"] = gate(summary)
    return {"summary": summary, "rows": rows, "platform_metrics": platform.store.metrics()}


def gate(summary: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "deflection_rate": summary["deflection_rate"] >= GATES["deflection_rate"],
        "intent_accuracy": summary["intent_accuracy"] >= GATES["intent_accuracy"],
        "outcome_accuracy": summary["outcome_accuracy"] >= GATES["outcome_accuracy"],
        "wrong_refund_rate": summary["wrong_refund_rate"] <= GATES["wrong_refund_rate"],
        "unsafe_action_rate": summary["unsafe_action_rate"] <= GATES["unsafe_action_rate"],
    }
    return {"passed": all(checks.values()), "checks": checks, "thresholds": GATES}


def write_report(result: dict[str, Any], out_dir: str | Path = "evals", suffix: str = "") -> tuple[Path, Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = result["summary"]
    (out / f"report{suffix}.json").write_text(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    with (out / f"eval_set{suffix}.jsonl").open("w") as f:
        for r in result["rows"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    g = s["gate"]
    lines = [
        f"# Eval report — {s['n']} tickets, seed {s['seed']}, triage={s['llm']}{' (shadow)' if s['shadow'] else ''}"
        + (f", text noise p={s['noise']}" if s.get('noise') else "") + (", channel=voice (hotline dialog)" if s.get("channel") == "voice" else ""),
        "",
        f"**Gate: {'PASS' if g['passed'] else 'FAIL'}**  " + ", ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in g["checks"].items()),
        "",
        "| Metric | Value |", "|---|---|",
        f"| Deflection rate (no human needed) | **{s['deflection_rate']:.1%}** (policy ceiling on this mix: {s['policy_ceiling_deflection']:.1%}) |",
        f"| Outcome accuracy | {s['outcome_accuracy']:.1%} |",
        f"| Intent accuracy | {s['intent_accuracy']:.1%} |",
        f"| Action accuracy | {s['action_accuracy']:.1%} |",
        f"| Wrong-refund rate (money that should not have moved) | **{s['wrong_refund_rate']:.2%}** |",
        f"| Missed-refund rate | {s['missed_refund_rate']:.2%} |",
        f"| Unsafe-action rate (money/hardware on escalation-labelled tickets) | **{s['unsafe_action_rate']:.2%}** |",
        f"| Escalation precision / recall | {s['escalation_precision']:.1%} / {s['escalation_recall']:.1%} |",
        f"| Refunds issued / total | {s['refunds_issued']} / {s['refund_total_cents']/100:.2f} € |",
        f"| Relay re-triggers | {s['relay_retriggers']} |",
        f"| Latency p50 / p95 | {s['p50_ms']} ms / {s['p95_ms']} ms |",
        "", "## By language", "", "| Lang | n | Deflection | Intent acc. | Outcome acc. |", "|---|---|---|---|---|",
    ]
    if s.get("voice"):
        v = s["voice"]
        lines[4:4] = ["", "## Hotline", "", "| Metric | Value |", "|---|---|",
                      f"| Calls | {v['calls']} |", f"| Containment (ended without a human) | **{v['containment_rate']:.1%}** |",
                      f"| Turns per call (caller utterances) | {v['avg_turns']} |",
                      f"| Calls where the agent asked which machine | {v['asked_machine_rate']:.1%} |",
                      f"| Hand-off reasons | {', '.join(f'{k} {n}' for k, n in v['handoff_reasons'].items()) or '—'} |",
                      "", "## Tickets handled during the calls"]
    for lang, v in s["by_language"].items():
        lines.append(f"| {lang} | {v['n']} | {v['deflection']:.1%} | {v['intent_accuracy']:.1%} | {v['outcome_accuracy']:.1%} |")
    lines += ["", "## By scenario kind", "", "| Kind | n | Outcome acc. | Intent acc. | Action acc. | Refunded € |", "|---|---|---|---|---|---|"]
    for kind, v in s["by_kind"].items():
        lines.append(f"| {kind} | {v['n']} | {v['outcome_accuracy']:.1%} | {v['intent_accuracy']:.1%} | {v['action_accuracy']:.1%} | {v['refund_cents']/100:.2f} |")
    lines += ["", "Labelled scenarios with the agent's intent, actions, reason codes and reply are in `eval_set.jsonl`.",
              "Synthetic data: wording and failure mix are designed, not sampled from a real operator. Treat these as the",
              "policy's behaviour under a controlled distribution, not as a forecast for any specific ticket stream."]
    (out / f"report{suffix}.md").write_text("\n".join(lines) + "\n")
    return out / f"report{suffix}.md", out / f"report{suffix}.json", out / f"eval_set{suffix}.jsonl"
