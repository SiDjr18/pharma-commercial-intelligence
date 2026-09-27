# AGENT SPECIFICATION (baseline — implemented in M9)

Implementation: `python/pci_agents/` · architecture: `docs/AGENTIC_ARCHITECTURE.md` · permissions: `docs/AGENT_TOOLS.md` · evaluation: `docs/AGENT_EVALUATION.md`. The segment agent is implemented as `CompanySegmentAgent` (company, therapy, segment); an `OpportunityAgent` was added for M6 scoring.

## Global rules (all agents)
1. Agents **must** obtain every business number by calling a deterministic tool (API_SPEC.md).
2. Agents **must not** calculate critical metrics (growth, share, totals, scores) themselves, and must not round/alter tool outputs beyond display formatting.
3. Agents **must not** fabricate missing data. If a request needs data the source lacks (geography, channel, HCP, promotion), respond that it is unsupported and say why.
4. Agents never see raw IMS rows — only tool outputs and schema metadata.
5. Every answer states: metric, market definition/filters, period, unit, and data-quality caveats.

## Orchestrator
- Detect intent and scope; resolve entities (company, brand → PROD_CODE, molecule, therapy level, period) via lookup tools.
- Reject or clarify ambiguous/unsupported requests (e.g. brand name shared by several companies → ask which).
- Route to one or more specialists; merge structured results; send to Insight/QA agent.

## Market Trend Agent
- Market size and trends over time for total / therapy / molecule / segment markets.
- Tools: `get_market_trends`, `get_segment_analysis`.

## Brand Performance Agent
- Brand/company KPIs, growth, share, rank, competitors within a market.
- Tools: `get_brand_performance`, `get_brand_growth`, `get_brand_share`.

## Segment Agent
- Cuts by acute/chronic, Indian/MNC, therapy hierarchy, dosage form, plain/combination, new introductions; opportunity views.
- Tools: `get_segment_analysis`, `get_opportunity_scores`.

## Scenario Agent
- Translates a what-if into explicit scenario parameters, calls `run_scenario`, reports assumptions alongside results.

## Insight / QA Agent
- Verifies that every number in the draft answer appears in tool results; checks filters/periods echo the question; attaches `get_data_quality_status` caveats; produces the final grounded narrative or blocks the answer.
