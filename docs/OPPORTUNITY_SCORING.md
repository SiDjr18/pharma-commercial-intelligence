# OPPORTUNITY SCORING — methodology OPP-1.0.0 (M6)

Canonical implementation: `python/pci_analytics/opportunity.py`. Parameters: `python/pci_analytics/opportunity_config.py` (version + fingerprint `ac4362f187f25004`). There is **one** implementation: no SQL copy, and both API classes delegate to it. **No LLM** is used to compute or explain any score.

## 1. Objective
Rank products and markets by the **strength of observed evidence** of commercial opportunity, using only metrics validated in M4 (SQL) and M5 (independent Python). The score is a relative, descriptive ranking (percentiles within a defined population). It is **not** a forecast or a probability.

## 2. Unit of scoring
| Level | Entity | Key | Why |
|---|---|---|---|
| `product` | product **within its therapy-subgroup market** | source `INDEX` (= product × subgroup) | Share and evolution index need a market. 4,519 products span several subgroups, so a product-level score would mix markets. The brand name is a display label only. |
| `market` | therapy subgroup (primary market, docs/MARKET_DEFINITION.md) | `SUBGROUP` | Growth and size are validated at market level |

Other domains (company, segment, molecule) are **not scored** in this version, because no market-relative evidence has been defined for them.

## 3. Eligible entities and required evidence → score status
There are two statuses: **`SCORED`** and **`INSUFFICIENT_EVIDENCE`**. The latter always carries one reason. Rules apply in this order:

| Reason | Rule | Rationale |
|---|---|---|
| `insufficient_history` | comparison window predates the data (MAT < 2023-05, YTD < 2023-01) | Growth is not measurable |
| `prior_zero` | prior-window value = 0 | Growth and EI are undefined. **Never** treated as 0% or as low performance. |
| `below_materiality` | prior value < `min_prior_value_cr` (0.01 crore = ₹1 lakh) | On tiny bases, growth/EI are driven by noise. *Methodology assumption.* |
| `market_zero_current` | market has no current sales (product only) | Share can't be computed and is **not** manufactured |
| `single_product_market` | < 2 active products (cur or prior > 0) in the market (product only) | No competitive evidence (share is 100% and EI is 100 by construction) |

A product with prior > 0 and **current = 0** is `SCORED`. That is genuine low performance (growth −100%, share 0, EI 0), not missing evidence. Insufficient entities have `score = null`, `opportunity_rank = null` and null normalized components. They are never ranked with scored entities.

Real data, MAT 2024-05: 42,106 of 65,398 product-in-market entities scored, and 1,798 of 1,889 markets.

## 4. Components (selected from the validated M4/M5 metrics)
### Product (product within subgroup)
| Component | Raw metric (validated) | Weight | Direction |
|---|---|---|---|
| Market growth | market `value_growth_pct` (subgroup, same window) | 0.30 | higher = better |
| Relative momentum | `evolution_index` of the product in its market | 0.40 | higher = better |
| Market position | `value_share_pct` of the product in its market | 0.30 | higher = better |

### Market (subgroup)
| Component | Raw metric | Weight | Direction |
|---|---|---|---|
| Market growth | `value_growth_pct` | 0.60 | higher = better |
| Market size | `value_share_pct` of the national total | 0.40 | higher = better |

**Why these, and why not the others** (rank correlations measured on the real population, MAT 2024-05):
- **Product growth is excluded:** its correlation with EI is 0.94, and product growth ≈ (1 + market growth) × EI. Using market growth + EI covers growth without double counting (EI vs market growth: −0.03).
- **Contribution to market growth is excluded:** it correlates 0.69 with EI and mixes share with growth.
- **Share is retained:** it correlates only 0.25 with EI and 0.06 with market growth, so it adds a distinct position signal.
- **Market level:** growth vs size correlate 0.26, so both are kept. Market EI vs national would be a monotone transform of market growth (same national total), so it is excluded.
- **Units growth:** not used (volume units scale is inferred; value is the confirmed measure).
- **Trend consistency:** not used. It would be a new, unvalidated metric.

## 5. Normalization
**Mid-rank percentile** within the reference population: `normalized = (average rank − 1) / (n − 1)`, in [0, 1], computed on values rounded to 1e-9 (the same rule as M4/M5 ranks). Ties share the average rank, and n = 1 gives 0.5.

- **Reference population:** all `SCORED` entities of the same level **nationally**, for the requested anchor and basis. Filters (therapy area, subgroup, company) select rows only and **never change scores**.
- **Why:** the distributions are extremely heavy-tailed (the maximum EI is about 250× its 95th percentile; the maximum growth is about 150× its 99th percentile). Min-max would compress almost every entity near 0. Z-scores assume symmetry. Robust min-max would need arbitrary clip points. Percentiles are bounded, scale-free and deterministic.
- **Reproducibility:** the same data, anchor, basis and config always give identical scores (tested).

## 6. Outliers
There is no clipping or winsorization: rank-based normalization already bounds each outlier's influence to its component weight (the top EI contributes exactly 40 points, never more; tested). Raw metrics are preserved unchanged in every output.

## 7. Weights
Weights are **methodology assumptions**, not empirical estimates:
- Relative momentum carries the most weight (0.40), because it is the most direct evidence of competitive traction.
- Market growth and position are balanced at 0.30.
- For markets, growth (0.60) outweighs size (0.40), because growth is the opportunity signal and size is context.

All weights and thresholds are in `OpportunityConfig`. Changing them requires a version bump, because the pinned fingerprint test fails otherwise.

## 8. Score calculation
`score = 100 × Σ weight_i × normalized_i` (weights sum to 1, so the score is in [0, 100]). Each component reports `raw`, `normalized`, `weight` and `weighted_contribution = 100 × weight × normalized`, and the contributions sum exactly to the score. Ranking: `opportunity_rank` is score rounded to 1e-9 descending, ties by key (UTF-8 byte order), among `SCORED` entities only. Envelopes also give `rank_in_selection` after filters.

## 9. Explanation (deterministic templates)
- **`positive_drivers`:** components with normalized ≥ 0.75 (raw value + percentile).
- **`constraints`:** components with normalized ≤ 0.25, plus fixed data limitations (market definition, units scale, national only). For insufficient entities, the first constraint is the reason text.

## 10. Opportunity matrix (product level, raw validated metrics)
| | Market growth ≥ national market growth | Market growth < national |
|---|---|---|
| **EI ≥ 100** (gaining share) | Outperforming in faster-growing market | Outperforming in slower-growing market |
| **EI < 100** (losing share) | Underperforming in faster-growing market | Underperforming in slower-growing market |

Boundaries are inclusive (EI = 100 and market growth = national growth fall in the upper/left cells). National growth is total-market value growth for the same window. Only `SCORED` products are classified; others get `null`. The matrix is descriptive; quadrant names don't prescribe action.

## 11. Interpretation
A score of 80 means that, on the weighted evidence, the entity sits around the 80th percentile of comparable scored entities for that window. Scores are comparable **only** within the same level, anchor, basis and methodology version.

## 12. Sensitivity (methodology diagnostic, MAT 2024-05, not a robustness proof)
Each weight was moved ±0.05, with the others rescaled proportionally, and equal weights were also tried:

| Variant | Products: Spearman vs baseline | Products: top-100 overlap | Markets: Spearman | Markets: top-100 overlap |
|---|---|---|---|---|
| each weight ±0.05 (range) | 0.994 – 0.996 | 0.90 – 0.98 | 0.996 – 0.997 | 0.92 – 0.95 |
| equal weights | 0.991 | 0.87 | 0.984 | 0.89 |

Overall ordering is insensitive to small weight changes. The exact membership of a top-100 list changes by 2–13%, so neighbouring ranks near a cut-off should not be over-interpreted. This was tested for one anchor only. Run `opportunity.sensitivity()` for others.

## 13. Limitations
- Weights, the materiality floor and the minimum-competitor rule are judgement-based assumptions.
- Scores are relative within one anchor/basis and cannot be compared across periods or methodology versions.
- Markets are the source therapy subgroups (analytical definition), and the data is national only.
- No prescriber, promotion, pricing-strategy or pipeline evidence exists in the source, so "opportunity" means observed-market evidence only.
- Eligible bases are MAT (default) and calendar YTD. MONTH is excluded as too noisy.

## 14. Methodology version
`OPP-1.0.0` · fingerprint `ac4362f187f25004` (SHA-256 of all config parameters, first 16 hex characters) · introduced in M6.
