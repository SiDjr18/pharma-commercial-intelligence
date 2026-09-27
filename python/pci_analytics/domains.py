"""Python-engine equivalents of the SQL-backed API (python/pci_analytics/api.py).

Same function names, parameters, validation rules, error codes and response envelope, but every
number is computed by the independent Python engine (metrics.py). The SQL-backed
CommercialAnalytics remains the production API; this class exists for independent validation
and as a fallback reference implementation.
"""
from __future__ import annotations

import datetime as dt

from . import metrics, periods
from .api import (BASES, CommercialAnalytics, COMPANY_SCOPES, MARKET_LEVELS, PRODUCT_SCOPES, SEGMENTS, STANDARD_CAVEATS,
                  THERAPY_LEVELS, UNITS, UNSUPPORTED, AnalyticsError)
from .engine import DataEngine, default_engine


def _iso(v):
    return v.isoformat() if isinstance(v, dt.date) else v


class PyCommercialAnalytics:
    # opportunity scoring has ONE implementation (opportunity.py); reuse the SQL API's thin wrappers
    get_opportunity_scores = CommercialAnalytics.get_opportunity_scores
    get_opportunity_detail = CommercialAnalytics.get_opportunity_detail
    _opportunity_engine = CommercialAnalytics._opportunity_engine
    _python_engine = CommercialAnalytics._python_engine
    run_scenario = CommercialAnalytics.run_scenario
    get_scenario_baseline = CommercialAnalytics.get_scenario_baseline

    def __init__(self, engine: DataEngine | None = None):
        self.engine = engine or default_engine()
        self._opp_engine = self.engine
        self.dim_product = {}
        for p in self.engine.packs:
            self.dim_product.setdefault(p["prod_code"], p)

    # ---------------- validation (independent of SQL) ----------------
    def _window(self, anchor, basis):
        b = str(basis).upper()
        if b not in BASES:
            raise AnalyticsError("invalid_parameter", f"basis must be one of {BASES}")
        try:
            a = dt.date.fromisoformat(str(anchor)[:10]).replace(day=1)
        except ValueError as e:
            raise AnalyticsError("invalid_parameter", f"anchor must be an ISO date: {anchor!r}") from e
        e = self.engine
        if not e.first_period <= a <= e.last_period:
            raise AnalyticsError("period_unavailable", f"anchor {a} outside data range {e.first_period}..{e.last_period}")
        w = periods.make_window(a, b, e.first_period)
        if not w.current_complete:
            raise AnalyticsError("period_unavailable",
                                 f"{b} window {w.cur_start}..{w.cur_end} starts before the first data month")
        return w

    def _type(self, value, allowed, what):
        v = str(value).lower()
        if v in UNSUPPORTED:
            raise AnalyticsError("unsupported", UNSUPPORTED[v])
        if v not in allowed:
            raise AnalyticsError("invalid_parameter", f"{what} must be one of {allowed}")
        return v

    def _key(self, etype, key):
        if not self.engine.has_key(etype, key):
            raise AnalyticsError("not_found", f"no {etype} with key {str(key)!r}")
        return str(key)

    def _scope(self, st, sk, allowed):
        st = self._type(st, allowed, "scope/market level")
        return (st, "TOTAL") if st == "total" else (st, self._key(st, sk))

    @staticmethod
    def _top(top_n):
        if top_n is not None and (not isinstance(top_n, int) or not 1 <= top_n <= 5000):
            raise AnalyticsError("invalid_parameter", "top_n must be an integer 1..5000 or None")
        return top_n

    def _envelope(self, function, rows, w=None, filters=None, caveats=(), total_rows=None, call=None):
        out = {
            "function": function, "filters": filters or {},
            "period": None if w is None else {
                "anchor": _iso(w.anchor), "basis": w.basis, "basis_label": w.basis_label,
                "cur_start": _iso(w.cur_start), "cur_end": _iso(w.cur_end), "prior_complete": w.prior_complete,
                "prior_start": _iso(w.prior_start) if w.prior_complete else None,
                "prior_end": _iso(w.prior_end) if w.prior_complete else None},
            "units": UNITS, "row_count": len(rows),
            "total_rows": len(rows) if total_rows is None else total_rows,
            "rows": [{k: _iso(v) for k, v in r.items()} for r in rows],
            "evidence": {"engine": "python", "call": call, "definitions": ["docs/PYTHON_ANALYTICS.md",
                                                                          "docs/SQL_ANALYTICS.md"]},
            "caveats": list(STANDARD_CAVEATS) + list(caveats),
        }
        if w is not None and not w.prior_complete:
            out["caveats"].append("Comparison window predates the data (starts 2021-06); growth and prior values are NULL.")
        if not rows:
            out["caveats"].append("No rows matched the request.")
        return out

    def _ranked(self, function, rows, w, filters, top_n, caveats=(), row_filter=None, call=None):
        if row_filter:
            rows = [r for r in rows if row_filter(r)]
        rows = sorted(rows, key=lambda r: r["rank_value"])
        total = len(rows)
        return self._envelope(function, rows[:top_n] if top_n else rows, w, filters, caveats, total, call)

    def _with_product(self, rows):
        for r in rows:
            p = self.dim_product[int(r["entity_key"])]
            r.update(brand=p["brand"], company=p["company"], manufacturer_code=p["manufacturer_code"],
                     prod_launch_month=p["prod_launch_month"])
        return rows

    # ---------------- functions ----------------
    def find_products(self, name_contains: str, limit: int = 50):
        if not name_contains or len(str(name_contains).strip()) < 2:
            raise AnalyticsError("invalid_parameter", "name_contains needs at least 2 characters")
        needle = str(name_contains).strip().lower()
        rows = [{"prod_code": pc, "brand": p["brand"], "company": p["company"],
                 "manufacturer_code": p["manufacturer_code"], "prod_launch_month": p["prod_launch_month"]}
                for pc, p in self.dim_product.items() if needle in p["brand"].lower()]
        rows.sort(key=lambda r: (r["brand"].encode(), r["company"].encode(), r["prod_code"]))
        return self._envelope("find_products", rows[:int(limit)], filters={"name_contains": name_contains})

    def get_market_performance(self, level="subgroup", anchor="2024-05-01", basis="MAT", top_n=None):
        level = self._type(level, MARKET_LEVELS, "level")
        w = self._window(anchor, basis)
        rows = metrics.entity_period(self.engine, level, w.anchor, w.basis)
        return self._ranked("get_market_performance", rows, w, {"level": level}, self._top(top_n),
                            call=("entity_period", level, w.anchor, w.basis))

    def get_market_trends(self, level="total", key="TOTAL"):
        level = self._type(level, MARKET_LEVELS, "level")
        key = "TOTAL" if level == "total" else self._key(level, key)
        return self._envelope("get_market_trends", metrics.entity_trend(self.engine, level, key),
                              filters={"level": level, "key": key}, call=("entity_trend", level, key),
                              caveats=["Month-level growth compares with the same month a year earlier; "
                                       "YTD from 2022, MAT from 2022-05, MAT growth from 2023-05."])

    def get_brand_performance(self, anchor="2024-05-01", basis="MAT", market_level="total", market_key="TOTAL",
                              company=None, top_n=20):
        w = self._window(anchor, basis)
        st, sk = self._scope(market_level, market_key, PRODUCT_SCOPES)
        filters = {"market_level": st, "market_key": sk}
        flt = None
        if company is not None:
            self._key("company", company)
            filters["company"] = company
            flt = (lambda r: r["company"] == company)
        rows = self._with_product(metrics.entity_period(self.engine, "product", w.anchor, w.basis, st, sk))
        return self._ranked("get_brand_performance", rows, w, filters, self._top(top_n),
                            ["Analytical key is prod_code; brand is a display label (names are not unique).",
                             "Shares and ranks are within the market scope; a product can appear in several subgroup markets."],
                            flt, call=("entity_period", "product", w.anchor, w.basis, st, sk))

    def get_brand_growth(self, prod_code, market_level="total", market_key="TOTAL"):
        pc = self._key("product", prod_code)
        st, sk = self._scope(market_level, market_key, PRODUCT_SCOPES)
        rows = metrics.entity_trend(self.engine, "product", pc, st, sk)
        if not rows:
            raise AnalyticsError("not_found", f"product {pc} has no packs in {st}={sk}")
        return self._envelope("get_brand_growth", rows, filters={"prod_code": pc, "market_level": st, "market_key": sk},
                              call=("entity_trend", "product", pc, st, sk))

    def get_brand_share(self, prod_code, market_level, market_key, anchor="2024-05-01", basis="MAT"):
        pc = self._key("product", prod_code)
        w = self._window(anchor, basis)
        st, sk = self._scope(market_level, market_key, PRODUCT_SCOPES)
        rows = [r for r in self._with_product(metrics.entity_period(self.engine, "product", w.anchor, w.basis, st, sk))
                if r["entity_key"] == pc]
        if not rows:
            raise AnalyticsError("not_found", f"product {pc} has no packs in {st}={sk}")
        return self._envelope("get_brand_share", rows, w, {"prod_code": pc, "market_level": st, "market_key": sk},
                              call=("entity_period", "product", w.anchor, w.basis, st, sk))

    def get_company_performance(self, anchor="2024-05-01", basis="MAT", market_level="total", market_key="TOTAL",
                                top_n=20):
        w = self._window(anchor, basis)
        st, sk = self._scope(market_level, market_key, COMPANY_SCOPES)
        rows = metrics.entity_period(self.engine, "company", w.anchor, w.basis, st, sk)
        mnc = {p["company"]: p["indian_mnc"] for p in self.engine.packs}
        for r in rows:
            r["indian_mnc"] = mnc[r["entity_key"]]
        return self._ranked("get_company_performance", rows, w, {"market_level": st, "market_key": sk},
                            self._top(top_n), call=("entity_period", "company", w.anchor, w.basis, st, sk))

    def get_therapy_performance(self, level="supergroup", anchor="2024-05-01", basis="MAT", within_supergroup=None,
                                top_n=None):
        level = self._type(level, THERAPY_LEVELS, "level")
        w = self._window(anchor, basis)
        st, sk = ("total", "TOTAL") if within_supergroup is None else ("supergroup",
                                                                       self._key("supergroup", within_supergroup))
        rows = metrics.entity_period(self.engine, level, w.anchor, w.basis, st, sk)
        hier = {p["subgroup"]: p for p in self.engine.packs}
        for r in rows:
            h = hier.get(r["entity_key"]) if level == "subgroup" else None
            r.update(therapy_group=h["therapy_group"] if h else None, supergroup=h["supergroup"] if h else None,
                     acute_chronic=h["acute_chronic"] if h else None)
        return self._ranked("get_therapy_performance", rows, w, {"level": level, "within_supergroup": within_supergroup},
                            self._top(top_n), call=("entity_period", level, w.anchor, w.basis, st, sk))

    def get_segment_analysis(self, segment, anchor="2024-05-01", basis="MAT", market_level="total",
                             market_key="TOTAL"):
        seg = self._type(segment, SEGMENTS, "segment")
        w = self._window(anchor, basis)
        st, sk = self._scope(market_level, market_key, MARKET_LEVELS + ("company",))
        rows = metrics.entity_period(self.engine, seg, w.anchor, w.basis, st, sk)
        return self._ranked("get_segment_analysis", rows, w, {"segment": seg, "market_level": st, "market_key": sk},
                            None, call=("entity_period", seg, w.anchor, w.basis, st, sk))
