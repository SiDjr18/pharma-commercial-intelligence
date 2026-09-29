# LOCAL APPLICATION (M8; UI redesigned in M11)

> **M11:** the pages below were redesigned into an 8-area product (Executive Overview, Market Intelligence + Segments & mix, Brand & Portfolio, Company Intelligence, Opportunity Intelligence, Scenario Planning, AI Analyst at `/agent`, Methodology & Data Quality). Tools, routes, security headers and the free/offline runtime are unchanged. Design, interactions and QA: `docs/UI_UX_ARCHITECTURE.md`.

A free, offline, local web application over the validated M3–M7 engines.

## Start
```
cd <PROJECT_ROOT>
.venv\Scripts\python.exe run_app.py            # optional: --port 8765 --no-warm
```
Open **http://127.0.0.1:8765**. Startup takes about 1 s; the Python engine is pre-loaded. Stop with Ctrl+C.
The active processed layer must exist: `data/synthetic` (default; `python -m pci_synthetic.generate`) or, in IMS mode, `PCI_IMS_DATA_DIR` (built in M3, outside the repository). The IMS workbook is **not** read at runtime.

## Architecture
```
Browser (app/web: index.html, app.js, style.css — vanilla JS, no external assets)
   │  same-origin fetch  POST /api/tools/<tool>
   ▼
python/pci_app/server.py   stdlib ThreadingHTTPServer, 127.0.0.1 only, whitelisted routes, CSP headers,
                           Host allow-list (421), POST JSON + same-origin check (415/403)
   ▼
python/pci_app/tools.py    ToolRegistry: contracts, validation, structured errors, path scrubbing
   ▼
pci_analytics.CommercialAnalytics   (M4 SQL API + M6 opportunity + M7 scenario, unchanged)
   ▼
DuckDB views / pyarrow engine over the active dataset folder (data/synthetic, or PCI_IMS_DATA_DIR outside the repo)
```
Routes: `GET /`, `GET /static/app.js`, `GET /static/style.css`, `GET /api/health`, `GET /api/tools`, `POST /api/tools/<name>`. Everything else returns 404; PUT/DELETE/PATCH are refused. Request bodies are limited to 64 KB. The `Host` header must name this server as 127.0.0.1, localhost or [::1] with its port (else 421, which blocks DNS rebinding); POST bodies must be `application/json` and, when a browser sends `Origin`/`Sec-Fetch-Site`, same-origin (else 415/403). No CORS headers are sent. List tools never return an unbounded result: `top_n` null/absent means at most 2,000 rows (500 for product lists). Requests are serialised with a lock, since there is one analytics connection and this is a single-user local app.

## Pages
| Page | Tools used | Shows |
|---|---|---|
| Executive overview | market_performance(total), market_trends, therapy_performance(supergroup), brand/company performance, opportunity_scores | market value, growth, units, trend chart, therapy areas, leading products and companies, top descriptive opportunity scores |
| Market explorer | market_performance, market_trends | any market level (primary = subgroup), growth, share, EI, contribution, ranks; click a row for its 36-month trend |
| Product / brand | find_products, brand_share, brand_growth, opportunity_scores (company filter) | product-code keyed search (brand = label), national KPIs, trend, subgroup markets where the product sells (share in market, EI, opportunity status) |
| Company | company_performance, brand_performance(company), segment_analysis(company scope) | ranked companies within any scope; top products; acute/chronic mix |
| Therapy / segment | therapy_performance, segment_analysis | therapy levels (optionally within a therapy area); any segment within any scope |
| Opportunity | opportunity_scores, detail | **DESCRIPTIVE OPPORTUNITY SCORE** banner; methodology version and fingerprint; statuses; 2×2 matrix counts; component breakdown, drivers and constraints |
| Scenario simulator | run_scenario (+ lists for pickers) | **SCENARIO / WHAT-IF ANALYSIS — NOT A FORECAST** banner; the 5 supported types only; OBSERVED / ASSUMED / CALCULATED panels; changes, formulas, units, limitations |
| Data quality / methodology | metadata, /api/tools | source structure, period, definitions, units, versions, unsupported dimensions, DQ controls, tool contracts. No data, no paths. |
| Natural language (M9) | none | placeholder only; no AI service connected |

Global controls (anchor month, basis) apply to every page. Opportunity pages switch MONTH to MAT, because MONTH isn't allowed for scoring.

## Free, local-only runtime
| Item | Status |
|---|---|
| Runtime cost | $0 |
| Paid connectors, paid APIs, API keys | none |
| Cloud services, accounts, telemetry | none |
| External runtime network calls | none: the server binds 127.0.0.1; the UI loads no external scripts, fonts or CDNs (CSP `default-src 'self'`; tested) |
| Internet required | no |
| AI / LLM dependency | none (the M9 placeholder only) |
| Runtime dependencies | Python 3.13 stdlib (`http.server`, `json`, `threading`), plus existing `duckdb` and `pyarrow` |
| New dependencies in M8 | **none** |

## Privacy and security controls
- The IMS workbook is never opened by the app. There is no endpoint for raw rows, packs or files; the finest grain is product-in-subgroup aggregates.
- No arbitrary SQL, Python, shell or file access: whitelisted tools with validated parameters, and bound SQL parameters in the engines.
- Responses and errors are scrubbed of filesystem paths; internal errors return a generic message only.
- The UI escapes all data before rendering, and there are no inline scripts or styles (a strict CSP is enforced).
- Real figures appear only in the local browser. Documentation and tests contain no real aggregates or screenshots.

## Testing
`tests/test_app.py` (56 tests) covers:
- contracts and API coverage
- valid requests across all domains, with results identical to direct engine calls
- M6 and M7 methodology preservation
- 24 structured-error cases
- internal-error sanitisation and path scrubbing
- SQL-injection strings
- no generic execution surface, and no external network or AI imports
- HTTP end-to-end on an ephemeral port: UI, headers, 404 for traversal and file probes, bad bodies, oversized bodies, market/product/opportunity/scenario and invalid-scenario flows
- UI integration: only registered tools and local assets are used, and every page exists

A manual browser check exercised all 9 pages, including valid and invalid scenarios.

## Performance (local HTTP, warm)
| Request | Time |
|---|---|
| Market performance (1,889 subgroups) | ~0.5 s |
| Top-20 products / companies | ~0.4–0.6 s |
| Therapy subgroups | ~0.5 s |
| Opportunity scores | first request 3.6 s (scores the whole population), then ~0.25 s (cached) |
| Scenario | ~35 ms |
| Trend | ~0.15 s |

## Limitations
- Single-user local app: requests are serialised, and there is no authentication because it binds to localhost only.
- Scope-key pickers use plain text inputs with suggestion lists. Molecule, manufacturer and product-in-subgroup keys must be typed.
- Charts are hand-written SVG (M11 design system). Markets at molecule level show the top 500 by value.
- There is no natural-language interface (M9).
