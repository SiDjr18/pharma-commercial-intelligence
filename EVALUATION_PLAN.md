# EVALUATION PLAN (baseline)

| Layer | What is evaluated | Method | Milestone |
|---|---|---|---|
| Data | Processed layer reproduces source | Reconciliation rules from DATA_QUALITY_BASELINE.md §8 as pytest tests; row/total parity pack × month | M3 |
| SQL vs Python | Same metric computed two ways | Cross-check tests, tolerance ≤ 1e-9 relative | M5 |
| Metric correctness | Growth / share / contribution / ranking | Hand-verified fixtures on synthetic mini-dataset | M4–M6 |
| Scenario engine | Determinism and assumption handling | Property tests (zero change → baseline; linearity where expected) | M7 |
| Agent routing | Correct agent + tool + parameters | Golden set of NL questions with expected tool calls | M10 |
| Numeric faithfulness | Numbers in answers ⊆ tool outputs | Automated extraction + comparison; target 100% | M10 |
| Refusal correctness | Unsupported questions (geography, HCP, channel) are declined | Golden negative set; target 100% | M10 |
| Latency / cost | Per-question time and tokens | Logged per eval run | M10 |

Golden sets must be written against **synthetic** data for the public repo; private-data eval results stay local.
Metric targets beyond the 100% items above: TO BE DETERMINED.
