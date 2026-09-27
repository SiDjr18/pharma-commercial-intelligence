"""Supplementary hypothesis checks on the IMS DATA sheet (read-only, streamed).

Outputs data/profile/ims_profile_supplement.json. Prints aggregates only.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from profile_ims import PROFILE_DIR, SOURCE, recon, stream_frame  # noqa: E402

import re
import zipfile


def num(s):
    return pd.to_numeric(s, errors="coerce")


def main():
    header, df, *_ = stream_frame()
    out = {}
    # ST suffix hypothesis: TSA ST == QTY MAT ?
    out["st_checks"] = [recon(df[f"TSA - MAT ~ 05/20{y} - ST"], df[f"QTY MAT MAY'{y}"], f"TSA ST 20{y} == QTY MAT MAY'{y}")
                        for y in ("22", "23", "24")]
    # NI (new introduction) hypothesis
    ni = []
    for y in ("22", "23", "24"):
        c = f"NI 24 MONTHS MAT MAY'{y}"
        nn = df[c].notna()
        lnch = num(df["PACK_LNCH"])
        cutoff_lo = (2000 + int(y) - 2) * 100 + 6  # launched Jun (Y-2) .. May Y
        cutoff_hi = (2000 + int(y)) * 100 + 5
        in_window = (lnch >= cutoff_lo) & (lnch <= cutoff_hi)
        plnch = num(df["PROD_LNCH"])
        prod_in_window = (plnch >= cutoff_lo) & (plnch <= cutoff_hi)
        r = recon(df.loc[nn, c], df.loc[nn, f"MAT MAY'{y}"], f"{c} == MAT MAY'{y} (non-null rows)")
        ni.append({"column": c, "non_null": int(nn.sum()),
                   "non_null_and_pack_launch_in_24m": int((nn & in_window).sum()),
                   "non_null_and_prod_launch_in_24m": int((nn & prod_in_window).sum()),
                   "pack_launch_in_24m_total": int(in_window.sum()),
                   "prod_launch_in_24m_total": int(prod_in_window.sum()),
                   "non_null_with_mat_gt0": int((nn & (num(df[f"MAT MAY'{y}"]) > 0)).sum()),
                   "equals_mat": r})
    out["ni_checks"] = ni
    # Ind distribution vs Plain/Combination & molecule component count
    comps = df["MOLECULE_DESC"].dropna().astype(str).str.count(r"\+") + 1
    out["ind_vs_molecule_plus_count"] = {
        "rows": int(comps.shape[0]),
        "ind_equals_plus_count_plus_1": int((num(df.loc[comps.index, "Ind"]) == comps).sum()),
        "ind_distribution": {str(k): int(v) for k, v in df["Ind"].value_counts(dropna=False).sort_index().items()},
    }
    # hierarchy violations detail (codes only)
    g = df.groupby("GROUP")["SUPERGROUP"].nunique()
    viol = g[g > 1].index
    out["group_supergroup_violation_rows"] = int(df["GROUP"].isin(viol).sum())
    b = df.groupby("BRANDS")["COMPANY"].nunique()
    out["brand_name_shared_across_companies"] = {"brand_names": int((b > 1).sum()),
                                                 "rows": int(df["BRANDS"].isin(b[b > 1].index).sum())}
    # brand name vs PROD_CODE cardinality
    pc = df.groupby("BRANDS")["PROD_CODE"].nunique()
    out["brand_names_mapping_to_multiple_prod_codes"] = int((pc > 1).sum())
    # launch date validity
    for c in ("PACK_LNCH", "PROD_LNCH"):
        s = num(df[c]).astype(int)
        nz = s[s != 0]
        mm = nz % 100
        out[f"{c}_validity"] = {"zero": int((s == 0).sum()), "invalid_month": int(((mm < 1) | (mm > 12)).sum()),
                                "min_nonzero": int(nz.min()), "max": int(nz.max()),
                                "pack_before_product": int(((num(df["PACK_LNCH"]) < num(df["PROD_LNCH"])) &
                                                             (num(df["PACK_LNCH"]) > 0)).sum()) if c == "PACK_LNCH" else None}
    # intra-series gaps: zero months between first and last active month
    mcols = [h for h in header if re.match(r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)'\d\d$", h)]
    v = df[mcols].apply(num).fillna(0).to_numpy()
    active = v != 0
    first = np.where(active.any(1), active.argmax(1), -1)
    last = np.where(active.any(1), v.shape[1] - 1 - active[:, ::-1].argmax(1), -1)
    gaps = [int((~active[i, first[i]:last[i] + 1]).sum()) if first[i] >= 0 else 0 for i in range(len(v))]
    gaps = np.array(gaps)
    out["intra_series_zero_gaps"] = {"rows_with_gaps": int((gaps > 0).sum()), "max_gap_months": int(gaps.max()),
                                     "median_gap_when_present": float(np.median(gaps[gaps > 0])) if (gaps > 0).any() else 0}
    # value/unit scale evidence (not raw rows): 1e4 * value/unit == PR ?
    u, val, pr = num(df["UNIT MAY'24"]), num(df["MAY'24"]), num(df["PR_MAY'24"])
    m = u > 0
    out["price_scale"] = {"pr_equals_1e4_x_value_over_unit_within_0.1pct":
                          int(((1e4 * val[m] / u[m] - pr[m]).abs() <= 0.001 * pr[m].abs() + 1e-9).sum()),
                          "rows_tested": int(m.sum()),
                          "pr_positive_when_units_zero": int(((u == 0) & (pr > 0)).sum())}
    # QTY/UNIT factor stability per PFC across months
    ratios = []
    for mon in ("MAY'24", "MAY'23", "MAY'22", "NOV'23"):
        uu, qq = num(df[f"UNIT {mon}"]), num(df[f"QTY {mon}"])
        ratios.append(np.where(uu > 0, qq / uu.replace(0, np.nan), np.nan))
    R = np.vstack(ratios).T
    ok = (~np.isnan(R)).sum(1) >= 2
    spread = np.nanmax(R[ok], 1) - np.nanmin(R[ok], 1)
    out["qty_unit_factor_stability"] = {"pfcs_with_2plus_months": int(ok.sum()),
                                        "stable_within_1e-6": int((spread <= 1e-6).sum())}
    # pivot definition: fields not in source header (calculated fields) and data fields
    with zipfile.ZipFile(SOURCE) as z:
        cd = z.read("xl/pivotCache/pivotCacheDefinition1.xml").decode("utf-8", "ignore")
        pt = z.read("xl/pivotTables/pivotTable1.xml").decode("utf-8", "ignore")
    fields = re.findall(r'<cacheField name="([^"]+)"[^>]*?(formula="[^"]*")?', cd)
    names = [f[0].replace("&apos;", "'").replace("&amp;", "&") for f in fields]
    out["pivot"] = {
        "cache_fields_not_in_source_header": [n for n in names if n not in header],
        "calculated_fields": [(a.replace("&apos;", "'"), b.replace("&apos;", "'")) for a, b in
                              re.findall(r'<cacheField name="([^"]+)"[^>]*formula="([^"]*)"', cd)],
        "data_fields": [d.replace("&apos;", "'") for d in re.findall(r'<dataField name="([^"]+)"', pt)],
        "row_field_count": len(re.findall(r"<rowFields[^>]*>(.*?)</rowFields>", pt, re.S)),
        "page_fields": len(re.findall(r"<pageField ", pt)),
        "refreshed_version": (re.search(r'refreshedVersion="(\d+)"', cd) or [None, None])[1],
        "refreshed_date": (re.search(r'refreshedDate="([^"]+)"', cd) or [None, None])[1],
    }
    (PROFILE_DIR / "ims_profile_supplement.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
