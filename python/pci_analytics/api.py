"""Deterministic analytical functions over the M4 SQL layer (future agent tools).

Every function:
- validates parameters against the data model (never guesses),
- runs exactly one documented SQL macro (sql/domains.sql -> sql/metrics.sql),
- returns a JSON-serialisable envelope with rows + period + units + evidence + caveats,
- raises AnalyticsError(code, message) for invalid / unsupported / not-found / unavailable requests.
Numbers are returned unrounded; formatting is a presentation concern.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pci_data.db import connect
from pci_data.schema import load_active_manifest

BASES = ("MONTH", "YTD", "MAT")
MARKET_LEVELS = ("total", "supergroup", "therapy_group", "subgroup", "molecule")
THERAPY_LEVELS = ("supergroup", "therapy_group", "subgroup")
SEGMENTS = ("acute_chronic", "indian_mnc", "plain_combination", "molecule_count", "dosage_form", "nfc1")
PRODUCT_SCOPES = MARKET_LEVELS + ("company",) + SEGMENTS
COMPANY_SCOPES = MARKET_LEVELS + tuple(s for s in SEGMENTS if s != "indian_mnc")
UNSUPPORTED = {
    "geography": "The source has no geography field (national data only).",
    "region": "The source has no geography field (national data only).",
    "state": "The source has no geography field (national data only).",
    "channel": "The source has no labelled channel field; SSA/HSA/DSA meaning is unconfirmed.",
    "ssa": "SSA/HSA/DSA meaning is unconfirmed by the data owner; excluded from analytics.",
    "hsa": "SSA/HSA/DSA meaning is unconfirmed by the data owner; excluded from analytics.",
    "dsa": "SSA/HSA/DSA meaning is unconfirmed by the data owner; excluded from analytics.",
    "hcp": "The source has no prescriber/HCP data.",
    "prescriber": "The source has no prescriber/HCP data.",
    "brand_name": "Brand names are not unique; use a prod_code (see find_products).",
}
UNITS = {"value": "INR crore (confirmed)", "units": "'000 packs (inferred)", "qty": "'000 counting units (inferred)",
         "growth": "% vs same window 12 months earlier", "share": "% of scope total", "contribution": "percentage points"}
STANDARD_CAVEATS = ["Units/qty scale ('000) is inferred from workbook labels, not explicitly stated for units fields.",
                    "YTD is calendar YTD (Jan..anchor month), as defined by the source; not Indian FY."]


class AnalyticsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message}}


def _jsonable(v):
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


class CommercialAnalytics:
    def __init__(self, con=None, manifest_path=None):
        """con / manifest_path default to the active dataset (pci_data.schema, PCI_DATASET); pass both to
        address another processed layer explicitly (e.g. the M13 synthetic dataset in tests)."""
        self.con = con or connect()
        m = (json.loads(Path(manifest_path).read_text(encoding="utf-8")) if manifest_path
             else load_active_manifest())                   # active dataset: manifest checked against PCI_DATASET
        self._source = {"source_sha256": m["source"]["sha256"], "processed_built_at": m["build"]["built_at"]}
        self.dataset = "synthetic" if m.get("dataset") == "synthetic" else "ims"   # label only; UI flags synthetic

    # ---------------- helpers ----------------
    def _rows(self, sql, params=()):
        cur = self.con.execute(sql, list(params))
        cols = [d[0] for d in cur.description]
        return [{c: _jsonable(v) for c, v in zip(cols, r)} for r in cur.fetchall()]

    def _period(self, anchor, basis):
        basis = str(basis).upper()
        if basis not in BASES:
            raise AnalyticsError("invalid_parameter", f"basis must be one of {BASES}")
        try:
            a = dt.date.fromisoformat(str(anchor)[:10]).replace(day=1)
        except ValueError as e:
            raise AnalyticsError("invalid_parameter", f"anchor must be an ISO date: {anchor!r}") from e
        rows = self._rows("SELECT * FROM period_basis WHERE anchor = ? AND basis = ?", (a, basis))
        if not rows:
            rng = self.con.execute("SELECT min(period), max(period) FROM dim_period").fetchone()
            raise AnalyticsError("period_unavailable", f"anchor {a} outside data range {rng[0]}..{rng[1]}")
        p = rows[0]
        if not p["current_complete"]:
            raise AnalyticsError("period_unavailable",
                                 f"{basis} window {p['cur_start']}..{p['cur_end']} starts before the first data month")
        return a, basis, p

    def _check_type(self, value, allowed, what):
        v = str(value).lower()
        if v in UNSUPPORTED:
            raise AnalyticsError("unsupported", UNSUPPORTED[v])
        if v not in allowed:
            raise AnalyticsError("invalid_parameter", f"{what} must be one of {allowed}")
        return v

    def _check_key(self, entity_type, key):
        key = str(key)
        if not self.con.execute("SELECT 1 FROM pack_entity WHERE entity_type = ? AND entity_key = ? LIMIT 1",
                                [entity_type, key]).fetchone():
            raise AnalyticsError("not_found", f"no {entity_type} with key {key!r}")
        return key

    def _scope(self, scope_type, scope_key, allowed):
        st = self._check_type(scope_type, allowed, "scope/market level")
        sk = "TOTAL" if st == "total" else self._check_key(st, scope_key)
        return st, sk

    @staticmethod
    def _top(top_n):
        if top_n is None:
            return None
        if not isinstance(top_n, int) or not 1 <= top_n <= 5000:
            raise AnalyticsError("invalid_parameter", "top_n must be an integer 1..5000 or None")
        return top_n

    def _envelope(self, function, sql, params, rows, period=None, filters=None, caveats=(), total_rows=None):
        out = {
            "function": function,
            "filters": filters or {},
            "period": ({k: _jsonable(period[k]) for k in ("anchor", "basis", "basis_label", "cur_start", "cur_end",
                                                          "prior_complete")}
                       | {"prior_start": _jsonable(period["prior_start"]) if period["prior_complete"] else None,
                          "prior_end": _jsonable(period["prior_end"]) if period["prior_complete"] else None}
                       ) if period else None,
            "units": UNITS,
            "row_count": len(rows),
            "total_rows": total_rows if total_rows is not None else len(rows),
            "rows": rows,
            "evidence": {"sql": sql, "params": [_jsonable(p) for p in params], **self._source,
                         "definitions": ["ANALYTICS_METHODS.md", "docs/SQL_ANALYTICS.md", "docs/MARKET_DEFINITION.md"]},
            "caveats": list(STANDARD_CAVEATS) + list(caveats),
        }
        if period and not period["prior_complete"]:
            out["caveats"].append("Comparison window predates the data (starts 2021-06); growth and prior values are NULL.")
        if not rows:
            out["caveats"].append("No rows matched the request.")
        return out

    def _ranked(self, function, macro_sql, params, period, filters, top_n, caveats=(), extra_where="", extra_params=()):
        # one pass: rows + number of rows before top_n (after optional filters)
        sql = (f"SELECT *, count(*) OVER () AS _matched FROM ({macro_sql}) t WHERE 1=1 {extra_where} "
               f"ORDER BY rank_value")
        if top_n:
            sql += f" LIMIT {int(top_n)}"
        rows = self._rows(sql, list(params) + list(extra_params))
        total = rows[0]["_matched"] if rows else 0
        for r in rows:
            del r["_matched"]
        return self._envelope(function, sql, list(params) + list(extra_params), rows, period, filters, caveats, total)

    # ---------------- lookup ----------------
    def find_products(self, name_contains: str, limit: int = 50):
        """Resolve a brand name fragment to analytical product keys (prod_code). Brand names are not unique."""
        if not name_contains or len(str(name_contains).strip()) < 2:
            raise AnalyticsError("invalid_parameter", "name_contains needs at least 2 characters")
        # literal substring match: escape LIKE wildcards in user input (found by M5 parity check)
        needle = str(name_contains).strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = ("SELECT prod_code, brand, company, manufacturer_code, prod_launch_month FROM dim_product "
               "WHERE brand ILIKE ? ESCAPE '\\' ORDER BY brand, company, prod_code LIMIT ?")
        params = [f"%{needle}%", int(limit)]
        rows = self._rows(sql, params)
        return self._envelope("find_products", sql, params, rows, filters={"name_contains": name_contains})

    # ---------------- A. market ----------------
    def get_market_performance(self, level="subgroup", anchor="2024-05-01", basis="MAT", top_n=None):
        level = self._check_type(level, MARKET_LEVELS, "level")
        a, b, p = self._period(anchor, basis)
        return self._ranked("get_market_performance", "SELECT * FROM market_performance(?, ?, ?)", [level, a, b],
                            p, {"level": level}, self._top(top_n))

    def get_market_trends(self, level="total", key="TOTAL"):
        level = self._check_type(level, MARKET_LEVELS, "level")
        key = "TOTAL" if level == "total" else self._check_key(level, key)
        sql = "SELECT * FROM market_trend(?, ?)"
        rows = self._rows(sql, [level, key])
        return self._envelope("get_market_trends", sql, [level, key], rows, filters={"level": level, "key": key},
                              caveats=["Month-level growth compares with the same month a year earlier; "
                                       "YTD from 2022, MAT from 2022-05, MAT growth from 2023-05."])

    # ---------------- B. brand / product ----------------
    def get_brand_performance(self, anchor="2024-05-01", basis="MAT", market_level="total", market_key="TOTAL",
                              company=None, top_n=20):
        a, b, p = self._period(anchor, basis)
        st, sk = self._scope(market_level, market_key, PRODUCT_SCOPES)
        extra, extra_p, filters = "", [], {"market_level": st, "market_key": sk}
        if company is not None:
            self._check_key("company", company)
            extra, extra_p = "AND company = ?", [company]
            filters["company"] = company
        return self._ranked("get_brand_performance", "SELECT * FROM product_performance(?, ?, ?, ?)",
                            [a, b, st, sk], p, filters, self._top(top_n),
                            ["Analytical key is prod_code; brand is a display label (names are not unique).",
                             "Shares and ranks are within the market scope; a product can appear in several subgroup markets."],
                            extra, extra_p)

    def get_brand_growth(self, prod_code, market_level="total", market_key="TOTAL"):
        pc = self._check_key("product", prod_code)
        st, sk = self._scope(market_level, market_key, PRODUCT_SCOPES)
        sql = "SELECT * FROM product_trend(?, ?, ?)"
        rows = self._rows(sql, [pc, st, sk])
        if not rows:
            raise AnalyticsError("not_found", f"product {pc} has no packs in {st}={sk}")
        return self._envelope("get_brand_growth", sql, [pc, st, sk], rows,
                              filters={"prod_code": pc, "market_level": st, "market_key": sk})

    def get_brand_share(self, prod_code, market_level, market_key, anchor="2024-05-01", basis="MAT"):
        pc = self._check_key("product", prod_code)
        a, b, p = self._period(anchor, basis)
        st, sk = self._scope(market_level, market_key, PRODUCT_SCOPES)
        sql = "SELECT * FROM product_performance(?, ?, ?, ?) WHERE entity_key = ?"
        rows = self._rows(sql, [a, b, st, sk, pc])
        if not rows:
            raise AnalyticsError("not_found", f"product {pc} has no packs in {st}={sk}")
        return self._envelope("get_brand_share", sql, [a, b, st, sk, pc], rows, p,
                              {"prod_code": pc, "market_level": st, "market_key": sk})

    # ---------------- C. company ----------------
    def get_company_performance(self, anchor="2024-05-01", basis="MAT", market_level="total", market_key="TOTAL",
                                top_n=20):
        a, b, p = self._period(anchor, basis)
        st, sk = self._scope(market_level, market_key, COMPANY_SCOPES)
        return self._ranked("get_company_performance", "SELECT * FROM company_performance(?, ?, ?, ?)",
                            [a, b, st, sk], p, {"market_level": st, "market_key": sk}, self._top(top_n))

    # ---------------- D. therapy ----------------
    def get_therapy_performance(self, level="supergroup", anchor="2024-05-01", basis="MAT", within_supergroup=None,
                                top_n=None):
        level = self._check_type(level, THERAPY_LEVELS, "level")
        a, b, p = self._period(anchor, basis)
        st, sk = ("total", "TOTAL") if within_supergroup is None else ("supergroup",
                                                                       self._check_key("supergroup", within_supergroup))
        return self._ranked("get_therapy_performance", "SELECT * FROM therapy_performance(?, ?, ?, ?, ?)",
                            [level, a, b, st, sk], p, {"level": level, "within_supergroup": within_supergroup},
                            self._top(top_n))

    # ---------------- E. segment ----------------
    def get_segment_analysis(self, segment, anchor="2024-05-01", basis="MAT", market_level="total",
                             market_key="TOTAL"):
        seg = self._check_type(segment, SEGMENTS, "segment")
        a, b, p = self._period(anchor, basis)
        st, sk = self._scope(market_level, market_key, MARKET_LEVELS + ("company",))
        return self._ranked("get_segment_analysis", "SELECT * FROM segment_performance(?, ?, ?, ?, ?)",
                            [seg, a, b, st, sk], p, {"segment": seg, "market_level": st, "market_key": sk}, None)

    # ---------------- F. opportunity scoring (M6) ----------------
    # Canonical scoring is Python (opportunity.py) over the M5-validated Python engine; the engine is
    # loaded lazily on first use (~0.7 s, ~140 MB) so the descriptive SQL functions stay lightweight.
    def _python_engine(self):
        if getattr(self, "_opp_engine", None) is None:
            from .engine import default_engine
            self._opp_engine = default_engine()
        return self._opp_engine

    _opportunity_engine = _python_engine

    def get_opportunity_scores(self, level="product", anchor="2024-05-01", basis="MAT", market_level=None,
                               market_key=None, company=None, include_insufficient=False, top_n=50):
        from .opportunity import scores_envelope
        return scores_envelope(self._opportunity_engine(), level, anchor, basis, market_level, market_key,
                               company, include_insufficient, top_n)

    def get_opportunity_detail(self, level, entity_key, anchor="2024-05-01", basis="MAT"):
        from .opportunity import detail_envelope
        return detail_envelope(self._opportunity_engine(), level, entity_key, anchor, basis)

    # ---------------- H. data quality (Phase 3; API_SPEC get_data_quality_status) ----------------
    # Deterministic structural checks of the ACTIVE processed layer (private or synthetic). No business metric:
    # every observed number is a row/violation count. Mirrors rules proven by the M3 test suites.
    _DQ_CHECKS = [
        ("pack key (PFC) unique", "SELECT count(*) - count(DISTINCT pfc) FROM pack", 0),
        ("fact grain dense: packs x months", "SELECT (SELECT count(*) FROM pack) * (SELECT count(DISTINCT period) "
         "FROM fact_pack_month) - (SELECT count(*) FROM fact_pack_month)", 0),
        ("no null or negative value/units/qty", "SELECT count(*) FROM fact_pack_month WHERE value_cr IS NULL OR "
         "units_k IS NULL OR qty_k IS NULL OR value_cr < 0 OR units_k < 0 OR qty_k < 0", 0),
        ("snapshot MAT = sum of 12 months", "SELECT count(*) FROM (SELECT s.pfc, s.snapshot_year, any_value(s.value_mat) AS v, "
         "sum(f.value_cr) AS t FROM pack_snapshot s JOIN fact_pack_month f ON f.pfc = s.pfc AND f.period > s.snapshot_month "
         "- INTERVAL 12 MONTH AND f.period <= s.snapshot_month GROUP BY 1, 2) WHERE abs(v - t) > 1e-6 * greatest(1, abs(t))", 0),
        ("subgroup -> supergroup is unique (primary market roll-up)", "SELECT count(*) FROM (SELECT subgroup FROM pack "
         "GROUP BY 1 HAVING count(DISTINCT supergroup) > 1)", 0),
        ("product code -> brand is unique", "SELECT count(*) FROM (SELECT prod_code FROM pack GROUP BY 1 "
         "HAVING count(DISTINCT brand) > 1)", 0),
        ("brand names shared by several companies (informational: never key on brand)",
         "SELECT count(*) FROM (SELECT brand FROM pack GROUP BY 1 HAVING count(DISTINCT company) > 1)", None),
        ("packs with no sales in any month (informational)",
         "SELECT count(*) FROM (SELECT pfc FROM fact_pack_month GROUP BY 1 HAVING max(value_cr) = 0 AND max(units_k) = 0)", None),
    ]

    def get_data_quality_status(self):
        rows = []
        for check, sql, expected in self._DQ_CHECKS:
            observed = int(self.con.execute(sql).fetchone()[0])
            status = "INFO" if expected is None else ("PASS" if observed == expected else "FAIL")
            rows.append({"check": check, "status": status, "observed_count": observed, "expected_count": expected})
        first, last, n = self.con.execute("SELECT min(period), max(period), count(DISTINCT period) FROM fact_pack_month").fetchone()
        n_packs = int(self.con.execute("SELECT count(*) FROM pack").fetchone()[0])
        env = self._envelope("get_data_quality_status", "; ".join(s for _, s, _ in self._DQ_CHECKS), [], rows,
                             caveats=["Geography is not in the data (national only); SSA/HSA/DSA meaning unknown and unused."])
        env["status"] = "FAIL" if any(r["status"] == "FAIL" for r in rows) else "PASS"
        env["coverage"] = {"first_month": _jsonable(first), "last_month": _jsonable(last), "months": int(n),
                           "packs": n_packs, "geography": "none (national)"}
        return env

    # ---------------- G. scenarios (M7) ----------------
    # Canonical deterministic what-if engine (scenario.py); baselines come from the M5-validated engine.
    def run_scenario(self, scenario_type, entity_type, entity_key, assumptions, anchor="2024-05-01", basis="MAT",
                     market_level=None, market_key=None):
        from .scenario import run_scenario
        return run_scenario(self._python_engine(), scenario_type, entity_type, entity_key, assumptions, anchor,
                            basis, market_level, market_key)

    def get_scenario_baseline(self, entity_type, entity_key, anchor="2024-05-01", basis="MAT", market_level=None,
                              market_key=None):
        from .scenario import baseline_envelope
        return baseline_envelope(self._python_engine(), entity_type, entity_key, anchor, basis, market_level,
                                 market_key)
