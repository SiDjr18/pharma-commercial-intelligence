"""SQL <-> independent Python cross-validation framework (M5).

Tolerance policy (docs/PYTHON_ANALYTICS.md):
- STRUCTURAL fields (keys, labels, types, dates, statuses, counts, ranks): exact equality,
  including NULL-ness. n_packs is exact, so a dropped or duplicated pack can never hide inside a
  numeric tolerance.
- NUMERIC fields: |sql - py| <= ABS_TOL + REL_TOL * |py|, with NULL-ness exact.
  REL_TOL = 1e-9 is ~1000x the observed SQL run-to-run noise (~1e-12 relative, parallel float
  summation) and far below any business-relevant difference at entity level.
- Near-zero results produced by cancellation (e.g. cur == prior => abs change / growth ~ 0) differ
  only in the last bits (observed <= 6e-14 pp); ABS_TOL governs those. Reported max_rel_diff
  therefore ignores |value| < REL_REPORT_FLOOR; max_abs_diff is reported for all values.

Run the matrix:  .venv\\Scripts\\python.exe -m pci_analytics.validation   (from python/)
It prints pass/fail and max differences only — never values.
"""
from __future__ import annotations

import datetime as dt
import math
import time
from dataclasses import dataclass, field

from . import metrics

REL_TOL = 1e-9
ABS_TOL = 1e-9
REL_REPORT_FLOOR = 1e-6  # report relative diffs only for |value| >= this

PERIOD_STRUCTURAL = ["entity_type", "entity_key", "entity_label", "scope_type", "scope_key", "anchor", "basis",
                     "basis_label", "cur_start", "cur_end", "prior_start", "prior_end", "n_packs",
                     "value_growth_status", "rank_value", "rank_growth", "n_entities"]
PERIOD_NUMERIC = ["value_cur", "value_prior", "value_abs_chg", "value_growth_pct", "units_cur", "units_prior",
                  "units_abs_chg", "units_growth_pct", "qty_cur", "qty_prior", "qty_growth_pct",
                  "scope_value_cur", "scope_value_prior", "value_share_pct", "value_share_prior_pct",
                  "value_share_chg_pp", "units_share_pct", "contribution_to_growth_pp", "evolution_index"]
TREND_STRUCTURAL = ["entity_type", "entity_key", "scope_type", "scope_key", "period", "period_index"]
TREND_NUMERIC = ["value_cr", "value_cr_prior", "value_growth_pct", "units_k", "units_k_prior", "units_growth_pct",
                 "value_ytd", "value_ytd_prior", "value_ytd_growth_pct", "value_mat", "value_mat_prior",
                 "value_mat_growth_pct", "units_mat", "units_mat_prior", "units_mat_growth_pct"]


@dataclass
class CheckResult:
    name: str
    n_rows_sql: int = 0
    n_rows_py: int = 0
    n_values: int = 0
    mismatches: list = field(default_factory=list)
    max_abs_diff: float = 0.0
    max_rel_diff: float = 0.0
    seconds_sql: float = 0.0
    seconds_py: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.mismatches and self.n_rows_sql == self.n_rows_py

    def summary(self) -> str:
        return (f"{'PASS' if self.passed else 'FAIL'}  {self.name:<58} rows={self.n_rows_py:>6} "
                f"values={self.n_values:>8} max_rel={self.max_rel_diff:.1e} max_abs={self.max_abs_diff:.1e} "
                f"sql={self.seconds_sql:.2f}s py={self.seconds_py:.2f}s"
                + (f"  mismatches={len(self.mismatches)}" if self.mismatches else ""))


def compare_rows(name, sql_rows, py_rows, key_fields, structural, numeric) -> CheckResult:
    res = CheckResult(name, len(sql_rows), len(py_rows))
    key = lambda r: tuple(r[k] for k in key_fields)  # noqa: E731
    s = {key(r): r for r in sql_rows}
    p = {key(r): r for r in py_rows}
    if len(s) != len(sql_rows) or len(p) != len(py_rows):
        res.mismatches.append(("duplicate keys", len(s), len(p)))
    for k in s.keys() ^ p.keys():
        res.mismatches.append(("row only in " + ("sql" if k in s else "python"), k))
    for k in s.keys() & p.keys():
        a, b = s[k], p[k]
        for f in structural:
            res.n_values += 1
            if a[f] != b[f]:
                res.mismatches.append((k, f, a[f], b[f]))
        for f in numeric:
            res.n_values += 1
            x, y = a[f], b[f]
            if x is None or y is None:
                if not (x is None and y is None):
                    res.mismatches.append((k, f, x, y))
                continue
            d = abs(x - y)
            res.max_abs_diff = max(res.max_abs_diff, d)
            if abs(y) >= REL_REPORT_FLOOR:   # relative error is meaningless near zero (cancellation)
                res.max_rel_diff = max(res.max_rel_diff, d / abs(y))
            if not d <= ABS_TOL + REL_TOL * abs(y) or math.isnan(d):
                res.mismatches.append((k, f, x, y))
    return res


def _sql_rows(con, sql, params):
    cur = con.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def crosscheck_period(con, engine, etype, anchor, basis, scope_type="total", scope_key="TOTAL", name=None):
    anchor = dt.date.fromisoformat(str(anchor))
    t = time.perf_counter()
    sql = _sql_rows(con, "SELECT * FROM entity_period(?, ?, ?, ?, ?)", [etype, anchor, basis, scope_type, scope_key])
    t_sql = time.perf_counter() - t
    t = time.perf_counter()
    py = metrics.entity_period(engine, etype, anchor, basis, scope_type, scope_key)
    t_py = time.perf_counter() - t
    res = compare_rows(name or f"{etype} {basis} {anchor} in {scope_type}", sql, py, ["entity_key"],
                       PERIOD_STRUCTURAL, PERIOD_NUMERIC)
    res.seconds_sql, res.seconds_py = t_sql, t_py
    return res


def crosscheck_trend(con, engine, etype, key, scope_type="total", scope_key="TOTAL", name=None):
    t = time.perf_counter()
    sql = _sql_rows(con, "SELECT * FROM entity_trend(?, ?, ?, ?)", [etype, key, scope_type, scope_key])
    t_sql = time.perf_counter() - t
    t = time.perf_counter()
    py = metrics.entity_trend(engine, etype, key, scope_type, scope_key)
    t_py = time.perf_counter() - t
    res = compare_rows(name or f"trend {etype} in {scope_type}", sql, py, ["period"], TREND_STRUCTURAL, TREND_NUMERIC)
    res.seconds_sql, res.seconds_py = t_sql, t_py
    return res


# ---------------------------------------------------------------- validation matrix
# Scope/entity keys starting with '@' are resolved at run time (no real names in the repository):
#   '@rank:<type>:<n>'  -> key of the n-th largest <type> by MAT value at the last month (Python engine)
#   '@scopetop'         -> (trends) largest entity of the type inside the given scope
def resolve(engine, token):
    if not isinstance(token, str) or not token.startswith("@rank:"):
        return token
    _, etype, n = token.split(":")
    rows = metrics.entity_period(engine, etype, engine.last_period, "MAT")
    return next(r["entity_key"] for r in rows if r["rank_value"] == int(n))


L = "2024-05-01"
PERIOD_MATRIX = [
    # domain            entity_type          anchor        basis    scope_type     scope_key
    ("total",           "total",             L,            "MONTH", "total", "TOTAL"),
    ("total",           "total",             L,            "YTD",   "total", "TOTAL"),
    ("total",           "total",             L,            "MAT",   "total", "TOTAL"),
    ("total first month",       "total",     "2021-06-01", "MONTH", "total", "TOTAL"),
    ("total first YTD",         "total",     "2022-01-01", "YTD",   "total", "TOTAL"),
    ("total first MAT",         "total",     "2022-05-01", "MAT",   "total", "TOTAL"),
    ("total first MONTH growth", "total",    "2022-06-01", "MONTH", "total", "TOTAL"),
    ("total first YTD growth",  "total",     "2023-01-01", "YTD",   "total", "TOTAL"),
    ("total first MAT growth",  "total",     "2023-05-01", "MAT",   "total", "TOTAL"),
    ("therapy area",    "supergroup",        L,            "MONTH", "total", "TOTAL"),
    ("therapy area",    "supergroup",        L,            "YTD",   "total", "TOTAL"),
    ("therapy area",    "supergroup",        L,            "MAT",   "total", "TOTAL"),
    ("therapy area",    "supergroup",        "2023-12-01", "YTD",   "total", "TOTAL"),
    ("therapy group",   "therapy_group",     L,            "MONTH", "total", "TOTAL"),
    ("therapy group",   "therapy_group",     L,            "MAT",   "total", "TOTAL"),
    ("market/subgroup", "subgroup",          L,            "MONTH", "total", "TOTAL"),
    ("market/subgroup", "subgroup",          L,            "YTD",   "total", "TOTAL"),
    ("market/subgroup", "subgroup",          L,            "MAT",   "total", "TOTAL"),
    ("market/subgroup", "subgroup",          "2023-05-01", "MAT",   "total", "TOTAL"),
    ("molecule market", "molecule",          L,            "MAT",   "total", "TOTAL"),
    ("molecule market", "molecule",          "2022-06-01", "MONTH", "total", "TOTAL"),
    ("company",         "company",           L,            "MONTH", "total", "TOTAL"),
    ("company",         "company",           L,            "YTD",   "total", "TOTAL"),
    ("company",         "company",           L,            "MAT",   "total", "TOTAL"),
    ("manufacturer",    "manufacturer",      L,            "MAT",   "total", "TOTAL"),
    ("product",         "product",           L,            "MONTH", "total", "TOTAL"),
    ("product",         "product",           L,            "YTD",   "total", "TOTAL"),
    ("product",         "product",           L,            "MAT",   "total", "TOTAL"),
    ("product",         "product",           "2022-06-01", "MONTH", "total", "TOTAL"),
    ("product in market", "product_subgroup", L,           "MAT",   "total", "TOTAL"),
    ("segment",         "acute_chronic",     L,            "MONTH", "total", "TOTAL"),
    ("segment",         "acute_chronic",     L,            "YTD",   "total", "TOTAL"),
    ("segment",         "acute_chronic",     L,            "MAT",   "total", "TOTAL"),
    ("segment",         "indian_mnc",        L,            "MAT",   "total", "TOTAL"),
    ("segment",         "plain_combination", L,            "MAT",   "total", "TOTAL"),
    ("segment",         "molecule_count",    L,            "MAT",   "total", "TOTAL"),
    ("segment",         "dosage_form",       L,            "YTD",   "total", "TOTAL"),
    ("segment",         "nfc1",              L,            "MONTH", "total", "TOTAL"),
    ("brand in market", "product",           L,            "MONTH", "subgroup", "@rank:subgroup:1"),
    ("brand in market", "product",           L,            "YTD",   "subgroup", "@rank:subgroup:1"),
    ("brand in market", "product",           L,            "MAT",   "subgroup", "@rank:subgroup:1"),
    ("brand in market", "product",           L,            "MAT",   "molecule", "@rank:molecule:1"),
    ("brand in company", "product",          L,            "MAT",   "company", "@rank:company:1"),
    ("company in market", "company",         L,            "MAT",   "supergroup", "@rank:supergroup:2"),
    ("company in market", "company",         "2023-05-01", "MAT",   "subgroup", "@rank:subgroup:5"),
    ("subgroup in area", "subgroup",         L,            "YTD",   "supergroup", "@rank:supergroup:1"),
    ("segment in market", "plain_combination", L,          "MAT",   "subgroup", "@rank:subgroup:3"),
    ("segment in market", "dosage_form",     L,            "MAT",   "company", "@rank:company:2"),
]
TREND_MATRIX = [
    ("total trend",            "total",    "TOTAL",             "total", "TOTAL"),
    ("market trend",           "subgroup", "@rank:subgroup:1",  "total", "TOTAL"),
    ("therapy area trend",     "supergroup", "@rank:supergroup:3", "total", "TOTAL"),
    ("company trend",          "company",  "@rank:company:1",   "total", "TOTAL"),
    ("product trend",          "product",  "@rank:product:1",   "total", "TOTAL"),
    ("product-in-market trend", "product", "@scopetop",         "subgroup", "@rank:subgroup:1"),
]


def run_matrix(con, engine, period_matrix=PERIOD_MATRIX, trend_matrix=TREND_MATRIX) -> list[CheckResult]:
    out = []
    for domain, etype, anchor, basis, st, sk in period_matrix:
        out.append(crosscheck_period(con, engine, etype, anchor, basis, st, resolve(engine, sk),
                                     name=f"{domain}: {etype} {basis} {anchor}" + ("" if st == "total" else f" in {st}")))
    for domain, etype, key, st, sk in trend_matrix:
        s = resolve(engine, sk)
        # '@scopetop' = largest entity of etype within the scope (so the entity sells there)
        k = (next(r["entity_key"] for r in metrics.entity_period(engine, etype, engine.last_period, "MAT", st, s)
                  if r["rank_value"] == 1) if key == "@scopetop" else resolve(engine, key))
        out.append(crosscheck_trend(con, engine, etype, k, st, s, name=f"{domain}: {etype}"))
    return out


def main():  # pragma: no cover - CLI
    from pci_data.db import connect
    from .engine import DataEngine
    t = time.perf_counter()
    engine = DataEngine()
    print(f"python engine load: {time.perf_counter() - t:.2f}s")
    results = run_matrix(connect(), engine)
    for r in results:
        print(r.summary())
    n_fail = sum(not r.passed for r in results)
    print(f"\n{len(results) - n_fail}/{len(results)} checks passed; "
          f"{sum(r.n_values for r in results):,} values compared; "
          f"max relative difference {max(r.max_rel_diff for r in results):.1e} (|v| >= {REL_REPORT_FLOOR:g}); "
          f"max absolute difference {max(r.max_abs_diff for r in results):.1e}")
    raise SystemExit(1 if n_fail else 0)


if __name__ == "__main__":  # pragma: no cover
    main()
