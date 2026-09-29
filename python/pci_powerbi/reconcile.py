"""Reconcile the Power BI semantic model against the validated reference engines (M12).

Reference hierarchy: processed Parquet -> SQL engine (M4, reference for metrics) -> independent Python
engine (M5; canonical for opportunity M6 and scenarios M7) -> Power BI (this check).
The reference engines are never changed to make Power BI match.

Every case runs a DAX query against the live model (Power BI Desktop's local engine) and compares
it row by row and field by field with the reference output:
  numbers : |pbi - ref| <= REL_TOL * max(1, |ref|)   (floating-point summation order only)
  BLANK   : must be BLANK exactly where the reference is NULL (growth never silently 0, and vice versa)
  text/int: exact (status, evidence, quadrant, ranks)
Rows: the set of entity keys must be identical.

Outputs (contain values): IMS mode -> <PCI_IMS_DATA_DIR>/reports/ (outside the repository; the summary .md too);
synthetic mode -> evaluation/reports/powerbi_reconciliation_synthetic.{json,csv} (git-ignored) + the tracked
value-free POWER_BI_RECONCILIATION_SYNTHETIC.md. The tracked POWER_BI_RECONCILIATION.md is the historical M12 summary.

Run (Power BI Desktop open on the project, refreshed):
    cd python && ..\\.venv\\Scripts\\python.exe -m pci_powerbi.reconcile
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from pci_analytics import metrics
from pci_analytics.api import AnalyticsError, CommercialAnalytics
from pci_analytics.engine import default_engine
from pci_analytics.opportunity import score_all
from pci_analytics.scenario import run_scenario

from . import desktop

ROOT = Path(__file__).resolve().parents[2]
from pci_data.schema import output_dir  # noqa: E402
# synthetic: evaluation/reports (git-ignored details + tracked value-free summary); IMS: <PCI_IMS_DATA_DIR>/reports
REPORTS = output_dir("reports", ROOT / "evaluation" / "reports")
REL_TOL = 1e-9
PBIP_TITLE = "PCI_Commercial_Intelligence"


def _suffix() -> str:
    """'' for the IMS dataset (M12 file names, written outside the repo); '_synthetic' for the synthetic dataset."""
    from pci_data.schema import DATASET
    return "" if DATASET == "ims" else f"_{DATASET}"

CORE = [("value_cur", "Value"), ("value_prior", "Value Prior"), ("value_abs_chg", "Value Change"),
        ("value_growth_pct", "Value Growth %"), ("value_growth_status", "Growth Status"),
        ("units_cur", "Units"), ("units_prior", "Units Prior"), ("units_abs_chg", "Units Change"),
        ("units_growth_pct", "Units Growth %"), ("qty_cur", "Qty"), ("qty_prior", "Qty Prior"),
        ("qty_growth_pct", "Qty Growth %"), ("n_packs", "Packs")]
NATIONAL = [("scope_value_cur", "National Value"), ("scope_value_prior", "National Value Prior"),
            ("value_share_pct", "Share of Total %"), ("value_share_prior_pct", "Share of Total Prior %"),
            ("value_share_chg_pp", "Share of Total Change pp"), ("units_share_pct", "Units Share of Total %"),
            ("contribution_to_growth_pp", "Contribution to Total Growth pp"), ("evolution_index", "Evolution Index vs Total")]
MARKET = [("scope_value_cur", "Market Value"), ("scope_value_prior", "Market Value Prior"),
          ("value_share_pct", "Share of Market %"), ("value_share_prior_pct", "Share of Market Prior %"),
          ("value_share_chg_pp", "Share Change pp"), ("units_share_pct", "Units Share of Market %"),
          ("contribution_to_growth_pp", "Contribution to Market Growth pp"), ("evolution_index", "Evolution Index")]
THERAPY = [("scope_value_cur", "Therapy Area Value"), ("scope_value_prior", "Therapy Area Value Prior"),
           ("value_share_pct", "Share of Therapy Area %"),
           ("contribution_to_growth_pp", "Contribution to Therapy Area Growth pp")]


def RANKS(label):
    return [("rank_value", f"Rank Value ({label})"), ("rank_growth", f"Rank Growth ({label})")]


LEVEL_COL = {"supergroup": ("Market", "Supergroup"), "therapy_group": ("Market", "Therapy Group"),
             "subgroup": ("Market", "Subgroup"), "molecule": ("Pack", "Molecule"),
             "company": ("Company", "Company"), "product": ("Product", "Product Code"),
             "acute_chronic": ("Market", "Acute/Chronic"), "indian_mnc": ("Company", "Indian/MNC"),
             "plain_combination": ("Pack", "Plain/Combination"), "molecule_count": ("Pack", "Molecule Count"),
             "dosage_form": ("Pack", "Dosage Form"), "nfc1": ("Pack", "Form (NFC1)")}
RANK_LABEL = {"supergroup": "Supergroup", "therapy_group": "Therapy Group", "subgroup": "Subgroup",
              "molecule": "Molecule", "company": "Company", "product": "Product"}


# ------------------------------------------------------------------------------------------ DAX helpers
def s(v: str) -> str:
    return '"' + str(v).replace('"', '""') + '"'


def c(t: str, col: str) -> str:
    return f"'{t}'[{col}]"


def f_anchor(d: dt.date) -> str:
    return f"TREATAS ( {{ DATE ( {d.year}, {d.month}, 1 ) }}, 'Anchor'[Anchor Month] )"


def f_basis(b: str) -> str:
    return f"TREATAS ( {{ {s(b)} }}, 'Basis'[Basis] )"


def f_eq(t: str, col: str, v) -> str:
    val = s(v) if isinstance(v, str) else repr(float(v))
    return f"TREATAS ( {{ {val} }}, {c(t, col)} )"


def summarize(group: list[tuple], filters: list[str], fields: list[tuple]) -> str:
    parts = [c(*g) for g in group] + filters + [f"{s(alias)}, [{m}]" for alias, m in fields]
    return "EVALUATE\nSUMMARIZECOLUMNS (\n    " + ",\n    ".join(parts) + "\n)"


def row_query(filters: list[str], fields: list[tuple]) -> str:
    body = ", ".join(f"{s(a)}, [{m}]" for a, m in fields)
    return f"EVALUATE\nCALCULATETABLE (\n    ROW ( {body} ),\n    " + ",\n    ".join(filters) + "\n)" if filters \
        else f"EVALUATE ROW ( {body} )"


# ------------------------------------------------------------------------------------------ comparison
@dataclass
class Result:
    case: str
    area: str
    description: str
    rows_ref: int = 0
    rows_pbi: int = 0
    fields: int = 0
    values_compared: int = 0
    mismatches: int = 0
    max_abs_diff: float = 0.0
    max_rel_diff: float = 0.0
    blank_checks: int = 0
    missing_keys: int = 0
    extra_keys: int = 0
    seconds_ref: float = 0.0
    seconds_pbi: float = 0.0
    status: str = "PASS"
    examples: list = field(default_factory=list)
    note: str = ""


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def compare(res: Result, ref_rows: dict, pbi_rows: dict, fields: list[tuple]) -> Result:
    """ref_rows / pbi_rows: key -> {alias: value}. fields: [(alias, kind)] kind in num|text|int."""
    res.rows_ref, res.rows_pbi, res.fields = len(ref_rows), len(pbi_rows), len(fields)
    miss, extra = set(ref_rows) - set(pbi_rows), set(pbi_rows) - set(ref_rows)
    res.missing_keys, res.extra_keys = len(miss), len(extra)
    for k in list(miss)[:3]:
        res.examples.append({"key": str(k), "issue": "missing in Power BI"})
    for k in list(extra)[:3]:
        res.examples.append({"key": str(k), "issue": "extra in Power BI"})
    for k in set(ref_rows) & set(pbi_rows):
        r, p = ref_rows[k], pbi_rows[k]
        for alias, kind in fields:
            rv, pv = r.get(alias), p.get(alias)
            res.values_compared += 1
            if rv is None or pv is None:
                res.blank_checks += rv is None
                ok = rv is None and pv is None
            elif kind == "num":
                d = abs(float(pv) - float(rv))
                rel = d / max(1.0, abs(float(rv)))
                res.max_abs_diff = max(res.max_abs_diff, d)
                res.max_rel_diff = max(res.max_rel_diff, rel)
                ok = rel <= REL_TOL
            elif kind == "int":
                ok = int(pv) == int(rv)
            else:
                ok = str(pv) == str(rv)
            if not ok:
                res.mismatches += 1
                if len(res.examples) < 8:
                    res.examples.append({"key": str(k), "field": alias, "ref": rv, "pbi": pv})
    if res.mismatches or res.missing_keys or res.extra_keys or (res.rows_ref == 0 and not res.note):
        res.status = "FAIL"
    return res


def kind_of(alias: str) -> str:
    if alias.startswith("rank") or alias in ("n_packs", "opportunity_rank"):
        return "int"
    if alias.endswith("status") or alias in ("evidence", "quadrant", "status_text"):
        return "text"
    return "num"


# ------------------------------------------------------------------------------------------ runner
class Reconciler:
    def __init__(self, pid: int | None = None):
        self.pid = pid or desktop.find_desktop(PBIP_TITLE)
        self.api = CommercialAnalytics()
        self.engine = None
        self.results: list[Result] = []

    def add(self, res: Result) -> Result:
        """Record a case and log it immediately (an interrupted run keeps its evidence)."""
        self.results.append(res)
        REPORTS.mkdir(parents=True, exist_ok=True)
        with open(REPORTS / f"powerbi_reconciliation{_suffix()}_progress.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res.__dict__, default=str) + "\n")
        print(f"{res.status} {res.case} rows={res.rows_ref}/{res.rows_pbi} values={res.values_compared} "
              f"maxrel={res.max_rel_diff:.1e} pbi={res.seconds_pbi:.1f}s", flush=True)
        return res

    def py(self):
        if self.engine is None:
            self.engine = default_engine()
        return self.engine

    def dax(self, q: str) -> tuple[list[dict], float]:
        return desktop.timed_query(self.pid, q)

    # ---- generic entity_period case
    def entity_case(self, cid, area, desc, level, anchor, basis, ref_fn, metric_sets, filters=(), key_col=None):
        res = Result(cid, area, desc)
        t0 = time.perf_counter()
        ref = ref_fn()
        res.seconds_ref = time.perf_counter() - t0
        fields = [f for ms in metric_sets for f in ms]
        group = [key_col or LEVEL_COL[level]] if level != "total" else []
        q = summarize(group, [f_anchor(anchor), f_basis(basis), *filters], fields)
        rows, res.seconds_pbi = self.dax(q)
        kname = group[0][1] if group else None
        ref_rows = {(r["entity_key"] if kname else "TOTAL"): {a: r.get(a) for a, _ in fields} for r in ref["rows"]}
        pbi_rows = {(str(r[kname]) if kname else "TOTAL"): r for r in rows}
        self.add(compare(res, ref_rows, pbi_rows, [(a, kind_of(a)) for a, _ in fields]))
        return ref

    # ---- cases
    def run_metrics(self):
        A = dt.date(2024, 5, 1)
        api = self.api
        # total market: every basis, including windows whose comparison predates the data
        for anchor, basis in [(A, "MONTH"), (A, "YTD"), (A, "MAT"), (dt.date(2021, 6, 1), "MONTH"),
                              (dt.date(2022, 3, 1), "YTD"), (dt.date(2022, 5, 1), "MAT"), (dt.date(2023, 5, 1), "MAT"),
                              (dt.date(2023, 1, 1), "YTD"), (dt.date(2023, 2, 1), "MONTH"), (dt.date(2022, 12, 1), "MAT")]:
            self.entity_case(f"MKT-total-{basis}-{anchor:%Y%m}", "market", f"Total market, {basis} {anchor:%b %Y}",
                             "total", anchor, basis,
                             lambda a=anchor, b=basis: api.get_market_performance("total", a, b), [CORE, NATIONAL])
        for level, anchor, basis in [("supergroup", A, "MAT"), ("supergroup", A, "YTD"), ("supergroup", A, "MONTH"),
                                     ("supergroup", dt.date(2023, 11, 1), "MAT"), ("therapy_group", A, "MAT"),
                                     ("therapy_group", dt.date(2024, 2, 1), "YTD"), ("subgroup", A, "MAT"),
                                     ("subgroup", A, "MONTH"), ("subgroup", dt.date(2023, 8, 1), "YTD"),
                                     ("subgroup", dt.date(2022, 5, 1), "MAT"), ("molecule", A, "MAT")]:
            self.entity_case(f"MKT-{level}-{basis}-{anchor:%Y%m}", "market",
                             f"All {level} markets, {basis} {anchor:%b %Y} (national share/contribution/EI, ranks)",
                             level, anchor, basis,
                             lambda l=level, a=anchor, b=basis: api.get_market_performance(l, a, b),
                             [CORE, NATIONAL, RANKS(RANK_LABEL[level])])
        # three-way check on one case: Python engine == SQL == Power BI
        res = Result("MKT-subgroup-MAT-202405-python", "market", "All subgroups MAT May 2024 vs the independent Python engine")
        t0 = time.perf_counter()
        pyrows = metrics.entity_period(self.py(), "subgroup", A, "MAT")
        res.seconds_ref = time.perf_counter() - t0
        fields = CORE + NATIONAL + RANKS("Subgroup")
        rows, res.seconds_pbi = self.dax(summarize([LEVEL_COL["subgroup"]], [f_anchor(A), f_basis("MAT")], fields))
        self.add(compare(res, {r["entity_key"]: r for r in pyrows}, {r["Subgroup"]: r for r in rows},
                                    [(a, kind_of(a)) for a, _ in fields]))

        # largest entities, chosen from the reference at run time (no names typed)
        sg = api.get_market_performance("supergroup", A, "MAT", top_n=1)["rows"][0]["entity_key"]
        sub = api.get_market_performance("subgroup", A, "MAT", top_n=1)["rows"][0]["entity_key"]
        self.top = {"supergroup": sg, "subgroup": sub}
        # companies
        for scope, key, anchor, basis in [("total", "TOTAL", A, "MAT"), ("total", "TOTAL", A, "YTD"),
                                          ("total", "TOTAL", A, "MONTH"), ("supergroup", sg, A, "MAT"),
                                          ("subgroup", sub, dt.date(2024, 3, 1), "YTD")]:
            flt = [] if scope == "total" else [f_eq(*LEVEL_COL[scope], key)]
            self.entity_case(f"CO-{scope}-{basis}-{anchor:%Y%m}", "company",
                             f"All companies within {scope if scope == 'total' else 'largest ' + scope}, {basis} {anchor:%b %Y}",
                             "company", anchor, basis,
                             lambda st=scope, k=key, a=anchor, b=basis: api.get_company_performance(a, b, st, k, top_n=None),
                             [CORE, MARKET, RANKS("Company")], flt)
        self.top["company"] = api.get_company_performance(A, "MAT", top_n=1)["rows"][0]["entity_key"]
        # products (full populations)
        for scope, key, anchor, basis in [("total", "TOTAL", A, "MAT"), ("subgroup", sub, A, "MAT"),
                                          ("supergroup", sg, A, "MONTH"), ("total", "TOTAL", dt.date(2023, 12, 1), "YTD")]:
            flt = [] if scope == "total" else [f_eq(*LEVEL_COL[scope], key)]
            self.entity_case(f"PR-{scope}-{basis}-{anchor:%Y%m}", "product",
                             f"All products within {scope if scope == 'total' else 'largest ' + scope}, {basis} {anchor:%b %Y} (key = product code)",
                             "product", anchor, basis,
                             lambda st=scope, k=key, a=anchor, b=basis: api.get_brand_performance(a, b, st, k, top_n=None),
                             [CORE, MARKET, RANKS("Product")], flt)
        self.top["product"] = api.get_brand_performance(A, "MAT", top_n=1)["rows"][0]["entity_key"]
        # therapy: subgroup within its therapy area
        self.entity_case("TH-subgroup-in-supergroup-MAT-202405", "market",
                         "Subgroups within the largest therapy area: share/contribution within the therapy area, ranks",
                         "subgroup", A, "MAT",
                         lambda: api.get_therapy_performance("subgroup", A, "MAT", within_supergroup=sg),
                         [CORE, THERAPY, RANKS("Subgroup")], [f_eq("Market", "Supergroup", sg)])
        # segments (national)
        for seg in ("acute_chronic", "indian_mnc", "plain_combination", "molecule_count", "dosage_form", "nfc1"):
            self.entity_case(f"SEG-{seg}-MAT-202405", "segment", f"Segment {seg}, MAT May 2024 (national)", seg, A, "MAT",
                             lambda sg_=seg: api.get_segment_analysis(sg_, A, "MAT"), [CORE, NATIONAL])
        self.entity_case("SEG-acute_chronic-YTD-202403", "segment", "Segment acute_chronic, YTD Mar 2024", "acute_chronic",
                         dt.date(2024, 3, 1), "YTD", lambda: api.get_segment_analysis("acute_chronic", dt.date(2024, 3, 1), "YTD"),
                         [CORE, NATIONAL])

    def run_trends(self):
        api = self.api
        specs = [("total", "TOTAL", lambda: api.get_market_trends("total", "TOTAL"), []),
                 ("subgroup", self.top["subgroup"], lambda: api.get_market_trends("subgroup", self.top["subgroup"]),
                  [f_eq("Market", "Subgroup", self.top["subgroup"])]),
                 ("product", self.top["product"], lambda: api.get_brand_growth(self.top["product"]),
                  [f_eq("Product", "Product Code", self.top["product"])])]
        pairs = {"MONTH": [("value_cr", "Trend Value"), ("value_cr_prior", "Trend Value Prior Year"),
                           ("value_growth_pct", "Trend Growth %"), ("units_k", "Trend Units"),
                           ("units_k_prior", "Trend Units Prior Year")],
                 "YTD": [("value_ytd", "Trend Value"), ("value_ytd_prior", "Trend Value Prior Year"),
                         ("value_ytd_growth_pct", "Trend Growth %")],
                 "MAT": [("value_mat", "Trend Value"), ("value_mat_prior", "Trend Value Prior Year"),
                         ("value_mat_growth_pct", "Trend Growth %"), ("units_mat", "Trend Units"),
                         ("units_mat_prior", "Trend Units Prior Year")]}
        for name, key, ref_fn, flt in specs:
            t0 = time.perf_counter()
            ref = ref_fn()["rows"]
            tref = time.perf_counter() - t0
            for basis, fs in pairs.items():
                res = Result(f"TR-{name}-{basis}", "trend", f"36-month trend of the {'largest ' if name != 'total' else ''}{name}, "
                             f"{basis} basis at every month (incl. months without a complete window)")
                res.seconds_ref = tref
                rows, res.seconds_pbi = self.dax(summarize([("Period", "Period")], [f_basis(basis), *flt],
                                                           [(a, m) for a, m in fs]))
                ref_rows = {str(r["period"])[:10]: {a: r[a] for a, _ in fs} for r in ref}
                pbi_rows = {str(r["Period"])[:10]: r for r in rows}
                # months where every value is NULL are legitimately absent from SUMMARIZECOLUMNS
                for k in list(ref_rows):
                    if all(v is None for v in ref_rows[k].values()) and k not in pbi_rows:
                        pbi_rows[k] = {a: None for a, _ in fs}
                self.add(compare(res, ref_rows, pbi_rows, [(a, "num") for a, _ in fs]))

    def run_guards(self):
        """Evidence rules: BLANK is never 0; ambiguous selections are never silently resolved."""
        A = dt.date(2024, 5, 1)
        checks = []
        # 1. incomplete current window -> SQL refuses (period_unavailable); Power BI shows no values
        for anchor, basis in [(dt.date(2022, 4, 1), "MAT"), (dt.date(2021, 12, 1), "YTD")]:
            try:
                self.api.get_market_performance("total", anchor, basis)
                ref = "OK"
            except AnalyticsError as e:
                ref = e.code
            rows, _ = self.dax(row_query([f_anchor(anchor), f_basis(basis)],
                                         [("v", "Value"), ("g", "Value Growth %"), ("cap", "Period Caption")]))
            r = rows[0]
            checks.append((f"GUARD-incomplete-{basis}-{anchor:%Y%m}", f"{basis} {anchor:%b %Y}: window before first data month",
                           ref == "period_unavailable" and r["v"] is None and r["g"] is None and "no values" in r["cap"]))
        # 2. two anchors selected -> nothing shown (never silently the latest)
        rows, _ = self.dax(row_query([f"TREATAS ( {{ DATE ( 2024, 5, 1 ), DATE ( 2024, 4, 1 ) }}, 'Anchor'[Anchor Month] )"],
                                     [("v", "Value"), ("cap", "Period Caption")]))
        checks.append(("GUARD-multi-anchor", "Two anchor months selected", rows[0]["v"] is None and rows[0]["cap"].startswith("Select")))
        rows, _ = self.dax(row_query([f"TREATAS ( {{ \"MAT\", \"YTD\" }}, 'Basis'[Basis] )"], [("v", "Value")]))
        checks.append(("GUARD-multi-basis", "Two bases selected", rows[0]["v"] is None))
        # 3. no selection -> documented defaults (latest month, MAT), same as the application
        rows, _ = self.dax(row_query([], [("a", "Anchor Month"), ("b", "Basis Selected")]))
        last = self.api.con.execute("SELECT max(period) FROM dim_period").fetchone()[0]
        checks.append(("GUARD-defaults", "No selection = latest month and MAT", rows[0]["a"] == last and rows[0]["b"] == "MAT"))
        # 4. prior_zero / prior_unavailable growth stays BLANK (counted across the full product population)
        ref = self.api.get_brand_performance(A, "MAT", top_n=None)["rows"]
        pz = [r["entity_key"] for r in ref if r["value_growth_status"] == "prior_zero"][:50]
        rows, _ = self.dax(summarize([("Product", "Product Code")],
                                     [f_anchor(A), f_basis("MAT"), f"TREATAS ( {{ {', '.join(s(k) for k in pz)} }}, 'Product'[Product Code] )"],
                                     [("g", "Value Growth %"), ("st", "Growth Status"), ("v", "Value")]))
        checks.append(("GUARD-prior-zero", f"{len(pz)} products with prior = 0 and current > 0: growth BLANK, status prior_zero",
                       len(rows) == len(pz) and all(r["g"] is None and r["st"] == "prior_zero" and r["v"] > 0 for r in rows)))
        for cid, desc, ok in checks:
            r = Result(cid, "guard", desc, rows_ref=1, rows_pbi=1, fields=1, values_compared=1, blank_checks=1)
            r.status = "PASS" if ok else "FAIL"
            self.add(r)

    def run_opportunity(self):
        eng = self.py()
        for anchor, basis in [(dt.date(2024, 5, 1), "MAT"), (dt.date(2024, 3, 1), "YTD"), (dt.date(2023, 5, 1), "MAT")]:
            # (a) imported canonical scores == canonical engine (products and markets, full populations)
            for level, tbl, keycol, meas in [
                ("product", "Opportunity Product", "Entity Key",
                 [("score", "Product Opportunity Score"), ("opportunity_rank", "Product Opportunity Rank"),
                  ("evidence", "Product Evidence"), ("market_value_growth_pct", "Product Opp Market Growth %"),
                  ("evolution_index", "Product Opp Evolution Index"), ("value_share_in_market_pct", "Product Opp Share in Market %"),
                  ("quadrant", "Product Opp Quadrant"), ("n_rel", "Momentum Percentile")]),
                ("market", "Opportunity Market", "Subgroup",
                 [("score", "Market Opportunity Score"), ("opportunity_rank", "Market Opportunity Rank"),
                  ("evidence", "Market Evidence"), ("value_growth_pct", "Market Opp Growth %"),
                  ("value_share_of_total_pct", "Market Opp Size Share %"), ("n_growth", "Market Growth Percentile")])]:
                res = Result(f"OPP-{level}-{basis}-{anchor:%Y%m}", "opportunity",
                             f"Imported OPP scores vs canonical engine, {level}s, {basis} {anchor:%b %Y} (full population)")
                t0 = time.perf_counter()
                from .export import _evidence
                out = score_all(eng, level, anchor, basis)["rows"]
                ref = {}
                for r in out:
                    comp = {c_["name"]: c_["normalized"] for c_ in r["components"]}
                    d = {k: r.get(k) for k, _ in meas}
                    d.update(evidence=_evidence(r), quadrant=r.get("matrix_quadrant"),
                             n_rel=comp.get("relative_momentum"), n_growth=comp.get("market_growth"))
                    ref[r["entity_key"]] = d
                res.seconds_ref = time.perf_counter() - t0
                q = summarize([(tbl, keycol)], [f_anchor(anchor), f_basis(basis)], meas)
                rows, res.seconds_pbi = self.dax(q)
                self.add(compare(res, ref, {r[keycol]: r for r in rows}, [(a, kind_of(a) if a != "score" else "num") for a, _ in meas]))
            # (b) independent: the model's own metrics reproduce the raw components the score was built on
            res = Result(f"OPP-market-components-{basis}-{anchor:%Y%m}", "opportunity",
                         f"Model measures (growth, share of total) reproduce the market score inputs, {basis} {anchor:%b %Y}")
            out = {r["entity_key"]: r for r in score_all(eng, "market", anchor, basis)["rows"]}
            rows, res.seconds_pbi = self.dax(summarize([("Market", "Subgroup")], [f_anchor(anchor), f_basis(basis)],
                                                       [("value_growth_pct", "Value Growth %"), ("value_share_of_total_pct", "Share of Total %")]))
            self.add(compare(res, {k: {"value_growth_pct": r["value_growth_pct"],
                                                  "value_share_of_total_pct": r["value_share_of_total_pct"]} for k, r in out.items()},
                                        {r["Subgroup"]: r for r in rows}, [("value_growth_pct", "num"), ("value_share_of_total_pct", "num")]))
        # (c) product components in the largest subgroup: EI / share-in-market / market growth from the model
        anchor, basis, sub = dt.date(2024, 5, 1), "MAT", self.top["subgroup"]
        res = Result("OPP-product-components-MAT-202405", "opportunity",
                     "Model measures (EI, share of market, market growth) reproduce product score inputs in the largest subgroup")
        out = {r["prod_code"]: r for r in score_all(eng, "product", anchor, basis)["rows"] if r["subgroup"] == sub}
        rows, res.seconds_pbi = self.dax(summarize([("Product", "Product Code")],
                                                   [f_anchor(anchor), f_basis(basis), f_eq("Market", "Subgroup", sub)],
                                                   [("evolution_index", "Evolution Index"), ("value_share_in_market_pct", "Share of Market %"),
                                                    ("market_value_growth_pct", "Market Growth %")]))
        self.add(compare(res, {k: {f: r[f] for f in ("evolution_index", "value_share_in_market_pct", "market_value_growth_pct")}
                                          for k, r in out.items()},
                                    {r["Product Code"]: r for r in rows},
                                    [("evolution_index", "num"), ("value_share_in_market_pct", "num"), ("market_value_growth_pct", "num")]))

    def run_scenarios(self):
        eng, A = self.py(), dt.date(2024, 5, 1)
        P, CO, SG, SUB = self.top["product"], self.top["company"], self.top["supergroup"], self.top["subgroup"]
        mol = self.api.get_market_performance("molecule", A, "MAT", top_n=1)["rows"][0]["entity_key"]
        # a product with packs but zero units in the window -> insufficient baseline
        zero = next(r["entity_key"] for r in self.api.get_brand_performance(A, "MONTH", top_n=None)["rows"]
                    if r["units_cur"] == 0)
        p_sub = self.api.get_brand_performance(A, "MAT", "subgroup", SUB, top_n=1)["rows"][0]["entity_key"]
        cases = [
            ("PRICE_CHANGE", "product", P, {"price_change_pct": 10}, A, "MAT", None, None),
            ("VOLUME_CHANGE", "company", CO, {"volume_change_pct": -15}, A, "MAT", None, None),
            ("PRICE_VOLUME_CHANGE", "product", p_sub, {"price_change_pct": 5, "volume_change_pct": -3},
             dt.date(2024, 3, 1), "YTD", "subgroup", SUB),
            ("PRICE_VOLUME_CHANGE", "subgroup", SUB, {"price_change_pct": -10, "volume_change_pct": 20},
             dt.date(2024, 2, 1), "MONTH", None, None),
            ("MARKET_GROWTH", "total", "TOTAL", {"market_growth_pct": 8}, A, "MAT", None, None),
            ("MARKET_GROWTH", "supergroup", SG, {"market_growth_pct": -5}, A, "MAT", None, None),
            ("MARKET_GROWTH", "molecule", mol, {"market_growth_pct": 12}, dt.date(2024, 3, 1), "YTD", None, None),
            ("MARKET_SHARE", "product", p_sub, {"target_share_pct": 25.5, "market_growth_pct": 5}, A, "MAT", "subgroup", SUB),
            ("MARKET_SHARE", "company", CO, {"target_share_pct": 10.0}, A, "MAT", "supergroup", SG),
            ("MARKET_SHARE", "company", CO, {"target_share_pct": 3.3, "market_growth_pct": -2}, A, "MAT", "total", "TOTAL"),
            ("PRICE_CHANGE", "product", zero, {"price_change_pct": 10}, A, "MONTH", None, None),          # insufficient
            ("MARKET_GROWTH", "product", P, {"market_growth_pct": 5}, A, "MAT", None, None),              # invalid entity
            ("PRICE_CHANGE", "product", P, {"price_change_pct": -100}, A, "MAT", None, None),             # out of range
            ("PRICE_CHANGE", "product", P, {"price_change_pct": 10, "volume_change_pct": 5}, A, "MAT", None, None),  # extra
        ]
        param = {"price_change_pct": ("Price Change", "Price Change %"), "volume_change_pct": ("Volume Change", "Volume Change %"),
                 "market_growth_pct": ("Market Growth Assumption", "Market Growth %"), "target_share_pct": ("Target Share", "Target Share %")}
        ent = {"product": ("Product", "Product Code"), "company": ("Company", "Company"), "subgroup": ("Market", "Subgroup"),
               "supergroup": ("Market", "Supergroup"), "therapy_group": ("Market", "Therapy Group"), "molecule": ("Pack", "Molecule")}
        fields = [("b_value", "Baseline Value (OBSERVED)"), ("b_units", "Baseline Units (OBSERVED)"),
                  ("b_qty", "Baseline Qty (OBSERVED)"), ("b_price", "Baseline Price per Pack (OBSERVED)"),
                  ("b_market", "Baseline Market Value (OBSERVED)"), ("b_share", "Baseline Share (OBSERVED)"),
                  ("r_value", "Scenario Value (CALCULATED)"), ("r_units", "Scenario Units (CALCULATED)"),
                  ("r_qty", "Scenario Qty (CALCULATED)"), ("r_price", "Scenario Price per Pack (CALCULATED)"),
                  ("r_market", "Scenario Market Value (CALCULATED)"), ("a_value", "Scenario Value Change (CALCULATED)"),
                  ("p_value", "Scenario Value Change % (CALCULATED)"), ("p_units", "Scenario Units Change % (CALCULATED)"),
                  ("p_price", "Scenario Price Change % (CALCULATED)"), ("d_price", "Price Effect (CALCULATED)"),
                  ("d_volume", "Volume Effect (CALCULATED)"), ("d_inter", "Price x Volume Interaction (CALCULATED)"),
                  ("a_share", "Scenario Share Change pp (CALCULATED)"), ("p_market", "Scenario Market Value Change % (CALCULATED)"),
                  ("status_text", "Scenario Status")]
        for i, (st, et, key, assm, anchor, basis, ml, mk) in enumerate(cases, 1):
            res = Result(f"SCN-{i:02d}-{st}-{et}", "scenario",
                         f"{st} on {et}{' within ' + ml if ml else ''}, {basis} {anchor:%b %Y}, assumptions {sorted(assm)}")
            t0 = time.perf_counter()
            try:
                out = run_scenario(eng, st, et, key, assm, anchor, basis, ml, mk)
                ref_code = "OK"
            except AnalyticsError as e:
                out, ref_code = None, e.code
            res.seconds_ref = time.perf_counter() - t0
            flt = [f_anchor(anchor), f_basis(basis), f_eq("Scenario Type", "Scenario Type", st)]
            if et != "total":
                flt.append(f_eq(*ent[et], key))
            if ml and ml != "total":
                flt.append(f_eq(*ent[ml], mk))
            for k, v in assm.items():
                flt.append(f_eq(*param[k], v))
            rows, res.seconds_pbi = self.dax(row_query(flt, fields))
            p = rows[0]
            if out is None:
                # the reference rejects: Power BI must show no result and a non-OK status of the same kind
                expect = {"insufficient_baseline": "INSUFFICIENT BASELINE", "invalid_parameter": "INVALID PARAMETER",
                          "invalid_assumption": ("INVALID ASSUMPTION", "SELECT: choose one")}[ref_code]
                expect = expect if isinstance(expect, tuple) else (expect,)
                ok = p["status_text"].startswith(expect) and all(p[a] is None for a, _ in fields if a != "status_text")
                res.rows_ref = res.rows_pbi = res.fields = res.values_compared = 1
                res.status = "PASS" if ok else "FAIL"
                res.note = f"reference rejects ({ref_code}); Power BI status: {p['status_text']}"
                if not ok:
                    res.examples.append({"ref": ref_code, "pbi": p})
                self.add(res)
                continue
            b, r_, a_, pc = out["baseline"], out["scenario_result"], out["absolute_change"], out["percentage_change"]
            dec = a_.get("value_decomposition_cr", {})
            ref = {"b_value": b["value_cr"], "b_units": b["units_k"], "b_qty": b["qty_k"], "b_price": b["price_rs_per_pack"],
                   "r_value": r_["value_cr"], "a_value": a_["value_cr"], "p_value": pc["value_pct"], "status_text": "OK"}
            if st in ("PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE"):
                ref.update(r_units=r_["units_k"], r_qty=r_["qty_k"], r_price=r_["price_rs_per_pack"],
                           p_units=pc["units_pct"], p_price=pc["price_pct"], d_price=dec["price_effect"],
                           d_volume=dec["volume_effect"], d_inter=dec["price_x_volume_interaction"])
            if st == "MARKET_SHARE":
                ref.update(b_market=b["market_value_cr"], b_share=b["share_pct"], r_market=r_["market_value_cr"],
                           a_share=a_["share_pp"], p_market=pc["market_value_pct"])
            # fields the reference does not produce for this type must be BLANK in Power BI
            full = {a: ref.get(a) for a, _ in fields}
            self.add(compare(res, {"x": full}, {"x": p}, [(a, kind_of(a)) for a, _ in fields]))

    def visual_query(self, group: list[str], measures: list[str], filters: list[str], topn: tuple | None = None,
                     order_measure: str | None = None) -> str:
        """The query shape Power BI visuals send: SUMMARIZECOLUMNS in a variable, windowed by TOPN(501).
        topn = (table, column, measure, n) reproduces a visual-level Top N filter."""
        pre = ""
        flt = list(filters)
        if topn:
            t, col, m, n = topn
            pre = (f"VAR __top = CALCULATETABLE ( TOPN ( {n}, VALUES ( {c(t, col)} ), [{m}], DESC ), "
                   + ", ".join(filters) + " )\n")
            flt = ["__top"] + flt
        parts = group + flt + [f"{s('m' + str(i))}, [{m}]" for i, m in enumerate(measures)]
        om = f"[m{measures.index(order_measure)}]" if order_measure else "[m0]"
        return ("DEFINE\n" + pre + "VAR __core = SUMMARIZECOLUMNS (\n    " + ",\n    ".join(parts) + "\n)\n"
                + f"EVALUATE TOPN ( 501, __core, {om}, DESC )")

    def run_visual_shapes(self):
        """Ranks as the report's visuals query them (outer TOPN window, several grouped attributes,
        Top N filter). A rank that is right in a flat query can be wrong in a visual (found in M12)."""
        A = dt.date(2024, 5, 1)
        F = [f_anchor(A), f_basis("MAT")]
        sub = self.top["subgroup"]
        cases = [
            ("VS-product-code+label-in-subgroup", "Product table grouped by code and label, largest subgroup",
             ["'Product'[Product Code]", "'Product'[Product]"], F + [f_eq("Market", "Subgroup", sub)], None,
             lambda: self.api.get_brand_performance(A, "MAT", "subgroup", sub, top_n=None)["rows"], "Product Code", "Product"),
            ("VS-product-label-only-in-subgroup", "Top-products bar grouped by label only, largest subgroup",
             ["'Product'[Product]"], F + [f_eq("Market", "Subgroup", sub)], None,
             lambda: self.api.get_brand_performance(A, "MAT", "subgroup", sub, top_n=None)["rows"], None, "Product"),
            ("VS-product-top500-national", "Brand & Portfolio table: national, Top 500 visual filter, code + label",
             ["'Product'[Product Code]", "'Product'[Product]"], F, ("Product", "Product Code", "Value", 500),
             lambda: self.api.get_brand_performance(A, "MAT", top_n=500)["rows"], "Product Code", "Product"),
            ("VS-company+mnc-national", "Company table grouped by company and Indian/MNC, national",
             ["'Company'[Company]", "'Company'[Indian/MNC]"], F, None,
             lambda: self.api.get_company_performance(A, "MAT", top_n=None)["rows"], "Company", "Company"),
            ("VS-subgroup-in-matrix", "Market matrix grouped by supergroup and subgroup, national",
             ["'Market'[Supergroup]", "'Market'[Subgroup]"], F, None,
             lambda: self.api.get_market_performance("subgroup", A, "MAT")["rows"], "Subgroup", "Subgroup"),
        ]
        label_map = None
        for cid, desc, group, flt, topn, ref_fn, keycol, rankcol in cases:
            res = Result(cid, "visual-shape", desc)
            label = {"Product": "Product", "Company": "Company", "Subgroup": "Subgroup"}[rankcol]
            meas = ["Value", f"Rank Value ({label})"] + ([f"Rank Growth ({label})"] if not topn else [])
            t0 = time.perf_counter()
            ref_rows = ref_fn()
            res.seconds_ref = time.perf_counter() - t0
            rows, res.seconds_pbi = self.dax(self.visual_query(group, meas, flt, topn))
            if keycol is None:     # label-only grouping: map labels back to product codes
                if label_map is None:
                    label_map = {r["Product"]: r["Product Code"] for r in desktop.query(
                        self.pid, "EVALUATE SUMMARIZECOLUMNS ( 'Product'[Product Code], 'Product'[Product] )")}
                pbi = {label_map[r["Product"]]: r for r in rows}
            else:
                pbi = {str(r[keycol]): r for r in rows}
            # Power BI returns the visual's first window (501 rows by value): compare those rows
            ref = {r["entity_key"]: r for r in ref_rows}
            fields = [("m1", "int")] + ([("m2", "int")] if not topn else [])
            refc = {k: {"m1": ref[k]["rank_value"], **({"m2": ref[k]["rank_growth"]} if not topn else {})} for k in pbi}
            self.add(compare(res, refc, pbi, fields))

    def run_visual_smoke(self, pages) -> list[dict]:
        """Execute every visual's fields in the visual query shape (catches broken bindings; gives timings)."""
        from .report import ref as fref
        out = []
        A = dt.date(2024, 5, 1)
        F = [f_anchor(A), f_basis("MAT")]
        for p in pages:
            for v in p.visuals:
                if v.vtype in ("textbox", "actionButton"):
                    continue
                cols, meas = [], []
                for f in v.fields():
                    ent, prop = fref(f)
                    (meas if "Measure" in f else cols).append((ent, prop))
                cols = list(dict.fromkeys(cols))
                meas = [m[1] for m in dict.fromkeys(meas)]
                topn = None
                for flt in v.filters:
                    if flt["type"] == "TopN":
                        sq = flt["filter"]["From"][0]["Expression"]["Subquery"]["Query"]
                        topn = (flt["field"]["Column"]["Expression"]["SourceRef"]["Entity"], flt["field"]["Column"]["Property"],
                                sq["OrderBy"][0]["Expression"]["Measure"]["Property"], sq["Top"])
                if cols:
                    q = self.visual_query([c(*x) for x in cols], meas or ["Value"], F, topn)
                elif meas:
                    q = ("EVALUATE CALCULATETABLE ( ROW ( " + ", ".join(f"{s('m' + str(i))}, [{m}]" for i, m in enumerate(meas))
                         + f" ), {', '.join(F)} )")
                else:
                    continue
                try:
                    rows, sec = self.dax(q)
                    out.append({"page": p.display, "visual": v.name, "type": v.vtype, "rows": len(rows),
                                "seconds": round(sec, 3), "ok": True})
                except (desktop.DesktopError, subprocess.TimeoutExpired) as e:
                    out.append({"page": p.display, "visual": v.name, "type": v.vtype, "ok": False, "error": str(e)[-400:]})
        return out


def write_reports(rec: Reconciler, smoke: list[dict], meta: dict) -> dict:
    REPORTS.mkdir(parents=True, exist_ok=True)
    rows = [r.__dict__ for r in rec.results]
    summary = {"cases": len(rows), "pass": sum(r["status"] == "PASS" for r in rows),
               "fail": sum(r["status"] == "FAIL" for r in rows),
               "values_compared": sum(r["values_compared"] for r in rows),
               "blank_checks": sum(r["blank_checks"] for r in rows),
               "max_rel_diff": max((r["max_rel_diff"] for r in rows), default=0.0),
               "visual_queries": len(smoke), "visual_query_failures": sum(not x["ok"] for x in smoke), **meta}
    (REPORTS / f"powerbi_reconciliation{_suffix()}.json").write_text(
        json.dumps({"summary": summary, "cases": rows, "visual_smoke": smoke}, indent=2, default=str), encoding="utf-8")
    with open(REPORTS / f"powerbi_reconciliation{_suffix()}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[k for k in rows[0] if k != "examples"])
        w.writeheader()
        for r in rows:
            w.writerow({k: v for k, v in r.items() if k != "examples"})
    # committed summary: no business values, only counts, differences and verdicts
    L = [f"# Power BI reconciliation report ({'M13 — SYNTHETIC dataset' if _suffix() else 'M12'})", "",
         f"Generated by `python -m pci_powerbi.reconcile` on {meta['run_at']} against the live model in Power BI Desktop "
         f"{meta.get('desktop_version', '')}. Reference: SQL engine (metrics), canonical Python engine (opportunity "
         "OPP-1.0.0, scenarios SCN-1.0.0), plus one three-way check against the independent Python metric engine.", "",
         f"Tolerance: |Power BI − reference| ≤ {REL_TOL:g} × max(1, |reference|) for numbers; exact for text, "
         "status and ranks; BLANK must match NULL exactly (no silent zeros). Entity key sets must be identical.", "",
         "This file intentionally contains no business values. Detailed results with values stay local in "
         f"`powerbi_reconciliation{_suffix()}.{{json,csv}}` next to this file (not tracked).", "",
         "## Summary", "",
         "| Cases | Pass | Fail | Values compared | NULL/BLANK checks | Max relative difference | Visual queries | Visual query failures |",
         "|---|---|---|---|---|---|---|---|",
         f"| {summary['cases']} | {summary['pass']} | {summary['fail']} | {summary['values_compared']:,} | "
         f"{summary['blank_checks']:,} | {summary['max_rel_diff']:.2e} | {summary['visual_queries']} | {summary['visual_query_failures']} |",
         "", "## Cases", "",
         "| Case | Area | What is compared | Ref rows | PBI rows | Fields | Values | Max abs diff | Max rel diff | Tolerance | Result |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        tol = f"{REL_TOL:g} rel" if r["area"] not in ("guard",) else "exact"
        desc = r["description"] + (f" — {r['note']}" if r["note"] else "")
        L.append(f"| {r['case']} | {r['area']} | {desc} | {r['rows_ref']:,} | {r['rows_pbi']:,} | {r['fields']} | "
                 f"{r['values_compared']:,} | {r['max_abs_diff']:.1e} | {r['max_rel_diff']:.1e} | {tol} | **{r['status']}** |")
    L += ["", "## Visual query smoke test", "",
          "Each report visual's fields are executed as a DAX query (default anchor, MAT). Timings are end-to-end "
          "through the local bridge (includes ~1–2 s PowerShell start-up per query).", "",
          "| Page | Visuals | Failures | Slowest (s) |", "|---|---|---|---|"]
    by = {}
    for x in smoke:
        d = by.setdefault(x["page"], [0, 0, 0.0])
        d[0] += 1
        d[1] += not x["ok"]
        d[2] = max(d[2], x.get("seconds", 0.0))
    for pg, (n, f, mx) in by.items():
        L.append(f"| {pg} | {n} | {f} | {mx:.2f} |")
    (REPORTS / f"POWER_BI_RECONCILIATION{_suffix().upper()}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return summary


def main():
    from .build import latest_anchor_label
    from .report import pages
    t0 = time.time()
    (REPORTS / f"powerbi_reconciliation{_suffix()}_progress.jsonl").unlink(missing_ok=True)
    rec = Reconciler()
    rec.run_metrics()
    rec.run_trends()
    rec.run_guards()
    rec.run_opportunity()
    rec.run_scenarios()
    rec.run_visual_shapes()
    from pci_analytics.opportunity_config import DEFAULT_CONFIG
    from pci_analytics.scenario import METHODOLOGY_VERSION
    # release the reference engines (DuckDB buffer pool, Python engine) before the heavier visual queries
    rec.api.con.close()
    rec.engine = None
    import gc
    gc.collect()
    smoke = rec.run_visual_smoke(pages(latest_anchor_label(), {"opportunity": DEFAULT_CONFIG.version,
                                                               "fingerprint": DEFAULT_CONFIG.fingerprint(),
                                                               "scenario": METHODOLOGY_VERSION}))
    ver = desktop._ps("(Get-AppxPackage -Name Microsoft.MicrosoftPowerBIDesktop).Version")
    summary = write_reports(rec, smoke, {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
                                         "desktop_version": ver, "seconds": round(time.time() - t0, 1)})
    print(json.dumps(summary, indent=2))
    for r in rec.results:
        if r.status != "PASS":
            print("FAIL", r.case, r.examples[:4], r.note)
    for x in smoke:
        if not x["ok"]:
            print("VISUAL FAIL", x["page"], x["visual"], x["error"][-300:])
    return summary


if __name__ == "__main__":
    main()
