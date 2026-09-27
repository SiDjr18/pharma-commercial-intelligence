"""Opportunity scoring methodology configuration (M6).

All weights, thresholds and normalization choices live here, not in the engine or in SQL.
Weights are METHODOLOGY ASSUMPTIONS (judgement), not empirical estimates; see
docs/OPPORTUNITY_SCORING.md. Changing any value requires bumping `version`; the pinned
fingerprint test (tests/test_opportunity.py) enforces this.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Component:
    name: str            # component id
    metric: str          # raw validated metric used (M4/M5 definition)
    weight: float        # share of the 0..100 score
    direction: int       # +1: higher raw value = more opportunity
    label: str           # display name for explanations


@dataclass(frozen=True)
class OpportunityConfig:
    version: str = "OPP-1.0.0"
    normalization: str = "midrank_percentile"   # (avg_rank - 1) / (n - 1) within the reference population
    rank_decimals: int = 9                       # values rounded before ranking (same rule as M4/M5)
    allowed_bases: tuple = ("MAT", "YTD")        # MONTH excluded: single-month growth is too noisy
    min_prior_value_cr: float = 0.01             # materiality floor on prior-window value (Rs crore) — assumption
    min_active_products: int = 2                 # products with cur or prior sales > 0 in the market
    driver_threshold: float = 0.75               # normalized >= this -> positive driver
    constraint_threshold: float = 0.25           # normalized <= this -> constraint
    matrix_ei_threshold: float = 100.0           # EI >= 100: gaining share vs its market
    matrix_market_growth_reference: str = "national"   # market growth compared with national market growth
    product_components: tuple = field(default_factory=lambda: (
        Component("market_growth", "market_value_growth_pct", 0.30, +1, "Market growth"),
        Component("relative_momentum", "evolution_index", 0.40, +1, "Relative momentum (evolution index)"),
        Component("market_position", "value_share_in_market_pct", 0.30, +1, "Market position (value share)"),
    ))
    market_components: tuple = field(default_factory=lambda: (
        Component("market_growth", "value_growth_pct", 0.60, +1, "Market growth"),
        Component("market_size", "value_share_of_total_pct", 0.40, +1, "Market size (share of national value)"),
    ))

    def __post_init__(self):
        for comps in (self.product_components, self.market_components):
            s = sum(c.weight for c in comps)
            if abs(s - 1.0) > 1e-12:
                raise ValueError(f"component weights must sum to 1, got {s}")
            if any(c.weight < 0 or c.direction not in (1, -1) for c in comps):
                raise ValueError("weights must be >= 0 and direction +1/-1")

    def fingerprint(self) -> str:
        """Stable hash of every methodology parameter (reported with every score)."""
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


DEFAULT_CONFIG = OpportunityConfig()
