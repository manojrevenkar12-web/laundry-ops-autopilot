# Eval report — 500 tickets, seed 42, triage=offline-rules, text noise p=0.5

**Gate: PASS**  deflection_rate=ok, intent_accuracy=ok, outcome_accuracy=ok, wrong_refund_rate=ok, unsafe_action_rate=ok

| Metric | Value |
|---|---|
| Deflection rate (no human needed) | **89.0%** (policy ceiling on this mix: 89.6%) |
| Outcome accuracy | 99.4% |
| Intent accuracy | 98.4% |
| Action accuracy | 98.6% |
| Wrong-refund rate (money that should not have moved) | **0.00%** |
| Missed-refund rate | 0.00% |
| Unsafe-action rate (money/hardware on escalation-labelled tickets) | **0.00%** |
| Escalation precision / recall | 94.5% / 100.0% |
| Refunds issued / total | 180 / 630.00 € |
| Relay re-triggers | 96 |
| Latency p50 / p95 | 1 ms / 2 ms |

## By language

| Lang | n | Deflection | Intent acc. | Outcome acc. |
|---|---|---|---|---|
| de | 227 | 86.8% | 98.7% | 99.6% |
| en | 153 | 91.5% | 99.4% | 100.0% |
| es | 38 | 92.1% | 97.3% | 100.0% |
| fr | 38 | 81.6% | 92.1% | 94.7% |
| tr | 44 | 95.5% | 100.0% | 100.0% |

## By scenario kind

| Kind | n | Outcome acc. | Intent acc. | Action acc. | Refunded € |
|---|---|---|---|---|---|
| aborted | 55 | 100.0% | 100.0% | 100.0% | 191.00 |
| app_payment | 23 | 95.7% | 95.7% | 95.7% | 0.00 |
| charged_no_session | 34 | 100.0% | 100.0% | 100.0% | 121.50 |
| damage | 19 | 100.0% | 94.7% | 100.0% | 0.00 |
| double_charge | 36 | 100.0% | 100.0% | 100.0% | 123.00 |
| double_claim_single | 12 | 91.7% | 91.7% | 91.7% | 0.00 |
| general | 37 | 97.3% | 97.3% | 97.3% | 0.00 |
| injection | 5 | 100.0% | 100.0% | 100.0% | 0.00 |
| legal | 9 | 100.0% | 88.9% | 100.0% | 0.00 |
| machine_report | 36 | 100.0% | 100.0% | 94.4% | 0.00 |
| not_started_offline | 18 | 100.0% | 100.0% | 100.0% | 63.00 |
| not_started_relay_fault | 37 | 100.0% | 100.0% | 94.6% | 131.50 |
| not_started_relay_ok | 61 | 100.0% | 96.7% | 100.0% | 0.00 |
| partner | 25 | 100.0% | 100.0% | 100.0% | 0.00 |
| refund_status_found | 25 | 100.0% | 100.0% | 100.0% | 0.00 |
| refund_status_none | 8 | 100.0% | 100.0% | 100.0% | 0.00 |
| repeat_refunder_cap | 7 | 100.0% | 100.0% | 100.0% | 0.00 |
| reservation_conflict | 31 | 100.0% | 100.0% | 100.0% | 0.00 |
| running_normal | 10 | 100.0% | 90.0% | 100.0% | 0.00 |
| unverifiable | 12 | 100.0% | 100.0% | 100.0% | 0.00 |

Labelled scenarios with the agent's intent, actions, reason codes and reply are in `eval_set.jsonl`.
Synthetic data: wording and failure mix are designed, not sampled from a real operator. Treat these as the
policy's behaviour under a controlled distribution, not as a forecast for any specific ticket stream.
