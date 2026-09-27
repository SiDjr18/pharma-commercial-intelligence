"""Independent Python implementation of the M4 metric definitions (no SQL).

Pure metric functions (growth, share, contribution, evolution index) are defined once here and
used by `entity_period` / `entity_trend`, which mirror the SQL macros of the same names
(sql/metrics.sql) field for field. Sums use math.fsum (exactly rounded), so any SQL/Python
difference is floating-point noise on the SQL side (parallel summation), bounded by the
tolerance policy in docs/PYTHON_ANALYTICS.md.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

from . import periods, rankings
from .engine import DataEngine

# ---------------------------------------------------------------- pure metric functions
def growth_pct(cur: float | None, prior: float | None) -> float | None:
    """Source pivot formula: cur / prior * 100 - 100; undefined (None) unless prior > 0."""
    if cur is None or prior is None or prior <= 0:
        return None
    return cur / prior * 100 - 100


def growth_status(cur: float | None, prior: float | None) -> str:
    if prior is None:
        return "prior_unavailable"
    if prior == 0 and cur == 0:
        return "no_sales"
    if prior == 0:
        return "prior_zero"
    return "ok"


def share_pct(part: float | None, total: float | None) -> float | None:
    if part is None or total is None or total <= 0:
        return None
    return part / total * 100


def contribution_pp(cur: float | None, prior: float | None, scope_prior: float | None) -> float | None:
    """(cur - prior) / scope prior * 100 — sums to the scope's growth."""
    if cur is None or prior is None or scope_prior is None or scope_prior <= 0:
        return None
    return (cur - prior) / scope_prior * 100


def evolution_index(cur, prior, scope_cur, scope_prior) -> float | None:
    """share_cur / share_prior * 100 (= 100 * (1 + g_entity) / (1 + g_scope))."""
    if prior is None or prior <= 0 or not scope_prior or scope_prior <= 0 or not scope_cur or scope_cur <= 0:
        return None
    return (cur / scope_cur) / (prior / scope_prior) * 100


def share_denominator(entity_totals: list[float]) -> float:
    """Scope total = sum over all entities in the (partitioned) scope."""
    return math.fsum(entity_totals)


def comparison_window(w: periods.Window) -> tuple[dt.date, dt.date]:
    return w.prior_start, w.prior_end


def scope_members(engine: DataEngine, scope_type: str, scope_key: str) -> list[dict]:
    return engine.scope_packs(scope_type, scope_key)


# ---------------------------------------------------------------- entity_period
def entity_period(engine: DataEngine, entity_type: str, anchor: dt.date, basis: str,
                  scope_type: str = "total", scope_key: str = "TOTAL") -> list[dict]:
    """Mirror of SQL entity_period(): one row per entity of `entity_type` within the scope.

    Returns [] when the current window is incomplete or the scope has no packs."""
    w = periods.make_window(anchor, basis, engine.first_period)
    if not w.current_complete or w.anchor > engine.last_period:
        return []
    packs = scope_members(engine, scope_type, str(scope_key))
    if not packs:
        return []
    cur = engine.pack_sums(w.cur_start, w.cur_end)
    p_start, p_end = comparison_window(w)
    pri = engine.pack_sums(p_start, p_end) if w.prior_complete else None

    acc = defaultdict(lambda: {"label": None, "n": 0, "v": [], "vp": [], "u": [], "up": [], "q": [], "qp": []})
    for p in packs:
        a = acc[engine.key(entity_type, p)]
        if a["label"] is None:
            a["label"] = engine.label(entity_type, p)
        a["n"] += 1
        c = cur.get(p["pfc"], (0.0, 0.0, 0.0))
        a["v"].append(c[0]); a["u"].append(c[1]); a["q"].append(c[2])
        if pri is not None:
            o = pri.get(p["pfc"], (0.0, 0.0, 0.0))
            a["vp"].append(o[0]); a["up"].append(o[1]); a["qp"].append(o[2])

    base = []
    for k, a in acc.items():
        prior_ok = pri is not None
        base.append({"entity_key": k, "entity_label": a["label"], "n_packs": a["n"],
                     "value_cur": math.fsum(a["v"]), "units_cur": math.fsum(a["u"]), "qty_cur": math.fsum(a["q"]),
                     "value_prior": math.fsum(a["vp"]) if prior_ok else None,
                     "units_prior": math.fsum(a["up"]) if prior_ok else None,
                     "qty_prior": math.fsum(a["qp"]) if prior_ok else None})

    t_vc = share_denominator([r["value_cur"] for r in base])
    t_uc = share_denominator([r["units_cur"] for r in base])
    t_vp = share_denominator([r["value_prior"] for r in base]) if pri is not None else None

    rows = []
    for r in base:
        vc, vp, uc, up, qc, qp = (r["value_cur"], r["value_prior"], r["units_cur"], r["units_prior"],
                                  r["qty_cur"], r["qty_prior"])
        s_cur, s_pri = share_pct(vc, t_vc), share_pct(vp, t_vp)
        rows.append({
            "entity_type": entity_type, "entity_key": r["entity_key"], "entity_label": r["entity_label"],
            "scope_type": scope_type, "scope_key": str(scope_key),
            "anchor": w.anchor, "basis": w.basis, "basis_label": w.basis_label,
            "cur_start": w.cur_start, "cur_end": w.cur_end,
            "prior_start": w.prior_start if w.prior_complete else None,
            "prior_end": w.prior_end if w.prior_complete else None,
            "n_packs": r["n_packs"],
            "value_cur": vc, "value_prior": vp,
            "value_abs_chg": None if vp is None else vc - vp,
            "value_growth_pct": growth_pct(vc, vp), "value_growth_status": growth_status(vc, vp),
            "units_cur": uc, "units_prior": up,
            "units_abs_chg": None if up is None else uc - up,
            "units_growth_pct": growth_pct(uc, up),
            "qty_cur": qc, "qty_prior": qp, "qty_growth_pct": growth_pct(qc, qp),
            "scope_value_cur": t_vc, "scope_value_prior": t_vp,
            "value_share_pct": s_cur, "value_share_prior_pct": s_pri,
            "value_share_chg_pp": None if s_cur is None or s_pri is None else s_cur - s_pri,
            "units_share_pct": share_pct(uc, t_uc),
            "contribution_to_growth_pp": contribution_pp(vc, vp, t_vp),
            "evolution_index": evolution_index(vc, vp, t_vc, t_vp),
        })
    return rankings.assign_ranks(rows)


# ---------------------------------------------------------------- entity_trend
def entity_trend(engine: DataEngine, entity_type: str, key: str,
                 scope_type: str = "total", scope_key: str = "TOTAL") -> list[dict]:
    """Mirror of SQL entity_trend(): dense monthly series with month-YoY, Calendar YTD and MAT."""
    scope = {p["pfc"] for p in scope_members(engine, scope_type, str(scope_key))}
    members = {p["pfc"] for p in engine.packs
               if p["pfc"] in scope and engine.key(entity_type, p) == str(key)}
    if not members:
        return []
    m = engine.monthly_series(members)
    months = engine.periods
    v = [m.get(p, (0.0, 0.0))[0] for p in months]
    u = [m.get(p, (0.0, 0.0))[1] for p in months]
    n = len(months)

    def mat(series, i):
        return math.fsum(series[i - 11:i + 1]) if i >= 11 else None

    def ytd(series, i):
        if not periods.ytd_available(months[i], engine.first_period):
            return None
        return math.fsum(series[j] for j in range(i + 1) if months[j].year == months[i].year)

    v_mat = [mat(v, i) for i in range(n)]
    u_mat = [mat(u, i) for i in range(n)]
    v_ytd = [ytd(v, i) for i in range(n)]
    out = []
    for i, p in enumerate(months):
        j = i - 12
        prev = (lambda s: s[j] if j >= 0 else None)
        out.append({
            "entity_type": entity_type, "entity_key": str(key), "scope_type": scope_type, "scope_key": str(scope_key),
            "period": p, "period_index": i + 1,
            "value_cr": v[i], "value_cr_prior": prev(v), "value_growth_pct": growth_pct(v[i], prev(v)),
            "units_k": u[i], "units_k_prior": prev(u), "units_growth_pct": growth_pct(u[i], prev(u)),
            "value_ytd": v_ytd[i], "value_ytd_prior": prev(v_ytd),
            "value_ytd_growth_pct": growth_pct(v_ytd[i], prev(v_ytd)),
            "value_mat": v_mat[i], "value_mat_prior": prev(v_mat),
            "value_mat_growth_pct": growth_pct(v_mat[i], prev(v_mat)),
            "units_mat": u_mat[i], "units_mat_prior": prev(u_mat),
            "units_mat_growth_pct": growth_pct(u_mat[i], prev(u_mat)),
        })
    return out
