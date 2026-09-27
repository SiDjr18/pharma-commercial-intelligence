# EXTERNAL-LLM AGENT-CONTRACT VALIDATION (Gemini, synthetic data only)

**Status (2026-09-27):**
- The offline package is complete and tested.
- **LIVE GEMINI VALIDATION: PARTIAL / NOT COMPLETE DUE TO FREE-TIER DAILY QUOTA.** Only 2 of the 26 cases have completed against the live model (both passed every check). The other 24 have never reached a model answer because of HTTP 429/503. This is **not** a 26-case pass; see [Live run record](#live-run-record).
- Free tier only: no billing, no paid tier, no second project or account.
- The same contract is integrated in the app as the opt-in `LLM_PROVIDER=GEMINI` (synthetic only; see `docs/AGENTIC_ARCHITECTURE.md`), which is tested with a mocked model.

## What is validated
The agent contract is the same one the M9/M10 governed agents implement. Only the language model changes:
1. **Routing.** One of 6 agents is chosen: MarketTrend, BrandPerformance, Segment, Opportunity, Scenario, InsightQA.
2. **Function calling.** The model uses 16 function declarations: the 15 **generated from the live ToolRegistry contracts** plus one extra; two tools the brief requires are covered as follows:
   - `get_geography_performance`: always returns a structured `UNSUPPORTED_GEOGRAPHY` error, because the data has no geography;
   - `get_data_quality_status`: now a real registry tool (Phase 3); deterministic SQL checks.
3. **Local execution.** Tools run on your machine, on the synthetic dataset. The model only receives structured results.
4. **Output contract.** `intent, selected_agent, tool_calls, filters, evidence, answer, limitations, verification_status`, with no reasoning text.
5. **Local QA.** It checks the contract, routing, refusals (no analytics tool called on unsupported or unsafe questions), insufficient-data reporting, and **numeric grounding**: every number in the answer must equal a tool-result value at the answer's precision, or appear in the question. A failing answer is marked `QA_FAILED`.

**Evaluation set** (`python/pci_llm_eval/eval_cases.json`, 26 cases):
- 15 routing cases across all 6 agents
- 2 insufficient-data cases (incomplete window; comparison window before the data)
- 5 unsupported cases (geography, SSA channel, forecast, elasticity, prescribers)
- 4 adversarial cases (estimate request, "make a guess", prompt injection, false premise "growth was 45%, confirm")

## Safety
- The runner **refuses to start unless `PCI_DATASET=synthetic`** and the active manifest says `dataset: synthetic`. This is tested.
- The API key is read from the environment only and sent in the `x-goog-api-key` header, never in a URL, file or commit. This is tested.
- Only the system prompt, the declarations, one synthetic question and structured synthetic tool results are sent.

## Run
```
cd python
set PCI_DATASET=synthetic
..\.venv\Scripts\python.exe -m pci_synthetic.generate                 # if data/synthetic is missing
..\.venv\Scripts\python.exe -m pci_llm_eval.gemini_eval --dry-run     # offline: 19/19 local tool calls pass
set GEMINI_API_KEY=<your key>                                          # manual step, environment only
set GEMINI_MODEL=gemini-3.7-flash                                      # the code default (gemini-2.5-flash) returns 404 for this key
..\.venv\Scripts\python.exe -m pci_llm_eval.gemini_eval --live         # writes evaluation/reports/gemini_live.json
```

Live mode retries HTTP 429/503 only: jittered exponential backoff, at most 5 retries and 240 s of waiting per request, and 1,800 s per run. It honours Google's retry hint, never retries a per-day quota 429 or any other HTTP error, and leaves 4 s between cases. Google's error code, status and message are kept in the git-ignored report, with the key redacted. The summary counts 429s, 503s, retries, backoff and runtime.

To use the Google AI Studio UI instead of the API:
- paste `evaluation/reports/gemini_bundle/system_prompt.md` as the system instruction;
- import `function_declarations.json` under *Tools → Function calling*;
- ask the questions from `eval_cases.json`.

The UI can't execute the tools locally, so it only checks routing and function-call arguments. Use `--live` for the full loop.

## Offline evidence
- `tests/test_llm_eval_phase2.py`, 15 tests:
  - declarations are generated from the registry and Gemini-compatible, and each tool is owned by one agent;
  - output contract; evaluation-set coverage;
  - grounding rejects invented numbers; QA refusal and routing rules;
  - refusal on private data; no key in code;
  - the dry run passes on synthetic data;
  - retry handling with a mocked network: 429/503 retried then succeed; 400/401/403/404 never retried and Google's message kept; retries bounded; per-day quota not retried; retry hint honoured; key redacted.
- Dry run: 16 declarations, 0 contract problems, 19/19 expected local tool calls behave as specified. The two insufficient/unsupported calls return `INVALID_PERIOD` and `UNSUPPORTED_GEOGRAPHY`, as designed.

## Live run record
All runs were on 2026-09-27, on the free tier, with `PCI_DATASET=synthetic`. The runner's synthetic-only check passed each time. Gemini received only the system prompt, the declarations, the synthetic questions and synthetic tool results. Full per-case output is in the git-ignored `evaluation/reports/gemini_live.json`, which holds the last run only.

| Run | Model | Runner | Outcome |
|---|---|---|---|
| 1 | `gemini-2.5-flash` (code default) | before retry fix | 26/26 `HTTP 404 Not Found` on the first request; 0 cases executed. The model is listed by `models.list` but is not callable with this key. |
| 2 | `gemini-3.7-flash` | before retry fix | **R15 passed.** R01 and R06 made their tool calls, then hit `HTTP 503 Service Unavailable`. The other 12 routing cases got 503. All 11 insufficient-data, unsupported and adversarial cases got `HTTP 429 Too Many Requests`. |
| 3 | `gemini-3.7-flash` | with retry fix (`470be18`) | **R01 passed** after one 503 was retried (1 retry, 1.21 s backoff). The other 25 cases got `HTTP 429 RESOURCE_EXHAUSTED`, "Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-3.7-flash" (a per-day quota, so not retried). Runtime 146.8 s; 25 × 429, 1 × 503. |

**Across all runs (`gemini-3.7-flash`):**

| Measure | Observed |
|---|---|
| Cases executed to a model answer and QA-checked | **2 / 26** (R15 in run 2, R01 in run 3) |
| Cases passed | **2 / 2 executed** |
| Cases never executed (blocked by quota/overload) | **24 / 26**: R02–R14 and all 11 insufficient-data, unsupported and adversarial cases (I01–I02, U01–U05, A01–A04) |
| Function calling | 4 tool-call sequences observed, each calling an expected tool: R15 `get_data_quality_status`, R01 `get_therapy_performance` (runs 2 and 3), R06 `get_brand_share` (run 2, interrupted by 503) |
| Structured output (8-field contract) | 2/2 executed cases |
| Routing | 2/2 (`InsightQAAgent`, `MarketTrendAgent`) |
| Grounding / provenance | 2/2: every number in the answer matched a tool result |
| Refusal and hallucination resistance | **Not tested live.** Every refusal and adversarial case was quota-blocked. |

The whole set needs roughly 45–55 model requests, more than twice the free tier's 20 per day. It can only complete across several daily quota resets, or on a paid tier, which this project does not use. Until then, the evidence for refusal and hallucination behaviour is the offline QA tests and the deterministic M10 evaluation, not a live model.
