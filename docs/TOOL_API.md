# TOOL API (M8) — `TOOLS-1.0.0`

A thin, whitelisted boundary over the validated analytics API. Code: `python/pci_app/tools.py`. **No metric is computed in this layer.** Every number is returned unchanged from the M4–M7 engines (tested).

```
UI / future agents ──> ToolRegistry.invoke(name, params)
                         1. tool must be in the registry (else UNKNOWN_TOOL)
                         2. params validated against the tool's JSON-Schema contract
                         3. call the one mapped CommercialAnalytics method
                         4. map engine errors -> structured error codes
                         5. scrub filesystem paths / non-finite numbers
                       <── {"ok": true, "tool", "result": <engine envelope>}  |  {"ok": false, "error": {...}}
```

## Tools (14)
| Tool | Maps to | Required parameters |
|---|---|---|
| `get_application_metadata` | selector values + methodology versions (no analytics) | – |
| `find_products` | `find_products` | name_contains |
| `get_market_performance` | `get_market_performance` | – |
| `get_market_trends` | `get_market_trends` | – |
| `get_brand_performance` | `get_brand_performance` | – |
| `get_brand_growth` | `get_brand_growth` | prod_code |
| `get_brand_share` | `get_brand_share` | prod_code, market_level, market_key |
| `get_company_performance` | `get_company_performance` | – |
| `get_therapy_performance` | `get_therapy_performance` | – |
| `get_segment_analysis` | `get_segment_analysis` | segment |
| `get_opportunity_scores` | M6 `get_opportunity_scores` | – |
| `get_opportunity_detail` | M6 `get_opportunity_detail` | level, entity_key |
| `run_scenario` | M7 `run_scenario` | scenario_type, entity_type, entity_key, assumptions |
| `get_scenario_baseline` | M7 `get_scenario_baseline` | entity_type, entity_key |
| `get_data_quality_status` | `get_data_quality_status` (Phase 3) | none |

There is deliberately **no geography tool**: the data is national. Geography questions are refused as `UNSUPPORTED_GEOGRAPHY` before any tool call.

Every public analytical method of `CommercialAnalytics` is exposed, and nothing else (tested).

## Machine-readable contracts
`GET /api/tools` (or `pci_app.tools.contracts()`) returns, per tool: `name`, `purpose`, `input_schema`, `output`, `limitations` and `errors`.
- **`input_schema`** is a JSON-Schema subset with `type`, `enum`, `format: date`, `minimum`/`maximum`, `minLength`/`maxLength`, `nullable`, `required`, and `additionalProperties: false`.
- **`output`** lists the envelope keys, row fields, units and period semantics.

Enums come directly from the engine constants: market levels, segments, bases, entity types, scenario types, and the opportunity levels and bases. Full parameter semantics: `API_SPEC.md`. Period semantics: `docs/SQL_ANALYTICS.md`.

Validation happens before any engine call:
- Types are strict: booleans are not integers, and numbers must be finite.
- Dates must be real ISO dates.
- Unknown parameters are rejected, not ignored. Examples: `sql`, and an `elasticity` assumption.

## Structured errors
```json
{"ok": false, "error": {"code": "INVALID_PERIOD", "category": "Invalid or unavailable period",
                        "message": "...", "engine_code": "period_unavailable"}}
```
| Code | Meaning | Engine code |
|---|---|---|
| `INVALID_INPUT` | wrong type, enum, range, format or extra parameter | invalid_parameter |
| `MISSING_PARAMETER` | a required parameter is absent | – |
| `UNKNOWN_TOOL` | not in the registry (e.g. `execute_sql`) | – |
| `UNSUPPORTED_ANALYSIS` | forecast, elasticity, prescriber, promotion, etc. | unsupported |
| `UNSUPPORTED_GEOGRAPHY` | geography/region/state/zone/city | unsupported |
| `UNSUPPORTED_CHANNEL` | channel | unsupported |
| `UNSUPPORTED_SSA_HSA_DSA` | SSA/HSA/DSA splits | unsupported |
| `INVALID_PERIOD` | outside data, or incomplete window | period_unavailable |
| `INSUFFICIENT_EVIDENCE` | zero-unit or zero-market baseline | insufficient_baseline |
| `INVALID_SCENARIO` | assumption out of range, or wrong type | invalid_assumption |
| `ENTITY_NOT_FOUND` | unknown key, or entity not in market | not_found |
| `INTERNAL_ERROR` | unexpected failure; generic message, and details only in the local log | – |

An **unavailable comparison period is not an error.** The engines return null prior/growth values plus a caveat (M4–M7 semantics are preserved). Error messages never contain tracebacks or paths.

## Evidence and traceability (preserved)
Engine envelopes are passed through unchanged:
- `period` (window, basis label, comparison window)
- `filters`, `units`
- `evidence` (SQL macro and parameters, source sha256, build time, definitions)
- `caveats`, and methodology (version + fingerprint) for opportunity scoring
- the OBSERVED / ASSUMED / CALCULATED blocks, formulas and limitations for scenarios

The finest grain exposed is product-in-subgroup, which is already an aggregate. No pack rows, source rows or file paths are ever returned (tested).

## Security restrictions
- A whitelist of 14 named tools, and parameters are data only. There is no SQL, Python, shell or filesystem access.
- Every engine query uses bound parameters. Injection strings are treated as literal keys, return `ENTITY_NOT_FOUND`, and leave all tables intact (tested).
- Response strings are scrubbed of anything resembling a filesystem path or private file name.
