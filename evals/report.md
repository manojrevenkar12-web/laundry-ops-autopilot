# Eval report — 500 tickets, seed 42, triage=offline-rules

**Gate: PASS**  deflection_rate=ok, intent_accuracy=ok, outcome_accuracy=ok, wrong_refund_rate=ok, unsafe_action_rate=ok

| Metric | Value |
|---|---|
| Deflection rate (no human needed) | **89.6%** (policy ceiling on this mix: 89.6%) |
| Outcome accuracy | 100.0% |
| Intent accuracy | 100.0% |
| Action accuracy | 100.0% |
| Wrong-refund rate (money that should not have moved) | **0.00%** |
| Missed-refund rate | 0.00% |
| Unsafe-action rate (money/hardware on escalation-labelled tickets) | **0.00%** |
| Escalation precision / recall | 100.0% / 100.0% |
| Refunds issued / total | 185 / 648.00 € |
| Relay re-triggers | 101 |
| Latency p50 / p95 | 2 ms / 3 ms |

## By language

| Lang | n | Deflection | Intent acc. | Outcome acc. |
|---|---|---|---|---|
| de | 233 | 90.1% | 100.0% | 100.0% |
| en | 156 | 87.8% | 100.0% | 100.0% |
| es | 27 | 96.3% | 100.0% | 100.0% |
| fr | 45 | 86.7% | 100.0% | 100.0% |
| tr | 39 | 92.3% | 100.0% | 100.0% |

## By scenario kind

| Kind | n | Outcome acc. | Intent acc. | Action acc. | Refunded € |
|---|---|---|---|---|---|
| aborted | 47 | 100.0% | 100.0% | 100.0% | 161.50 |
| app_payment | 30 | 100.0% | 100.0% | 100.0% | 0.00 |
| charged_no_session | 36 | 100.0% | 100.0% | 100.0% | 124.50 |
| damage | 22 | 100.0% | 100.0% | 100.0% | 0.00 |
| double_charge | 50 | 100.0% | 100.0% | 100.0% | 177.50 |
| double_claim_single | 13 | 100.0% | 100.0% | 100.0% | 0.00 |
| general | 30 | 100.0% | 100.0% | 100.0% | 0.00 |
| injection | 7 | 100.0% | 100.0% | 100.0% | 0.00 |
| legal | 8 | 100.0% | 100.0% | 100.0% | 0.00 |
| machine_report | 39 | 100.0% | 100.0% | 100.0% | 0.00 |
| not_started_offline | 17 | 100.0% | 100.0% | 100.0% | 58.00 |
| not_started_relay_fault | 35 | 100.0% | 100.0% | 100.0% | 126.50 |
| not_started_relay_ok | 66 | 100.0% | 100.0% | 100.0% | 0.00 |
| partner | 16 | 100.0% | 100.0% | 100.0% | 0.00 |
| refund_status_found | 26 | 100.0% | 100.0% | 100.0% | 0.00 |
| refund_status_none | 9 | 100.0% | 100.0% | 100.0% | 0.00 |
| repeat_refunder_cap | 7 | 100.0% | 100.0% | 100.0% | 0.00 |
| reservation_conflict | 27 | 100.0% | 100.0% | 100.0% | 0.00 |
| running_normal | 7 | 100.0% | 100.0% | 100.0% | 0.00 |
| unverifiable | 8 | 100.0% | 100.0% | 100.0% | 0.00 |

Labelled scenarios with the agent's intent, actions, reason codes and reply are in `eval_set.jsonl`.
Synthetic data: wording and failure mix are designed, not sampled from a real operator. Treat these as the
policy's behaviour under a controlled distribution, not as a forecast for any specific ticket stream.
