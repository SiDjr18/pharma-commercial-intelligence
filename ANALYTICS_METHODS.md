# ANALYTICS METHODS

Status legend: **VALIDATED** (implemented in SQL and independently in Python — M5 — and cross-checked field-by-field; SQL also reconciled to the source) · **SOURCE-DEFINED** (formula stated or evidenced by the source) · **NOT YET VALIDATED** (future milestone)

Base measures (DATA_DICTIONARY.md §5): value ₹ crore (CONFIRMED), units '000 (INFERRED), qty '000 (INFERRED); monthly Jun 2021 – May 2024 at pack grain.
Computation source: `fact_pack_month` + `pack` via the M4 SQL layer (`docs/SQL_ANALYTICS.md`). `pack_snapshot` is used only to prove reconciliation.
Independent implementation: `python/pci_analytics/{periods,engine,metrics,rankings}.py` (`docs/PYTHON_ANALYTICS.md`). Every metric in §1–§8 agrees between SQL and Python within 1e-9 (structural fields exact) across a 54-check matrix.
Market definition: `docs/MARKET_DEFINITION.md` · Segments: `docs/SEGMENT_DEFINITIONS.md` · API: `API_SPEC.md`.

## 1. Sales / value — VALIDATED (M4)
- Month, **Calendar YTD** (Jan..anchor), MAT (12 months to anchor) for any entity; source-defined windows reproduced exactly (the source's May snapshots for total, supergroup, company and acute/chronic, 2022–2024).
- Aggregation = Σ over packs. All 15 entity types are pack partitions, so they reconcile exactly to the national total.
- Indian FY YTD: **not implemented** (not a source definition).

## 2. Volume — VALIDATED (M4) for units and qty sums/growth
- Units and qty are aggregated like value. Caution: qty sums across unlike dosage forms are only meaningful within a form segment (not enforced; documented).

## 3. Growth — VALIDATED (M4), SOURCE-DEFINED formula
- `growth % = cur / prior × 100 − 100` on aggregated sums, where the comparison window = the same window 12 months earlier. This reproduces the source pivot calculated fields (VAL/UNIT/QTY GRTH MONTH/CUMM/MAT) for 2023 and 2024 across 5 entity levels.
- prior = 0 → NULL (`prior_zero` or `no_sales`); prior window unavailable → NULL (`prior_unavailable`). **Never 0.**
- CAGR and price/volume/mix decomposition — NOT YET VALIDATED.

## 4. Market share — VALIDATED (M4)
- `share = entity / scope total × 100` within one partition type and one scope (market, company or segment). Shares sum to 100%.
- Share change (pp) and evolution index (`share_cur/share_prior × 100`) — VALIDATED.
- Product share is per market; a product spanning several subgroups has a separate share in each (value split by pack, no double counting).

## 5. Contribution — VALIDATED (M4)
- `contribution_to_growth_pp = (cur − prior) / scope prior × 100`; sums to scope growth.

## 6. Ranking — VALIDATED (M4)
- `rank_value`: value rounded to 1e-9 crore, descending, ties by key ascending. `rank_growth`: growth ratio (rounded 1e-9) descending, NULLS LAST, then value, then key. Always within the returned scope and filters.

## 7. Trend — VALIDATED (M4)
- Dense 36-month series per entity: month YoY, calendar YTD, MAT and their growth; NULL where the history is insufficient.
- Rolling 3-month and seasonality adjustment — NOT YET VALIDATED.

## 8. Segmentation — VALIDATED (M4)
- Segments: acute_chronic, indian_mnc, plain_combination, molecule_count, dosage_form, nfc1 (source fields only).
- Geography / channel / HCP / SSA-HSA-DSA / new-introduction segment — NOT SUPPORTED at this stage.

## 9. Opportunity score — IMPLEMENTED (M6), methodology OPP-1.0.0
- Canonical Python implementation built only from validated M4/M5 metrics: product-in-subgroup (market growth 0.30, evolution index 0.40, share in market 0.30) and subgroup market (growth 0.60, size 0.40); mid-rank percentile normalization; statuses SCORED / INSUFFICIENT_EVIDENCE. Weights are methodology assumptions. Full definition: `docs/OPPORTUNITY_SCORING.md`.

## 10. Scenario analysis — IMPLEMENTED (M7), methodology SCN-1.0.0
- Deterministic what-if (not a forecast): PRICE_CHANGE, VOLUME_CHANGE, PRICE_VOLUME_CHANGE, MARKET_GROWTH, MARKET_SHARE. Canonical Python engine `scenario.py`; baselines from the M5-validated engine. Full definition: `docs/SCENARIO_ENGINE.md`.
- Price = value_cr × 10^7 / (units_k × 10^3) = 10^4 × value/units (₹/pack), reproducing source PR exactly (≤ 4.4e-16) for every pack-month with units > 0.
- Price input available: PR = 1e4 × value/units (₹ per pack), SOURCE-DEFINED, reproduced in M3. PR is carried forward when units = 0.
