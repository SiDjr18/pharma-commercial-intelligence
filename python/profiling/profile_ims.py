"""Profile the private IMS source workbook locally (read-only, streamed).

- Opens the private workbook (PCI_SOURCE_PATH) in openpyxl read-only mode (streamed XML).
- Never writes to, renames, moves or copies the source file.
- Writes only derived metadata / aggregates to data/profile/.
- Prints no raw rows.

Usage:  python python/profiling/profile_ims.py
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pci_data.schema import PROJECT_ROOT, SOURCE_PATH as SOURCE  # noqa: E402  (PCI_SOURCE_PATH-overridable)

PROFILE_DIR = PROJECT_ROOT / "data" / "profile"
SHEET = "DATA"
CHUNK = 10_000

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_COL = re.compile(r"^(?:(UNIT|QTY) )?(" + "|".join(MONTHS) + r")'(\d{2})$")


def month_key(col: str):
    m = MONTH_COL.match(col)
    if not m:
        return None
    prefix, mon, yy = m.groups()
    return (prefix or "VALUE", 2000 + int(yy), MONTHS.index(mon) + 1)


def stream_frame():
    wb = openpyxl.load_workbook(SOURCE, read_only=True, data_only=True)
    ws = wb[SHEET]
    it = ws.iter_rows(values_only=True)
    header = list(next(it))
    type_counts = {h: Counter() for h in header}
    row_hashes = Counter()
    chunks, buf, n = [], [], 0
    blank_rows = 0
    for row in it:
        if row is None or all(v is None for v in row):
            blank_rows += 1
            continue
        buf.append(row)
        if len(buf) >= CHUNK:
            chunks.append(_consume(buf, header, type_counts, row_hashes))
            n += len(buf)
            buf = []
            print(f"  streamed {n:,} rows", flush=True)
    if buf:
        chunks.append(_consume(buf, header, type_counts, row_hashes))
    wb.close()
    df = pd.concat(chunks, ignore_index=True)
    return header, df, type_counts, row_hashes, blank_rows


def _consume(buf, header, type_counts, row_hashes):
    for row in buf:
        row_hashes[hashlib.md5(repr(row).encode()).hexdigest()] += 1
    cols = list(zip(*buf))
    data = {}
    for h, values in zip(header, cols):
        type_counts[h].update(type(v).__name__ for v in values)
        data[h] = list(values)
    return pd.DataFrame(data, columns=header)


def col_profile(s: pd.Series, types: Counter) -> dict:
    p = {
        "python_types": dict(types),
        "null_count": int(s.isna().sum()),
        "null_pct": round(float(s.isna().mean() * 100), 4),
        "distinct_count": int(s.nunique(dropna=True)),
    }
    num = pd.to_numeric(s, errors="coerce")
    non_null = s.notna().sum()
    if non_null and num.notna().sum() == non_null:
        p.update({
            "kind": "numeric",
            "min": float(num.min()), "max": float(num.max()),
            "mean": float(num.mean()), "median": float(num.median()),
            "sum": float(num.sum()),
            "zero_count": int((num == 0).sum()),
            "negative_count": int((num < 0).sum()),
            "non_integer_count": int((num.dropna() % 1 != 0).sum()),
        })
        pos = num[num > 0]
        if len(pos) > 10:
            q1, q3 = pos.quantile([0.25, 0.75])
            p["iqr_outliers_gt_q3_plus_3iqr_of_positive"] = int((pos > q3 + 3 * (q3 - q1)).sum())
    else:
        st = s.dropna().astype(str)
        p.update({
            "kind": "text",
            "min_len": int(st.str.len().min()) if len(st) else None,
            "max_len": int(st.str.len().max()) if len(st) else None,
            "blank_string_count": int((st.str.strip() == "").sum()),
            "leading_trailing_ws_count": int((st != st.str.strip()).sum()),
        })
        if p["distinct_count"] <= 25:
            p["values"] = {str(k): int(v) for k, v in s.value_counts(dropna=False).items()}
    return p


def uniqueness(df, cols):
    sub = df[list(cols)]
    dup = int(sub.duplicated(keep=False).sum())
    return {"columns": list(cols), "null_rows": int(sub.isna().any(axis=1).sum()),
            "duplicate_rows_involved": dup, "is_unique": dup == 0}


def functional_dependency(df, a, b):
    g = df[[a, b]].dropna().groupby(a)[b].nunique()
    return {"determinant": a, "dependent": b, "violating_keys": int((g > 1).sum()), "keys": int(len(g))}


def recon(lhs: pd.Series, rhs: pd.Series, label: str) -> dict:
    lhs = pd.to_numeric(lhs, errors="coerce").fillna(0.0)
    rhs = pd.to_numeric(rhs, errors="coerce").fillna(0.0)
    diff = (lhs - rhs).abs()
    scale = np.maximum(lhs.abs(), rhs.abs())
    return {
        "check": label,
        "rows": int(len(diff)),
        "exact_match": int((diff == 0).sum()),
        "match_abs_le_1": int((diff <= 1).sum()),
        "match_rel_le_0.1pct": int(((diff <= 1) | (diff <= 0.001 * scale)).sum()),
        "max_abs_diff": float(diff.max()),
        "total_lhs": float(lhs.sum()), "total_rhs": float(rhs.sum()),
    }


def main():
    t0 = time.time()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("streaming source (read-only)...", flush=True)
    header, df, type_counts, row_hashes, blank_rows = stream_frame()
    print(f"loaded {len(df):,} rows x {len(header)} cols in {time.time()-t0:.0f}s", flush=True)

    # ---- columns
    dup_headers = [h for h, c in Counter(header).items() if c > 1]
    columns = {h: col_profile(df[h], type_counts[h]) for h in header}

    # ---- measure families
    monthly = {h: month_key(h) for h in header if month_key(h)}
    fam_periods = {}
    for h, (fam, y, m) in monthly.items():
        fam_periods.setdefault(fam, []).append((y, m, h))
    period_summary = {}
    for fam, lst in fam_periods.items():
        lst.sort()
        ym = [(y, m) for y, m, _ in lst]
        expected, (y, m) = [], ym[0]
        while (y, m) <= ym[-1]:
            expected.append((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        period_summary[fam] = {
            "first": f"{ym[0][0]}-{ym[0][1]:02d}", "last": f"{ym[-1][0]}-{ym[-1][1]:02d}",
            "n_periods": len(ym), "missing_periods": [f"{a}-{b:02d}" for a, b in expected if (a, b) not in ym],
        }

    # ---- duplicates / keys
    full_dup = sum(c - 1 for c in row_hashes.values() if c > 1)
    dims = header[:23]
    key_candidates = [("PFC",), ("PROD_CODE",), ("PACK_DESC",), ("PFC", "PROD_CODE"),
                      ("PROD_CODE", "PACK_DESC"), ("PFC", "PACK_DESC"), ("BRANDS", "PACK_DESC"),
                      ("PFC", "PROD_CODE", "MANUFAC CODE"), tuple(dims)]
    keys = [uniqueness(df, k) for k in key_candidates]
    fds = [functional_dependency(df, a, b) for a, b in [
        ("PFC", "PACK_DESC"), ("PFC", "PROD_CODE"), ("PFC", "BRANDS"), ("PROD_CODE", "BRANDS"),
        ("PROD_CODE", "MANUFAC CODE"), ("PROD_CODE", "PROD_LNCH"), ("PFC", "PACK_LNCH"),
        ("MANUFAC CODE", "MANUFACT. DESC"), ("MANUFACT. DESC", "COMPANY"), ("MANUFAC CODE", "COMPANY"),
        ("COMPANY", "INDIAN_MNC"), ("SUBGROUP", "GROUP"), ("GROUP", "SUPERGROUP"),
        ("NFC 3", "NFC 2"), ("NFC 2", "NFC 1"), ("PFC", "MOLECULE_DESC"), ("BRANDS", "COMPANY"),
        ("SUBGROUP", "ACUTE_CHRONIC"), ("MOLECULE_DESC", "Plain/Combination"),
    ] if a in df and b in df]

    # ---- reconciliation (does source internally reconcile?)
    def mcols(fam, months):
        return [h for h, k in monthly.items() if k[0] == fam and (k[1], k[2]) in months]

    def window(end_y, end_m, n):
        out, y, m = [], end_y, end_m
        for _ in range(n):
            out.append((y, m))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        return set(out)

    recs = []
    for fam, pre in [("VALUE", ""), ("UNIT", "UNIT "), ("QTY", "QTY ")]:
        for yy in (22, 23, 24):
            y = 2000 + yy
            mat_c, month_c, cumm_c = f"{pre}MAT MAY'{yy}", f"{pre}MONTH MAY'{yy}", f"{pre}CUMM MAY'{yy}"
            may_c = f"{pre}MAY'{yy}"
            if month_c in df and may_c in df:
                recs.append(recon(df[month_c], df[may_c], f"{month_c} == {may_c}"))
            w = mcols(fam, window(y, 5, 12))
            if mat_c in df and len(w) == 12:
                recs.append(recon(df[mat_c], df[w].apply(pd.to_numeric, errors="coerce").sum(axis=1),
                                  f"{mat_c} == sum of 12 monthly cols ending May'{yy}"))
            for label, n in [("Apr-May (Indian FY YTD)", 2), ("Jan-May (calendar YTD)", 5)]:
                w = mcols(fam, window(y, 5, n))
                if cumm_c in df and len(w) == n:
                    recs.append(recon(df[cumm_c], df[w].apply(pd.to_numeric, errors="coerce").sum(axis=1),
                                      f"{cumm_c} == sum {label} {y}"))
    for yr in ("2022", "2023", "2024"):
        for suf, base in [("LC", ""), ("UN", "UNIT "), ("ST", None)]:
            cols = [f"{c} - MAT ~ 05/{yr} - {suf}" for c in ("SSA", "HSA", "DSA")]
            tsa = f"TSA - MAT ~ 05/{yr} - {suf}"
            if all(c in df for c in cols) and tsa in df:
                recs.append(recon(df[tsa], df[cols].apply(pd.to_numeric, errors="coerce").sum(axis=1),
                                  f"{tsa} == SSA+HSA+DSA"))
            mat = f"{base}MAT MAY'{yr[2:]}" if base is not None else None
            if mat and mat in df and tsa in df:
                recs.append(recon(df[tsa], df[mat], f"{tsa} == {mat}"))
            if mat and mat in df:
                for sa in ("SSA", "HSA", "DSA"):
                    c = f"{sa} - MAT ~ 05/{yr} - {suf}"
                    if c in df:
                        recs.append(recon(df[c], df[mat], f"{c} == {mat}"))

    # ---- price column hypothesis: PR_<MON> ~= value / units or value / qty
    price_checks = []
    for h in [c for c in header if c.startswith("PR_")]:
        mon = h[3:]
        v, u, q = df.get(mon), df.get(f"UNIT {mon}"), df.get(f"QTY {mon}")
        pr = pd.to_numeric(df[h], errors="coerce")
        res = {"column": h, "non_null": int(pr.notna().sum()), "zero": int((pr == 0).sum())}
        for lbl, den in [("value/unit", u), ("value/qty", q)]:
            if v is not None and den is not None:
                vn, dn = pd.to_numeric(v, errors="coerce"), pd.to_numeric(den, errors="coerce")
                mask = (dn > 0) & pr.notna() & (pr > 0)
                ratio = (vn[mask] / dn[mask]) / pr[mask]
                res[lbl] = {"rows_tested": int(mask.sum()),
                            "median_ratio": float(ratio.median()) if mask.any() else None,
                            "within_1pct": int(((ratio - ratio.median()).abs() <= 0.01 * abs(ratio.median())).sum()) if mask.any() else 0}
        price_checks.append(res)

    # ---- unit consistency: QTY / UNIT per PFC should be a constant pack factor if QTY = units x pack size
    uq = []
    for mon in ("MAY'24", "MAY'23", "MAY'22"):
        u, q = pd.to_numeric(df[f"UNIT {mon}"], errors="coerce"), pd.to_numeric(df[f"QTY {mon}"], errors="coerce")
        mask = u > 0
        r = (q[mask] / u[mask])
        uq.append({"month": mon, "rows_with_units": int(mask.sum()),
                   "ratio_is_integer": int((r % 1 == 0).sum()),
                   "ratio_min": float(r.min()) if mask.any() else None,
                   "ratio_median": float(r.median()) if mask.any() else None,
                   "ratio_max": float(r.max()) if mask.any() else None,
                   "qty_positive_when_units_zero": int(((u == 0) & (q > 0)).sum())})

    # ---- per-row activity (missing / zero periods)
    val_month_cols = sorted([h for h, k in monthly.items() if k[0] == "VALUE"], key=lambda h: monthly[h][1:])
    vm = df[val_month_cols].apply(pd.to_numeric, errors="coerce")
    activity = {
        "rows_all_36_value_months_zero_or_null": int(((vm.fillna(0) == 0).all(axis=1)).sum()),
        "rows_with_any_negative_value_month": int((vm < 0).any(axis=1).sum()),
        "rows_with_any_null_value_month": int(vm.isna().any(axis=1).sum()),
        "rows_active_in_latest_month": int((vm[val_month_cols[-1]].fillna(0) != 0).sum()),
        "monthly_value_totals": {c: float(vm[c].sum()) for c in val_month_cols},
        "monthly_active_rows": {c: int((vm[c].fillna(0) != 0).sum()) for c in val_month_cols},
    }

    # ---- launch fields format
    launch = {}
    for c in ("PACK_LNCH", "PROD_LNCH"):
        s = pd.to_numeric(df[c], errors="coerce").dropna().astype(int).astype(str)
        launch[c] = {"length_distribution": {str(k): int(v) for k, v in s.str.len().value_counts().items()},
                     "min": s.min() if len(s) else None, "max": s.max() if len(s) else None}

    # ---- pivot sheet metadata (layout only)
    import zipfile
    with zipfile.ZipFile(SOURCE) as z:
        cache_def = z.read("xl/pivotCache/pivotCacheDefinition1.xml").decode("utf-8", "ignore")
    cache_fields = re.findall(r'<cacheField name="([^"]+)"', cache_def)
    src_ref = re.search(r'<worksheetSource ([^/]+)/>', cache_def)

    profile = {
        "source": {"path": str(SOURCE), "size_bytes": SOURCE.stat().st_size, "sheet_profiled": SHEET},
        "shape": {"rows": int(len(df)), "columns": len(header), "blank_rows_skipped": blank_rows},
        "duplicate_header_names": dup_headers,
        "full_row_duplicates_excess": int(full_dup),
        "period_families": period_summary,
        "candidate_keys": keys,
        "functional_dependencies": fds,
        "reconciliation": recs,
        "price_column_checks": price_checks,
        "unit_qty_consistency": uq,
        "activity": activity,
        "launch_fields": launch,
        "pivot_cache": {"field_count": len(cache_fields), "fields": cache_fields,
                        "worksheet_source": src_ref.group(1) if src_ref else None},
        "columns": columns,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    (PROFILE_DIR / "ims_profile.json").write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")
    rows = [{"position": i, "column": h, **{k: v for k, v in columns[h].items()
                                             if k not in ("values", "python_types")},
             "python_types": json.dumps(columns[h]["python_types"])} for i, h in enumerate(header)]
    pd.DataFrame(rows).to_csv(PROFILE_DIR / "column_profile.csv", index=False)
    print(f"done in {time.time()-t0:.0f}s -> {PROFILE_DIR}")


if __name__ == "__main__":
    main()
