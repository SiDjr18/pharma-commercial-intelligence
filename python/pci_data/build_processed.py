"""One controlled conversion: IMS workbook (sheet DATA) -> Parquet analytical layer.

- Opens the source read-only (openpyxl read_only streaming). Never writes to it.
- Streams in chunks; the long fact table is written incrementally.
- Records independent source-side control totals (computed from raw cells) in
  data/processed/_manifest.json for reconciliation tests.
- Writes to *.tmp files and swaps them in only after a successful build.

Run:  .venv\\Scripts\\python.exe -m pci_data.build_processed   (from python/)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
from collections import defaultdict

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq

from .schema import (DESCRIPTIVE, INT_DESCRIPTIVE, MANIFEST_PATH, PROCESSED_DIR, SOURCE_PATH,
                     SOURCE_SHEET, classify, yyyymm_to_date)

CHUNK_ROWS = 5_000
COMPRESSION = "zstd"

# Source-side control totals by dimension (raw column names, computed from raw cells).
CONTROL_DIMS = ["SUPERGROUP", "ACUTE_CHRONIC", "INDIAN_MNC", "Plain/Combination", "SHORT DESCRIPTION", "COMPANY"]
CONTROL_MEASURES = ["MAT MAY'22", "MAT MAY'23", "MAT MAY'24", "UNIT MAT MAY'24", "QTY MAT MAY'24", "MAY'24"]
NULL_KEY = "__NULL__"

PACK_SCHEMA = pa.schema(
    [("source_row", pa.int32())]
    + [(t, pa.int64() if t in INT_DESCRIPTIVE else pa.string()) for _, t in DESCRIPTIVE]
    + [("pack_launch_month", pa.date32()), ("prod_launch_month", pa.date32())]
)
FACT_SCHEMA = pa.schema([("pfc", pa.int64()), ("period", pa.date32()),
                         ("value_cr", pa.float64()), ("units_k", pa.float64()), ("qty_k", pa.float64())])
PRICE_SCHEMA = pa.schema([("pfc", pa.int64()), ("period", pa.date32()), ("price_rs", pa.float64())])


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def to_float(v, col):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(f"non-numeric value in measure column {col!r}: type {type(v).__name__}")
    return float(v)


def build() -> dict:
    t0 = time.time()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    src_stat = SOURCE_PATH.stat()
    src_hash_before = sha256(SOURCE_PATH)

    wb = openpyxl.load_workbook(SOURCE_PATH, read_only=True, data_only=True)
    ws = wb[SOURCE_SHEET]
    dimension = ws.calculate_dimension()
    rows = ws.iter_rows(values_only=True)
    header = list(next(rows))
    specs = classify(header)

    desc_specs = [s for s in specs if s.kind == "descriptive"]
    monthly = defaultdict(list)                       # period -> {measure: spec}
    for s in specs:
        if s.kind == "monthly":
            monthly[s.period].append(s)
    periods = sorted(monthly)
    month_idx = [{s.target: s.position for s in monthly[p]} for p in periods]
    snap_years = sorted({s.snapshot_year for s in specs if s.kind in ("snapshot", "ni", "split")})
    snap_idx = {y: {s.target: s.position for s in specs
                    if s.kind in ("snapshot", "ni", "split") and s.snapshot_year == y} for y in snap_years}
    snap_cols = sorted({t for y in snap_years for t in snap_idx[y]})
    price_specs = sorted([s for s in specs if s.kind == "price"], key=lambda s: s.period)
    numeric_positions = [s.position for s in specs if s.kind != "descriptive"] + \
                        [s.position for s in desc_specs if s.target in INT_DESCRIPTIVE]
    pos = {h: i for i, h in enumerate(header)}

    tmp = {name: PROCESSED_DIR / f"{name}.parquet.tmp"
           for name in ("pack", "fact_pack_month", "pack_snapshot", "pack_price_month")}
    fact_writer = pq.ParquetWriter(tmp["fact_pack_month"], FACT_SCHEMA, compression=COMPRESSION)
    pack_cols = defaultdict(list)
    snap_data = defaultdict(list)
    price_data = defaultdict(list)

    # Source-side controls from raw cells
    col_partial_sums = defaultdict(list)
    col_non_null = defaultdict(int)
    dim_totals = {d: defaultdict(lambda: defaultdict(float)) for d in CONTROL_DIMS}
    n_rows, blank = 0, 0
    buf = []

    def flush(numbered):
        f = defaultdict(list)
        chunk = [r for _, r in numbered]
        for excel_no, row in numbered:
            # descriptive
            pack_cols["source_row"].append(excel_no)
            for s in desc_specs:
                v = row[s.position]
                if s.target in INT_DESCRIPTIVE and v is not None:
                    v = int(v)
                pack_cols[s.target].append(v)
            pfc = int(row[pos["PFC"]])
            pack_cols["pack_launch_month"].append(yyyymm_to_date(row[pos["PACK_LNCH"]]))
            pack_cols["prod_launch_month"].append(yyyymm_to_date(row[pos["PROD_LNCH"]]))
            # long monthly fact
            for p, idx in zip(periods, month_idx):
                f["pfc"].append(pfc)
                f["period"].append(p)
                for m in ("value_cr", "units_k", "qty_k"):
                    f[m].append(to_float(row[idx[m]], header[idx[m]]))
            # snapshots
            for y in snap_years:
                snap_data["pfc"].append(pfc)
                snap_data["snapshot_year"].append(y)
                snap_data["snapshot_month"].append(dt.date(y, 5, 1))
                for c in snap_cols:
                    i = snap_idx[y].get(c)
                    snap_data[c].append(to_float(row[i], header[i]) if i is not None else None)
            # price
            for s in price_specs:
                price_data["pfc"].append(pfc)
                price_data["period"].append(s.period)
                price_data["price_rs"].append(to_float(row[s.position], s.source))
            # controls
            for d in CONTROL_DIMS:
                key = row[pos[d]]
                key = NULL_KEY if key is None else str(key)
                for m in CONTROL_MEASURES:
                    v = row[pos[m]]
                    if v is not None:
                        dim_totals[d][key][m] += float(v)
        for i in numeric_positions:
            vals = [float(r[i]) for r in chunk if r[i] is not None]
            col_partial_sums[header[i]].append(math.fsum(vals))
            col_non_null[header[i]] += len(vals)
        fact_writer.write_table(pa.table(f, schema=FACT_SCHEMA))

    excel_row = 2
    for row in rows:
        if row is None or all(v is None for v in row):
            blank += 1
            excel_row += 1
            continue
        if len(row) != len(header):
            raise ValueError(f"row {excel_row} has {len(row)} cells, expected {len(header)}")
        buf.append((excel_row, row))
        if len(buf) >= CHUNK_ROWS:
            flush(buf)
            n_rows += len(buf)
            buf = []
            print(f"  {n_rows:,} source rows converted", flush=True)
        excel_row += 1
    if buf:
        flush(buf)
        n_rows += len(buf)
    wb.close()
    fact_writer.close()

    snap_schema = pa.schema([("pfc", pa.int64()), ("snapshot_year", pa.int16()), ("snapshot_month", pa.date32())]
                            + [(c, pa.float64()) for c in snap_cols])
    pq.write_table(pa.table(pack_cols, schema=PACK_SCHEMA), tmp["pack"], compression=COMPRESSION)
    pq.write_table(pa.table(snap_data, schema=snap_schema), tmp["pack_snapshot"], compression=COMPRESSION)
    pq.write_table(pa.table(price_data, schema=PRICE_SCHEMA), tmp["pack_price_month"], compression=COMPRESSION)

    if sha256(SOURCE_PATH) != src_hash_before:
        raise RuntimeError("source file changed during build — aborting")
    outputs = {}
    for name, p in tmp.items():
        final = PROCESSED_DIR / f"{name}.parquet"
        os.replace(p, final)
        outputs[name] = {"file": final.name, "rows": pq.ParquetFile(final).metadata.num_rows,
                         "bytes": final.stat().st_size}

    manifest = {
        "source": {"path": str(SOURCE_PATH), "sheet": SOURCE_SHEET, "sheet_dimension": dimension,
                   "size_bytes": src_stat.st_size,
                   "mtime": dt.datetime.fromtimestamp(src_stat.st_mtime).isoformat(timespec="seconds"),
                   "sha256": src_hash_before, "header": header,
                   "data_rows": n_rows, "blank_rows_skipped": blank},
        "build": {"built_at": dt.datetime.now().isoformat(timespec="seconds"),
                  "seconds": round(time.time() - t0, 1), "python": sys.version.split()[0],
                  "pyarrow": pa.__version__, "openpyxl": openpyxl.__version__, "compression": COMPRESSION},
        "periods": [p.isoformat() for p in periods],
        "snapshot_years": snap_years,
        "price_periods": [s.period.isoformat() for s in price_specs],
        "source_column_controls": {h: {"sum": math.fsum(col_partial_sums[h]), "non_null": col_non_null[h]}
                                   for h in col_partial_sums},
        "source_dimension_controls": {d: {k: dict(v) for k, v in t.items()} for d, t in dim_totals.items()},
        "column_map": [{"position": s.position, "source": s.source, "kind": s.kind, "target": s.target,
                        "period": s.period.isoformat() if s.period else None,
                        "snapshot_year": s.snapshot_year} for s in specs],
        "outputs": outputs,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"built in {manifest['build']['seconds']}s: " +
          ", ".join(f"{k}={v['rows']:,} rows/{v['bytes']/1e6:.1f} MB" for k, v in outputs.items()))
    return manifest


if __name__ == "__main__":
    build()
