"""Deterministic, explainable opportunity scoring (M6) — CANONICAL implementation.

The score is computed only in Python, from metrics produced by the M5-validated Python engine
(which agrees with the M4 SQL layer field by field). There is no SQL copy of the formula.

Levels
  product : product within its therapy-subgroup market (key = source INDEX = product x subgroup)
  market  : therapy subgroup (primary market definition)
Methodology: docs/OPPORTUNITY_SCORING.md · parameters: opportunity_config.py
No LLM is involved anywhere in scoring or explanation.
"""
from __future__ import annotations

import datetime as dt
import math

from . import metrics
from .opportunity_config import DEFAULT_CONFIG, Component, OpportunityConfig
from .rankings import _r

SCORED = "SCORED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
LEVELS = ("product", "market")
REASONS = {
    "insufficient_history": "Comparison window predates the data; growth cannot be measured.",
    "prior_zero": "No sales in the comparison window (new or re-launched); growth is undefined.",
    "below_materiality": "Comparison-window value below the materiality floor; growth and relative momentum are unreliable.",
    "market_zero_current": "The market has no current sales; share cannot be computed.",
    "single_product_market": "Fewer than the minimum number of active products in the market; no competitive evidence.",
}
QUADRANTS = {
    (True, True): "Outperforming in faster-growing market",
    (True, False): "Outperforming in slower-growing market",
    (False, True): "Underperforming in faster-growing market",
    (False, False): "Underperforming in slower-growing market",
}
DATA_LIMITATIONS = [
    "Market = source therapy SUBGROUP (analytical definition).",
    "Units scale ('000) inferred; value in Rs crore confirmed.",
    "National data only (no geography/channel).",
]


# ---------------------------------------------------------------- building blocks
def percentile_normalize(values: dict, decimals: int = 9) -> dict:
    """Mid-rank percentile in [0, 1]: (average rank - 1) / (n - 1). Ties (after rounding to
    `decimals`) share the average rank. n == 1 -> 0.5. Robust to outliers by construction."""
    n = len(values)
    if n == 0:
        return {}
    if n == 1:
        return {k: 0.5 for k in values}
    order = sorted(values, key=lambda k: _r(values[k]))
    out, i = {}, 0
    while i < n:
        j = i
        v = _r(values[order[i]])
        while j + 1 < n and _r(values[order[j + 1]]) == v:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in order[i:j + 1]:
            out[k] = (avg_rank - 1) / (n - 1)
        i = j + 1
    return out


def aggregate_score(parts: list[tuple[float, float]]) -> float:
    """0..100 score = 100 * sum(weight * normalized)."""
    return 100.0 * math.fsum(w * n for w, n in parts)


def score_rank_key(row: dict):
    return (-_r(row["score"]), row["entity_key"].encode("utf-8"))


def directed(value: float, comp: Component) -> float:
    return value * comp.direction


# ---------------------------------------------------------------- inputs (validated metrics)
def _windows_ok(engine, anchor, basis, cfg):
    from .api import AnalyticsError
    from . import periods
    b = str(basis).upper()
    if b not in cfg.allowed_bases:
        raise AnalyticsError("invalid_parameter", f"opportunity basis must be one of {cfg.allowed_bases}")
    a = dt.date.fromisoformat(str(anchor)[:10]).replace(day=1)
    if not engine.first_period <= a <= engine.last_period:
        raise AnalyticsError("period_unavailable", f"anchor {a} outside data range")
    w = periods.make_window(a, b, engine.first_period)
    if not w.current_complete:
        raise AnalyticsError("period_unavailable", f"{b} window starts before the first data month")
    return a, b, w


def product_inputs(engine, anchor, basis, cfg: OpportunityConfig) -> list[dict]:
    ps = metrics.entity_period(engine, "product_subgroup", anchor, basis)
    mk = {r["entity_key"]: r for r in metrics.entity_period(engine, "subgroup", anchor, basis)}
    attrs = {}
    for p in engine.packs:
        attrs.setdefault(p["index_desc"], p)
    active = {}
    for r in ps:
        sg = attrs[r["entity_key"]]["subgroup"]
        if r["value_cur"] > 0 or (r["value_prior"] or 0) > 0:
            active[sg] = active.get(sg, 0) + 1
    rows = []
    for r in ps:
        p = attrs[r["entity_key"]]
        m = mk[p["subgroup"]]
        rows.append({
            "entity_level": "product", "entity_key": r["entity_key"], "entity_label": r["entity_label"],
            "prod_code": str(p["prod_code"]), "brand": p["brand"], "company": p["company"],
            "subgroup": p["subgroup"], "therapy_group": p["therapy_group"], "supergroup": p["supergroup"],
            "value_cur": r["value_cur"], "value_prior": r["value_prior"],
            "value_growth_pct": r["value_growth_pct"],
            "market_value_cur": m["value_cur"], "market_value_prior": m["value_prior"],
            "market_value_growth_pct": m["value_growth_pct"],
            "value_share_in_market_pct": metrics.share_pct(r["value_cur"], m["value_cur"]),
            "evolution_index": metrics.evolution_index(r["value_cur"], r["value_prior"], m["value_cur"], m["value_prior"]),
            "active_products_in_market": active.get(p["subgroup"], 0),
        })
    return rows


def market_inputs(engine, anchor, basis, cfg: OpportunityConfig) -> list[dict]:
    rows = []
    for r in metrics.entity_period(engine, "subgroup", anchor, basis):
        rows.append({"entity_level": "market", "entity_key": r["entity_key"], "entity_label": r["entity_label"],
                     "value_cur": r["value_cur"], "value_prior": r["value_prior"],
                     "value_growth_pct": r["value_growth_pct"],
                     "value_share_of_total_pct": r["value_share_pct"], "n_packs": r["n_packs"]})
    hier = {p["subgroup"]: p for p in engine.packs}
    for r in rows:
        h = hier[r["entity_key"]]
        r.update(therapy_group=h["therapy_group"], supergroup=h["supergroup"], acute_chronic=h["acute_chronic"])
    return rows


# ---------------------------------------------------------------- eligibility
def product_eligibility(r: dict, cfg: OpportunityConfig):
    if r["value_prior"] is None:
        return INSUFFICIENT, "insufficient_history"
    if r["value_prior"] <= 0:
        return INSUFFICIENT, "prior_zero"
    if r["value_prior"] < cfg.min_prior_value_cr:
        return INSUFFICIENT, "below_materiality"
    if not r["market_value_cur"] or r["market_value_cur"] <= 0:
        return INSUFFICIENT, "market_zero_current"
    if r["active_products_in_market"] < cfg.min_active_products:
        return INSUFFICIENT, "single_product_market"
    return SCORED, None


def market_eligibility(r: dict, cfg: OpportunityConfig):
    if r["value_prior"] is None:
        return INSUFFICIENT, "insufficient_history"
    if r["value_prior"] <= 0:
        return INSUFFICIENT, "prior_zero"
    if r["value_prior"] < cfg.min_prior_value_cr:
        return INSUFFICIENT, "below_materiality"
    return SCORED, None


# ---------------------------------------------------------------- scoring
def _fmt(metric, v):
    if v is None:
        return "n/a"
    if metric.endswith("_pct"):
        return f"{v:+.1f}%" if "growth" in metric else f"{v:.1f}%"
    return f"{v:.1f}"


def score_population(rows: list[dict], components: tuple, cfg: OpportunityConfig, eligibility) -> list[dict]:
    """Assign status, normalized components, score, rank, drivers and constraints (in place)."""
    for r in rows:
        r["score_status"], r["insufficient_reason"] = eligibility(r, cfg)
    scored = [r for r in rows if r["score_status"] == SCORED]
    norms = {c.name: percentile_normalize({r["entity_key"]: directed(r[c.metric], c) for r in scored},
                                          cfg.rank_decimals) for c in components}
    for r in rows:
        r["methodology_version"] = cfg.version
        if r["score_status"] != SCORED:
            r.update(score=None, opportunity_rank=None,
                     components=[{"name": c.name, "label": c.label, "metric": c.metric, "raw": r.get(c.metric),
                                  "normalized": None, "weight": c.weight, "weighted_contribution": None}
                                 for c in components],
                     positive_drivers=[], constraints=[REASONS[r["insufficient_reason"]]] + DATA_LIMITATIONS)
            continue
        comps = []
        for c in components:
            n = norms[c.name][r["entity_key"]]
            comps.append({"name": c.name, "label": c.label, "metric": c.metric, "raw": r[c.metric],
                          "normalized": n, "weight": c.weight, "weighted_contribution": 100.0 * c.weight * n})
        r["components"] = comps
        r["score"] = aggregate_score([(c["weight"], c["normalized"]) for c in comps])
        r["positive_drivers"] = [f"{c['label']}: {_fmt(c['metric'], c['raw'])} (percentile {c['normalized'] * 100:.0f})"
                                 for c in comps if c["normalized"] >= cfg.driver_threshold]
        r["constraints"] = [f"{c['label']}: {_fmt(c['metric'], c['raw'])} (percentile {c['normalized'] * 100:.0f})"
                            for c in comps if c["normalized"] <= cfg.constraint_threshold] + DATA_LIMITATIONS
    for i, r in enumerate(sorted(scored, key=score_rank_key), 1):
        r["opportunity_rank"] = i
    return rows


def matrix_quadrant(r: dict, national_growth: float | None, cfg: OpportunityConfig) -> str | None:
    """Product matrix: x = EI >= threshold (inclusive); y = market growth >= national growth (inclusive).
    Only SCORED products are classified (None otherwise)."""
    if r["score_status"] != SCORED or r["evolution_index"] is None or r["market_value_growth_pct"] is None \
            or national_growth is None:
        return None
    return QUADRANTS[(r["evolution_index"] >= cfg.matrix_ei_threshold,
                      r["market_value_growth_pct"] >= national_growth)]


def score_all(engine, level: str, anchor, basis, cfg: OpportunityConfig = DEFAULT_CONFIG) -> dict:
    """Score the full national reference population (cached per engine/anchor/basis/config)."""
    a, b, w = _windows_ok(engine, anchor, basis, cfg)
    cache = engine.__dict__.setdefault("_opportunity_cache", {})
    key = (level, a, b, cfg.fingerprint())
    if key in cache:
        return cache[key]
    national = metrics.entity_period(engine, "total", a, b)[0]["value_growth_pct"]
    if level == "product":
        rows = score_population(product_inputs(engine, a, b, cfg), cfg.product_components, cfg, product_eligibility)
        for r in rows:
            r["matrix_quadrant"] = matrix_quadrant(r, national, cfg)
    elif level == "market":
        rows = score_population(market_inputs(engine, a, b, cfg), cfg.market_components, cfg, market_eligibility)
    else:
        from .api import AnalyticsError
        raise AnalyticsError("invalid_parameter", f"level must be one of {LEVELS}")
    result = {"rows": rows, "window": w, "national_value_growth_pct": national}
    if len(cache) > 8:
        cache.pop(next(iter(cache)))
    cache[key] = result
    return result


def select_rows(rows: list[dict], level: str, market_level=None, market_key=None, company=None) -> list[dict]:
    """Filters select rows only; they never change the reference population or the scores."""
    out = rows
    if market_key is not None:
        out = [r for r in out if r.get(market_level) == market_key]
    if company is not None:
        out = [r for r in out if r.get("company") == company]
    return out


# ---------------------------------------------------------------- sensitivity (diagnostic)
def _avg_ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in order[i:j + 1]:
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float:
    ra, rb = _avg_ranks(a), _avg_ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return cov / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))


def sensitivity(engine, level="product", anchor="2024-05-01", basis="MAT", cfg=DEFAULT_CONFIG, delta=0.05, top_k=100):
    """Re-weight each component by +/- delta (others rescaled proportionally) and equal weights;
    report Spearman rank correlation with the baseline and top-k overlap. Normalized components
    do not depend on weights, so only the aggregation is recomputed."""
    rows = [r for r in score_all(engine, level, anchor, basis, cfg)["rows"] if r["score_status"] == SCORED]
    names = [c["name"] for c in rows[0]["components"]]
    base_w = {c["name"]: c["weight"] for c in rows[0]["components"]}
    norm = [{c["name"]: c["normalized"] for c in r["components"]} for r in rows]
    keys = [r["entity_key"] for r in rows]

    def scores(wts):
        return [aggregate_score([(wts[n], nr[n]) for n in names]) for nr in norm]

    def top(sc):
        return {keys[i] for i in sorted(range(len(sc)), key=lambda i: (-_r(sc[i]), keys[i].encode()))[:top_k]}

    base = scores(base_w)
    base_top = top(base)
    variants = []
    for n in names:
        for d in (-delta, delta):
            w = dict(base_w)
            new = min(max(w[n] + d, 0.0), 1.0)
            rest = 1.0 - w[n]
            w = {k: (new if k == n else v * (1.0 - new) / rest) for k, v in w.items()}
            variants.append((f"{n} {d:+.2f}", w))
    variants.append(("equal weights", {n: 1.0 / len(names) for n in names}))
    out = []
    for label, w in variants:
        sc = scores(w)
        out.append({"variant": label, "weights": {k: round(v, 4) for k, v in w.items()},
                    "spearman_vs_baseline": spearman(base, sc),
                    f"top{top_k}_overlap": len(base_top & top(sc)) / top_k})
    return {"level": level, "n_scored": len(rows), "variants": out}


# ---------------------------------------------------------------- API envelopes
PRODUCT_FILTER_LEVELS = ("supergroup", "therapy_group", "subgroup")
MARKET_FILTER_LEVELS = ("supergroup", "therapy_group")


def _methodology(cfg: OpportunityConfig, level: str, n_scored: int) -> dict:
    comps = cfg.product_components if level == "product" else cfg.market_components
    return {"version": cfg.version, "fingerprint": cfg.fingerprint(), "normalization": cfg.normalization,
            "reference_population": (f"all SCORED {'product-in-subgroup' if level == 'product' else 'subgroup market'} "
                                     "entities nationally for this anchor/basis (filters never change scores)"),
            "population_size": n_scored,
            "components": [{"name": c.name, "metric": c.metric, "weight": c.weight, "direction": c.direction,
                            "label": c.label} for c in comps],
            "thresholds": {"min_prior_value_cr": cfg.min_prior_value_cr,
                           "min_active_products": cfg.min_active_products,
                           "driver_threshold": cfg.driver_threshold, "constraint_threshold": cfg.constraint_threshold,
                           "matrix_ei_threshold": cfg.matrix_ei_threshold,
                           "matrix_market_growth_reference": cfg.matrix_market_growth_reference}}


def _validate_filters(engine, level, market_level, market_key, company):
    from .api import AnalyticsError, UNSUPPORTED
    if level not in LEVELS:
        raise AnalyticsError("invalid_parameter", f"level must be one of {LEVELS}")
    if market_key is not None or market_level is not None:
        allowed = PRODUCT_FILTER_LEVELS if level == "product" else MARKET_FILTER_LEVELS
        ml = str(market_level).lower()
        if ml in UNSUPPORTED:
            raise AnalyticsError("unsupported", UNSUPPORTED[ml])
        if ml not in allowed or market_key is None:
            raise AnalyticsError("invalid_parameter", f"market_level must be one of {allowed} with a market_key")
        if not engine.has_key(ml, market_key):
            raise AnalyticsError("not_found", f"no {ml} with key {market_key!r}")
        market_level = ml
    if company is not None:
        if level != "product":
            raise AnalyticsError("invalid_parameter", "company filter applies to level='product' only")
        if not engine.has_key("company", company):
            raise AnalyticsError("not_found", f"no company with key {company!r}")
    return market_level


def _period(w):
    return {"anchor": w.anchor.isoformat(), "basis": w.basis, "basis_label": w.basis_label,
            "cur_start": w.cur_start.isoformat(), "cur_end": w.cur_end.isoformat(),
            "prior_complete": w.prior_complete,
            "prior_start": w.prior_start.isoformat() if w.prior_complete else None,
            "prior_end": w.prior_end.isoformat() if w.prior_complete else None}


def _caveats(w, rows_selected):
    c = ["Opportunity score is a relative, descriptive ranking of evidence (percentiles within the national "
         "reference population); it is not a forecast or a probability.",
         "Component weights are methodology assumptions (docs/OPPORTUNITY_SCORING.md)."]
    if not w.prior_complete:
        c.append("Comparison window predates the data: no entity can be scored for this anchor/basis.")
    if not rows_selected:
        c.append("No rows matched the request.")
    return c


def scores_envelope(engine, level="product", anchor="2024-05-01", basis="MAT", market_level=None, market_key=None,
                    company=None, include_insufficient=False, top_n=50, cfg: OpportunityConfig = DEFAULT_CONFIG):
    from .api import AnalyticsError
    if top_n is not None and (not isinstance(top_n, int) or not 1 <= top_n <= 5000):
        raise AnalyticsError("invalid_parameter", "top_n must be an integer 1..5000 or None")
    level = str(level).lower()
    market_level = _validate_filters(engine, level, market_level, market_key, company)
    res = score_all(engine, level, anchor, basis, cfg)
    rows = select_rows(res["rows"], level, market_level, market_key, company)
    status_counts = {SCORED: sum(r["score_status"] == SCORED for r in rows),
                     INSUFFICIENT: sum(r["score_status"] == INSUFFICIENT for r in rows)}
    scored = sorted([r for r in rows if r["score_status"] == SCORED], key=score_rank_key)
    ranked = [dict(r, rank_in_selection=i) for i, r in enumerate(scored, 1)]
    if include_insufficient:
        ranked += [dict(r, rank_in_selection=None) for r in
                   sorted([r for r in rows if r["score_status"] != SCORED], key=lambda r: r["entity_key"].encode())]
    total = len(ranked)
    out_rows = ranked[:top_n] if top_n else ranked
    n_pop = sum(r["score_status"] == SCORED for r in res["rows"])
    return {"function": "get_opportunity_scores",
            "filters": {"level": level, "market_level": market_level, "market_key": market_key, "company": company,
                        "include_insufficient": include_insufficient},
            "period": _period(res["window"]),
            "methodology": _methodology(cfg, level, n_pop),
            "national_value_growth_pct": res["national_value_growth_pct"],
            "status_counts": status_counts, "row_count": len(out_rows), "total_rows": total, "rows": out_rows,
            "evidence": {"engine": "python (canonical scoring)", "inputs": "metrics.entity_period (M5-validated)",
                         "definitions": ["docs/OPPORTUNITY_SCORING.md"]},
            "caveats": _caveats(res["window"], out_rows)}


def detail_envelope(engine, level, entity_key, anchor="2024-05-01", basis="MAT", cfg: OpportunityConfig = DEFAULT_CONFIG):
    from .api import AnalyticsError
    level = str(level).lower()
    _validate_filters(engine, level, None, None, None)
    res = score_all(engine, level, anchor, basis, cfg)
    row = next((r for r in res["rows"] if r["entity_key"] == str(entity_key)), None)
    if row is None:
        raise AnalyticsError("not_found", f"no {level} opportunity entity with key {entity_key!r}"
                             + (" (product level uses the product-in-subgroup key, see find_products / INDEX)"
                                if level == "product" else ""))
    n_pop = sum(r["score_status"] == SCORED for r in res["rows"])
    return {"function": "get_opportunity_detail", "filters": {"level": level, "entity_key": str(entity_key)},
            "period": _period(res["window"]), "methodology": _methodology(cfg, level, n_pop),
            "national_value_growth_pct": res["national_value_growth_pct"],
            "row_count": 1, "total_rows": 1, "rows": [dict(row)],
            "evidence": {"engine": "python (canonical scoring)", "inputs": "metrics.entity_period (M5-validated)",
                         "definitions": ["docs/OPPORTUNITY_SCORING.md"]},
            "caveats": _caveats(res["window"], [row])}
