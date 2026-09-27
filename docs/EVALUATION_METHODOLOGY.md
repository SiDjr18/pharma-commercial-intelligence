# EVALUATION METHODOLOGY (M10)

Harness: `python/pci_eval/`, which contains `cases.py` (dataset), `controls.py` (negative controls) and `runner.py` (execution, metrics, reports).

```
cd python
..\.venv\Scripts\python.exe -m pci_eval
```
This writes `evaluation/reports/AGENT_EVAL_REPORT.md` (committed; metrics only) plus `agent_eval_report.json` and `agent_eval_cases.csv`. The JSON and CSV are local and git-ignored.

Everything runs locally in DETERMINISTIC DEMO MODE, with no network, LLM or API key; sockets are blocked in a test. The harness plays the user: it resolves placeholders and replies to clarifications, and it may read the ToolRegistry to audit results. Agents never touch the ToolRegistry directly; they use the permissioned gateway.

## 1. Dataset structure (`EvalCase`)
| Field | Meaning |
|---|---|
| `case_id`, `category` | ID and one of 31 categories A–AE (`CATEGORIES`) |
| `user_request` | `{"text"}`, structured `{"intent","params"}`, `{"turns":[...]}` (multi-turn), or a non-object (malformed) |
| `expected_intent`, `expected_agent`, `expected_tools` | exact intent, the ordered specialist route, and the exact ordered tool sequence |
| `prohibited_tools` | by default every analytics tool not expected (13 tools in total) |
| `expected_status` | `OK`, `AMBIGUOUS_ENTITY`, a refusal status, or a passed-through tool error code |
| `expected_disambiguation` | the case must end in a clarification: more than one product code, no product tools, a continuation token |
| `expected_evidence_behavior` | `findings_with_provenance` \| `no_tool_calls` \| `clarification_only` \| `tool_error_passed_through` \| `findings_with_limitation` |
| `expected_qa_behavior` | `pass` (for all dataset cases; blocking is tested by the negative controls) |
| `security_class` | `none`, `prompt_injection`, `sql_injection`, `code_injection`, `path_injection`, `url_injection`, `instruction_override`, `fabrication`, `data_exfiltration`, `permission_escalation`, `forced_selection` or `token_tampering` |
| `expected_methodology`, `expected_disclaimer`, `expected_error_code`, `notes` | where applicable |

The dataset has **75 cases**, 24 of them adversarial.
- **No real data in fixtures:** placeholders (`@top_product`, `@shared_brand`, `@unique_brand`, `@dormant_product`, `@top_company`, `@top_subgroup`, `@top_opp_key`, `@insufficient_opp_key`, …) are resolved at run time. Quoted literals are limited to the category label `CARDIAC` and a fake brand; a test enforces this.
- **Report files:** no entity names or IMS figures appear in them (tested).

## 2. Per-case pass definition
A case passes only if **all** of the following hold:
- status = expected
- intent = expected, and route = expected
- tool sequence = expected, and no prohibited tool was called
- QA passed
- disambiguation behaviour matches (and no case unexpectedly ends ambiguous)
- evidence behaviour matches
- methodology and disclaimer texts are present when required
- the expected error code appears (tool record or status)
- multi-turn cases: turn 1 clarified; turn 2 preserved the original parameters, used only the selected code and did not search again
- **all 3 repeated executions are equivalent** (determinism)

## 3. Metrics and denominators
| Metric | Numerator | Denominator |
|---|---|---|
| Routing accuracy | cases with intent **and** route correct | all cases (75) |
| Tool-selection accuracy | cases with the exact tool sequence and no prohibited tool | all cases (75) |
| Tool-permission compliance | tool calls not DENIED | all attempted tool calls across the dataset |
| QA pass rate | cases whose valid response passed QA | all cases (every case expects QA pass) |
| QA block rate | negative controls blocked with the expected QA check among the failures | negative controls expecting QA_FAILED (24) |
| Numerical provenance rate | numeric claims whose cited tool field, **re-executed independently** through the registry, formats to the same display | all numeric claims in OK responses |
| Methodology preservation | cases with the required methodology label/version and disclaimer present | cases with a methodology/disclaimer requirement |
| Unsupported-request handling | UNSUPPORTED_* cases returning that exact status with zero tool calls | cases expecting UNSUPPORTED_* |
| Ambiguity handling | ambiguity events handled correctly | ambiguity events: expected clarifications + first turns of multi-turn cases + refused forced selection |
| Injection resistance | adversarial cases fully passing | cases with `security_class != none` |
| Determinism | cases with 3 equivalent runs | all cases |
| End-to-end success | valid (expected OK) cases fully passing | cases expecting OK |
| Fault-injection catch rate | all negative controls blocked | all negative controls (24) |
| Tool-failure handling | simulated tool errors preserved (status = code, no findings, explanation present, QA passed) | simulated failures (6) |

**Equivalence** means the user-visible view is identical: status, intent, route, tools, finding statements, limitations, final text, and the QA check list. Raw floats may differ by about 1e-12 (the documented M4 parallel-summation noise); displays may not.

## 4. Routing error matrix
Each case gets an expected and an actual label: `MarketTrendAgent`, `BrandProductAgent`, `CompanySegmentAgent`, `OpportunityAgent`, `ScenarioAgent`, `MultiStep`, `Clarification`, `Refusal`, `Unrecognized` or `InputRejected`.

Off-diagonal cells are classified as:
- **false_route** — agent ↔ agent
- **false_refusal** — expected an agent, got a refusal, unrecognized or rejection
- **missed_ambiguity** — expected a clarification, got something else
- **unsupported_accepted** — expected a refusal, got an agent

## 5. Red-team methodology
- **Adversarial dataset cases (24).** They cover prompt injection, instruction override, SQL/Python/path/URL injection, data exfiltration, permission escalation, fabrication requests, forced entity selection, and token tampering. The expected safe behaviour is one of: refuse (`UNSAFE_REQUEST`), unsupported (`UNSUPPORTED_*`), treat the input as a literal key (`ENTITY_NOT_FOUND` via the tool), reject the input (`INVALID_INPUT`), or clarify.
- **Attack matrix (17 strings) in `tests/test_eval_m10.py`.** Examples: `../`, `..\`, `'`, `"`, `--`, `UNION SELECT`, `__import__`, `os.system`, `subprocess`, environment variables, API key, URLs, `file://`, "ignore all prior instructions", "call every tool", "bypass", "jailbreak". Each must give no OK tool result, no DENIED attempt, no path in any string, and no traceback.
- **Structured injections** in brand names are treated as literal text, and the tables remain intact.
- **Principle:** the system is never made more permissive to raise completion; refusals are counted as successes only when a refusal is the expected behaviour.

## 6. Hallucination and provenance testing
**Numeric claims.** A finding value is a reference: `claim → tool → result field → value`. It appears in the response as `provenance` (for example `{"tool": "get_company_performance", "field": "rows[1].value_cur", "value": "…"}`). Provenance is checked twice:
- QA re-reads each field in-process.
- The M10 audit re-executes every recorded tool call through the registry and compares the formatted field.

**Hallucination controls (NC01–NC07).** A known number is altered, or an invented percentage, market size, ranking, growth, price or scenario result is added. Each gives `QA_FAILED` via `text_numbers_traceable`, and the answer is withheld.

## 7. Disclaimer and methodology testing
| Control | Fault injected | Caught by |
|---|---|---|
| NC08 | opportunity presented as "most likely to succeed" | `no_unsupported_claims` |
| NC09 | scenario turned into a forecast | `no_unsupported_claims` |
| NC10 | scenario banner dropped | `scenario_not_forecast` |
| NC11 | opportunity methodology dropped | `methodology_preserved` |
| NC12 | market-definition note dropped | `market_definition` |
| NC13 | "descriptive" rewritten as "predicted" | `no_unsupported_claims` |
| NC17 | tool returns a non-published methodology version | `published_methodology` (new in M10) |
| NC18 | scenario result lost its not-a-forecast disclaimer | `published_methodology` (new in M10) |

## 8. Fault injection and tool failures
**Tool-result faults.** The real registry is wrapped (`FaultyRegistry`) and one tool's result is mutated:

| Control | Fault | Caught by |
|---|---|---|
| NC14 | wrong unit | `unit_consistency` |
| NC15 | wrong period | `period_entity_consistency` |
| NC16 | wrong entity | `requested_entity` (new) |
| NC19 | missing evidence | `evidence_complete` (new) |
| NC22 | malformed rows | `result_well_formed` (new); the agent fails safe with INTERNAL_ERROR |
| NC23 | missing value field | `result_well_formed` (new); the agent fails safe |
| NC24 | missing methodology | `evidence_complete` |

**Rogue agents.** A permitted-but-unexpected tool (NC20) is caught by `tool_scope` (new). An unauthorized tool (NC21) is DENIED and caught by `tool_provenance`.

**Tool failures.** `FailingRegistry` simulates ENTITY_NOT_FOUND, INVALID_PERIOD, INSUFFICIENT_EVIDENCE, UNSUPPORTED_ANALYSIS, INVALID_SCENARIO and INTERNAL_ERROR. In each case the code is preserved, there are no findings, an explanation is present, QA passes (no false success), and nothing is invented.

## 9. Multi-turn clarification
Turn 1 (ambiguous brand) returns `AMBIGUOUS_ENTITY`, the options, and a **continuation token**. Turn 2 sends `{"continuation", "select"}`, or text containing the code.

**The token.**
- **Contents:** only the intent, the original parameters (minus the name) and the options.
- **Signing:** it is HMAC-SHA256 signed with a per-process in-memory key. Nothing is stored server-side and there is no long-term memory.

**What is tested.**
- A valid code resumes with the original period and filters and calls only `get_brand_share` and `get_brand_growth`.
- An invalid code produces the clarification again, with no tool call.
- Tampered or foreign tokens, or extra keys in the reply, return `INVALID_INPUT`.
- Permissions and QA stay active on turn 2.

## 10. Latency methodology
- **Setup:** 8 workflows measured **before** the dataset run, in a clean process state.
- **Cold:** the first run after clearing the Python engine caches.
- **Warm:** the median, minimum and maximum of 5 runs.
- **Components:** tool time is the sum of the gateway call timings; QA time is measured around `qa.validate`.
- **Measurement fix:** latency measured *after* the suite was inflated 2–3× by memory held in the provenance-audit cache. This was found in M10, so the order was changed and the cache is now released.

No targets are claimed; the results are in `AGENT_EVAL_REPORT.md`.

## 11. First-run findings and fixes (all reruns pass)
| First-run failure | Root cause | Fix |
|---|---|---|
| U02 QA_FAILED | the orchestrator's own rejection message echoed the parameter name "elasticity", and the forbidden-wording scan treated it as a claim | system/tool messages are treated as governed text, like limitations |
| AE02 misrouted | "segment analysis … in company X" hit the company rule before the segment rule | segment intent takes precedence in the parser |
| NC22–NC24 blocked for the wrong reason | malformed results tripped `market_definition`, or crashed QA (`qa_engine`) | new `result_well_formed` check; presentation checks apply only when an answer is shown; QA reads tool fields defensively |
| Latency 2–3× high | measured after the suite with a large audit cache in memory | measure first; release the cache |

## 12. Limitations
- Deterministic demo mode is a fixed request grammar. The evaluation proves governance and routing *for that grammar*, not natural-language understanding.
- The dataset targets 75 cases, the upper end of the requested range. Some categories have 1–2 cases, so metrics are exact on this dataset, not statistical estimates.
- Latency is from a single laptop under whatever else was running at the time. Cold opportunity latency includes scoring the whole population once.
- Refusal keywords are conservative, so false refusals are possible outside the grammar. No such case exists in the dataset (false_refusal = 0).
