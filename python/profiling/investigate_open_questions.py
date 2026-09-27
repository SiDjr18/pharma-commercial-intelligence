"""Technical investigation of DATA_DICTIONARY.md §8 open questions (read-only).

Writes data/profile/open_questions_evidence.json. Prints aggregates / structure only.
"""
import json
import re
import sys
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from profile_ims import PROFILE_DIR, SOURCE, stream_frame  # noqa: E402


def pivot_grand_total():
    wb = openpyxl.load_workbook(SOURCE, read_only=True, data_only=False)
    ws = wb["PivotTable"]
    labels, found = [], None
    for r in ws.iter_rows():
        vals = [c.value for c in r]
        if any(isinstance(v, str) and v.strip().lower() == "grand total" for v in vals):
            found = [(c.value, getattr(c, "number_format", None)) for c in r if isinstance(c.value, (int, float))]
        if any(isinstance(v, str) and ("CR" in v or "000" in v) for v in vals[:4]):
            labels += [v for v in vals if isinstance(v, str) and ("CR" in v or "000" in v)]
    wb.close()
    return found, labels


def main():
    out = {}
    gt, labels = pivot_grand_total()
    out["pivot_unit_labels"] = labels
    header, df, *_ = stream_frame()
    raw = {y: float(pd.to_numeric(df[f"MAT MAY'{y}"]).sum()) for y in ("22", "23", "24")}
    out["pivot_grand_total_vs_raw_sum"] = [
        {"pivot_value": v, "number_format": f,
         "matches_raw_MAT_sum": {y: abs(v - s) <= 1e-6 * max(1, abs(s)) for y, s in raw.items()}}
        for v, f in (gt or [])]
    # Price plausibility (distribution only)
    u, val = pd.to_numeric(df["UNIT MAT MAY'24"]), pd.to_numeric(df["MAT MAY'24"])
    m = u > 0
    ppp = (1e7 * val[m]) / (1e3 * u[m])  # rupees per pack if value=crore, units='000
    out["implied_price_per_pack_rs_if_crore_and_thousand"] = {
        q: round(float(ppp.quantile(q)), 2) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}
    # INDEX composition
    idx = df["INDEX"].astype(str)
    contain = {}
    for c in header[:23]:
        if c == "INDEX":
            continue
        s = df[c].map(lambda v: "" if v is None or v != v else str(v))
        contain[c] = round(float(sum(a in b for a, b in zip(s, idx)) / len(idx)), 4)
    out["index_contains_column_value_share"] = contain
    cands = [["MOLECULE_DESC"], ["MOLECULE_DESC", "SUBGROUP"], ["MOLECULE_DESC", "NFC"],
             ["MOLECULE_DESC", "SUBGROUP", "NFC"], ["MOLECULE_DESC", "NFC 3"], ["MOLECULE_DESC", "SHORT DESCRIPTION"],
             ["MOLECULE_DESC", "SUBGROUP", "SHORT DESCRIPTION"], ["MOLECULE_DESC", "GROUP", "SHORT DESCRIPTION"],
             ["MOLECULE_DESC", "SUPERGROUP", "SHORT DESCRIPTION"], ["MOLECULE_DESC", "SUBGROUP", "NFC 1"]]
    fd = []
    for c in cands:
        g = df.groupby(c, dropna=False)["INDEX"].nunique()
        r = df.groupby("INDEX")[c].nunique().max(axis=1) if len(c) > 1 else df.groupby("INDEX")[c[0]].nunique()
        fd.append({"columns": c, "distinct_combos": int(len(g)), "combo_to_index_violations": int((g > 1).sum()),
                   "index_to_combo_violations": int((r > 1).sum())})
    out["index_functional_dependencies"] = fd
    # structural template of INDEX: letters->A, digits->9, collapsed
    tmpl = idx.str.replace(r"[A-Za-z]+", "A", regex=True).str.replace(r"\d+", "9", regex=True)
    out["index_separator_templates_top"] = {k: int(v) for k, v in tmpl.value_counts().head(8).items()}
    # GROUP -> SUPERGROUP exceptions
    g = df.groupby("GROUP")["SUPERGROUP"].nunique()
    bad = g[g > 1].index
    sub = df[df["GROUP"].isin(bad)]
    out["group_supergroup"] = {
        "violating_groups": int(len(bad)),
        "subgroup_to_supergroup_violations": int((df.groupby("SUBGROUP")["SUPERGROUP"].nunique() > 1).sum()),
        "per_group": [{"n_supergroups": int(x["SUPERGROUP"].nunique()),
                       "n_subgroups": int(x["SUBGROUP"].nunique()),
                       "rows_per_supergroup": sorted(x["SUPERGROUP"].value_counts().tolist(), reverse=True)}
                      for _, x in sub.groupby("GROUP")],
    }
    # Ind cap
    comps = df["MOLECULE_DESC"].dropna().astype(str).str.count(r"\+") + 1
    ln = df["MOLECULE_DESC"].dropna().astype(str).str.len()
    out["ind_cap"] = {"max_components_in_molecule_desc": int(comps.max()),
                      "molecule_desc_len_by_ind": {int(k): int(v) for k, v in ln.groupby(pd.to_numeric(df.loc[ln.index, "Ind"])).max().items()},
                      "rows_ind10": int((comps == 10).sum())}
    # SSA/HSA/DSA shares (aggregate only)
    shares = {}
    for y in ("2022", "2023", "2024"):
        t = pd.to_numeric(df[f"TSA - MAT ~ 05/{y} - LC"]).sum()
        shares[y] = {k: round(float(pd.to_numeric(df[f"{k} - MAT ~ 05/{y} - LC"]).sum() / t), 4) for k in ("SSA", "HSA", "DSA")}
        shares[y]["rows_where_all_three_nonzero"] = int(((pd.to_numeric(df[f"SSA - MAT ~ 05/{y} - LC"]) > 0) &
                                                       (pd.to_numeric(df[f"HSA - MAT ~ 05/{y} - LC"]) > 0) &
                                                       (pd.to_numeric(df[f"DSA - MAT ~ 05/{y} - LC"]) > 0)).sum())
    out["split_value_shares"] = shares
    (PROFILE_DIR / "open_questions_evidence.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
