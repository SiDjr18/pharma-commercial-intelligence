"""Export canonical opportunity scores (M6, OPP-1.0.0) for import into Power BI.

The score is computed ONLY by pci_analytics.opportunity.score_all (the single canonical implementation);
this module copies its output fields verbatim into Parquet. Power BI never recomputes a score.

Anchors exported: every month whose MAT / calendar-YTD window AND comparison window are complete
(earlier anchors are all INSUFFICIENT_EVIDENCE / insufficient_history; the report states that instead
of importing ~1.6M rows that carry no score).

Output (derived, never tracked): <active dataset folder>/powerbi/opportunity_{product,market}.parquet + _manifest.json
(IMS: <PCI_IMS_DATA_DIR>/powerbi, outside the repository; synthetic: data/synthetic/powerbi, git-ignored)
Run:  cd python && ..\\.venv\\Scripts\\python.exe -m pci_powerbi.export
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from pci_analytics import periods
from pci_analytics.engine import default_engine
from pci_analytics.opportunity import QUADRANTS, SCORED, score_all
from pci_analytics.opportunity_config import DEFAULT_CONFIG
from pci_data.db import connect
from pci_data.schema import DATASET, PROCESSED_DIR, assert_outside_repo

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = PROCESSED_DIR               # active dataset (PCI_DATASET; default synthetic)
OUT = PROCESSED / "powerbi"

REASON_LABELS = {
    "insufficient_history": "Insufficient: no comparison window",
    "prior_zero": "Insufficient: no prior sales",
    "below_materiality": "Insufficient: below materiality",
    "market_zero_current": "Insufficient: market has no sales",
    "single_product_market": "Insufficient: < 2 active products",
}
_SIDES = {name: key for key, name in QUADRANTS.items()}   # quadrant -> (ei >= 100, growth >= national)

# Power BI compares text case-insensitively and ignores trailing spaces; DuckDB does not.
KEY_COLUMNS = ("company", "subgroup", "supergroup", "therapy_group", "molecule_desc", "index_desc",
               "form_short_desc", "nfc1", "plain_combination", "acute_chronic", "indian_mnc")


def assert_keys_powerbi_safe(con=None) -> dict:
    """Fail if two distinct keys would collapse into one in Power BI (case / trailing-space collision)."""
    con = con or connect()
    out = {}
    for c in KEY_COLUMNS:
        a, b = con.execute(f"SELECT count(DISTINCT {c}), count(DISTINCT upper(rtrim({c}))) FROM pack").fetchone()
        out[c] = (a, b)
        if a != b:
            raise AssertionError(f"{c}: {a} distinct keys but {b} after case/trailing-space folding; "
                                 "Power BI would merge entities")
    return out


def eligible_anchors(engine, cfg=DEFAULT_CONFIG) -> list[tuple[dt.date, str]]:
    out = []
    a = engine.first_period
    while a <= engine.last_period:
        for b in cfg.allowed_bases:
            w = periods.make_window(a, b, engine.first_period)
            if w.current_complete and w.prior_complete:
                out.append((a, b))
        a = (a.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def _comp(r, name, key):
    for c in r["components"]:
        if c["name"] == name:
            return c[key]
    raise KeyError(name)


def _evidence(r):
    return "Scored" if r["score_status"] == SCORED else REASON_LABELS[r["insufficient_reason"]]


def product_rows(res, anchor, basis):
    rows = []
    for r in res["rows"]:
        q = r.get("matrix_quadrant")
        ei_side, g_side = _SIDES[q] if q else (None, None)
        rows.append({
            "Anchor": anchor, "Basis": basis, "Entity Key": r["entity_key"], "Product in Market": r["entity_label"],
            "prod_code": int(r["prod_code"]), "Product Code": str(r["prod_code"]),
            "Subgroup": r["subgroup"], "Company": r["company"],
            "Score Status": r["score_status"], "Insufficient Reason": r["insufficient_reason"], "Evidence": _evidence(r),
            "Score": r["score"], "Opportunity Rank": r["opportunity_rank"],
            "Market Growth %": r["market_value_growth_pct"], "Evolution Index": r["evolution_index"],
            "Share in Market %": r["value_share_in_market_pct"],
            "Market Growth Normalized": _comp(r, "market_growth", "normalized"),
            "Momentum Normalized": _comp(r, "relative_momentum", "normalized"),
            "Matrix Quadrant": q,
            "Momentum Side": None if q is None else ("Gaining share (EI ≥ 100)" if ei_side else "Losing share (EI < 100)"),
            "Market Growth Side": None if q is None else ("Market ≥ national" if g_side else "Market < national"),
            "Methodology Version": r["methodology_version"],
        })
    return rows


def market_rows(res, anchor, basis):
    return [{
        "Anchor": anchor, "Basis": basis, "Subgroup": r["entity_key"],
        "Score Status": r["score_status"], "Insufficient Reason": r["insufficient_reason"], "Evidence": _evidence(r),
        "Score": r["score"], "Opportunity Rank": r["opportunity_rank"],
        "Market Growth %": r["value_growth_pct"], "Market Size Share %": r["value_share_of_total_pct"],
        "Growth Normalized": _comp(r, "market_growth", "normalized"),
        "Size Normalized": _comp(r, "market_size", "normalized"),
        "Value Cur": r["value_cur"], "Value Prior": r["value_prior"],
        "Methodology Version": r["methodology_version"],
    } for r in res["rows"]]


INT_COLS = {"prod_code", "Opportunity Rank"}
DATE_COLS = {"Anchor"}
TEXT_COLS = {"Basis", "Entity Key", "Product in Market", "Product Code", "Subgroup", "Company",
             "Score Status", "Insufficient Reason", "Evidence", "Matrix Quadrant", "Momentum Side",
             "Market Growth Side", "Methodology Version"}


def _schema(types) -> pa.Schema:
    def t(name):
        if name in DATE_COLS:
            return pa.date32()
        if name in INT_COLS:
            return pa.int64()
        if name in TEXT_COLS:
            return pa.string()
        return pa.float64()
    return pa.schema([(name, t(name)) for name, _ in types])


def _table(rows, schema: pa.Schema) -> pa.Table:
    return pa.table({f.name: pa.array([r[f.name] for r in rows], type=f.type) for f in schema}, schema=schema)


def run(engine=None, anchors=None, out_dir: Path = OUT) -> dict:
    """Streams one anchor/basis at a time (one row group each) so memory stays bounded while
    Power BI Desktop is open."""
    from .model import OPP_MARKET_TYPES, OPP_PRODUCT_TYPES
    t0 = time.time()
    assert_keys_powerbi_safe()
    engine = engine or default_engine()
    anchors = anchors or eligible_anchors(engine)
    if DATASET == "ims":
        assert_outside_repo(out_dir)          # IMS-derived exports never enter the repository
    out_dir.mkdir(parents=True, exist_ok=True)
    sp, sm = _schema(OPP_PRODUCT_TYPES), _schema(OPP_MARKET_TYPES)
    tmp_p, tmp_m = out_dir / "opportunity_product.parquet.tmp", out_dir / "opportunity_market.parquet.tmp"
    n_p = n_m = 0
    with pq.ParquetWriter(tmp_p, sp, compression="zstd") as wp, pq.ParquetWriter(tmp_m, sm, compression="zstd") as wm:
        for a, b in anchors:
            rows = product_rows(score_all(engine, "product", a, b), a, b)
            wp.write_table(_table(rows, sp))
            n_p += len(rows)
            rows = market_rows(score_all(engine, "market", a, b), a, b)
            wm.write_table(_table(rows, sm))
            n_m += len(rows)
            engine.__dict__.pop("_opportunity_cache", None)      # bounded memory
    tmp_p.replace(out_dir / "opportunity_product.parquet")
    tmp_m.replace(out_dir / "opportunity_market.parquet")
    src = json.loads((PROCESSED / "_manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "methodology_version": DEFAULT_CONFIG.version, "config_fingerprint": DEFAULT_CONFIG.fingerprint(),
        "engine": "pci_analytics.opportunity.score_all (canonical)",
        "anchors": [[a.isoformat(), b] for a, b in anchors],
        "rows": {"opportunity_product": n_p, "opportunity_market": n_m},
        "source_sha256": src["source"]["sha256"], "processed_built_at": src["build"]["built_at"],
        "exported_at": dt.datetime.now().isoformat(timespec="seconds"), "seconds": round(time.time() - t0, 1),
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    m = run()
    print(json.dumps({k: m[k] for k in ("methodology_version", "config_fingerprint", "rows", "seconds")}, indent=2))
    print(f"{len(m['anchors'])} anchor/basis combinations -> {OUT}")
