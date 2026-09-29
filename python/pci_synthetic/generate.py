"""Generate the synthetic processed layer (M13, SYN-1.0.0).

    cd python && ..\\.venv\\Scripts\\python.exe -m pci_synthetic.generate            # -> data/synthetic/
    ..\\.venv\\Scripts\\python.exe -m pci_synthetic.generate --out <OUTPUT_DIR>      # any folder

Contract (docs/SYNTHETIC_DATA.md):
  * inputs  : spec.py, names.py, processed_schema.json only — no data file is read (static test);
  * output  : pack / fact_pack_month / pack_snapshot / pack_price_month Parquet with the exact processed-layer
              column names, types and grain, plus _manifest.json (same key structure as the M3 manifest);
  * values  : invented (structure-only); entities and codes fictional;
  * determinism: only random.Random(SEED).random() is used (its output is guaranteed across Python versions);
              the fingerprint hashes canonical table content (not Parquet bytes).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import random
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import names as N
from . import spec as S

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "synthetic"
FINGERPRINT_FILE = "SYNTHETIC_FINGERPRINT.json"
SCHEMA = json.loads((Path(__file__).with_name("processed_schema.json")).read_text(encoding="utf-8"))["tables"]
_TYPES = {"int16": pa.int16(), "int32": pa.int32(), "int64": pa.int64(), "double": pa.float64(),
          "string": pa.string(), "date32[day]": pa.date32()}


# ------------------------------------------------------------------------------------------------ rng
class Rng:
    """Deterministic helpers built ONLY on random.Random.random()."""

    def __init__(self, seed: int):
        self._r = random.Random(seed)

    def random(self) -> float:
        return self._r.random()

    def u(self, a: float, b: float) -> float:
        return a + (b - a) * self._r.random()

    def idx(self, n: int) -> int:
        return min(int(self._r.random() * n), n - 1)

    def pick(self, seq):
        return seq[self.idx(len(seq))]

    def bern(self, p: float) -> bool:
        return self._r.random() < p

    def normal(self, mu: float = 0.0, sd: float = 1.0) -> float:
        u1 = max(self._r.random(), 1e-12)
        u2 = self._r.random()
        return mu + sd * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def lognormal(self, median: float, sigma: float) -> float:
        return median * math.exp(self.normal(0.0, sigma))

    def pareto(self, alpha: float) -> float:
        return (1.0 - self._r.random()) ** (-1.0 / alpha)

    def weighted(self, weights: list[float]) -> int:
        t = self._r.random() * sum(weights)
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if t < acc:
                return i
        return len(weights) - 1

    def shuffle(self, seq: list) -> list:
        for i in range(len(seq) - 1, 0, -1):
            j = self.idx(i + 1)
            seq[i], seq[j] = seq[j], seq[i]
        return seq


def _month_index(d: dt.date) -> int:
    return (d.year - S.FIRST_PERIOD.year) * 12 + d.month - S.FIRST_PERIOD.month


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.year * 12 + d.month - 1 + n
    return dt.date(m // 12, m % 12 + 1, 1)


def _yyyymm(d: dt.date | None) -> int:
    return 0 if d is None else d.year * 100 + d.month


def _unique(rng: Rng, make, n: int, taken: set | None = None) -> list[str]:
    taken = set() if taken is None else taken
    out = []
    while len(out) < n:
        w = make()
        if w not in taken:
            taken.add(w)
            out.append(w)
    return out


# ------------------------------------------------------------------------------------------------ entities
def build_entities(rng: Rng) -> dict:
    areas = [{"name": n, "acute": a, "season_amp": rng.u(*S.SEASONAL_AMPLITUDE), "season_phase": rng.idx(12)}
             for n, a in N.THERAPY_AREAS[:S.N_THERAPY_AREAS]]
    # therapy groups: every area gets >= 3; exactly GROUPS_SPANNING_TWO_AREAS groups get an alternate area
    groups = []
    for g in range(S.N_THERAPY_GROUPS):
        ai = g % S.N_THERAPY_AREAS if g < 3 * S.N_THERAPY_AREAS else rng.idx(S.N_THERAPY_AREAS)
        groups.append({"area": ai, "alt": None,
                       "name": f"TG{g + 1:02d} {areas[ai]['name'].split(' ')[0]} {rng.pick(N.CLASS_WORDS)}"})
    for g in range(S.GROUPS_SPANNING_TWO_AREAS):
        gi = 5 + g * 7
        groups[gi]["alt"] = (groups[gi]["area"] + 1 + g) % S.N_THERAPY_AREAS
    # molecules (fictional)
    taken: set = set()
    molecules = _unique(rng, lambda: (N.word(rng, 1 + rng.idx(2)) + rng.pick(N.MOLECULE_SUFFIX)).upper(),
                        S.N_MOLECULES, taken)
    # subgroups: each belongs to one group; area from the group (alternate area for part of spanning groups)
    subgroups = []
    for s in range(S.N_SUBGROUPS):
        gi = s % S.N_THERAPY_GROUPS if s < S.N_THERAPY_GROUPS else rng.idx(S.N_THERAPY_GROUPS)
        g = groups[gi]
        ai = g["area"]
        if g["alt"] is not None and (s % 2 == 1):
            ai = g["alt"]
        acute = areas[ai]["acute"] if rng.bern(0.85) else not areas[ai]["acute"]
        # molecule descriptors of this market: plain molecules and '+' combinations
        descs = []
        for _ in range(1 + rng.idx(4)):
            k = 1 if rng.bern(0.55) else 2 + rng.idx(3)
            mols = sorted({molecules[rng.idx(len(molecules))] for _ in range(k)})
            descs.append(" + ".join(mols))
        head = descs[0].split(" + ")
        label = "+".join(m[:7] + "." for m in head[:3])
        subgroups.append({"group": gi, "area": ai, "acute": acute, "descs": sorted(set(descs)),
                          "name": f"S{ai + 1:02d}{chr(65 + gi % 26)}{s + 1:03d} {label}",
                          "size": rng.lognormal(1.0, S.SUBGROUP_SIZE_SIGMA),
                          "growth": rng.normal(*S.MARKET_GROWTH)})
    # companies and manufacturers (fictional)
    cwords = _unique(rng, lambda: N.word(rng, 2 + rng.idx(2)).upper(), S.N_COMPANIES, set())
    companies = [{"name": f"{w} {rng.pick(N.COMPANY_SUFFIX)}", "indian": rng.bern(S.INDIAN_COMPANY_RATE)}
                 for w in cwords]
    manufacturers = []
    for i in range(S.N_MANUFACTURERS):
        ci = i if i < S.N_COMPANIES else rng.idx(S.N_COMPANIES)
        c = companies[ci]
        desc = c["name"] if i < S.N_COMPANIES else f"{c['name'].split(' ')[0]} {rng.pick(N.DIVISION_WORDS)} DIV-{i:03d}"
        manufacturers.append({"code": S.FIRST_CODE + 1000 + i, "company": ci, "desc": desc})
    # dosage forms: fictional NFC-like codes (functional: nfc -> nfc1/nfc2/nfc3, short description)
    forms = []
    for f in range(S.N_FORMS):
        short, grp, abbr = N.FORMS[f % len(N.FORMS)]
        nfc = f"SF{chr(65 + f // 10)}{f % 10}{chr(75 + f % 7)}"
        forms.append({"nfc": nfc, "nfc1": grp, "nfc2": nfc[:3], "nfc3": nfc[:4], "short": short, "abbr": abbr})
    return {"areas": areas, "groups": groups, "subgroups": subgroups, "molecules": molecules,
            "companies": companies, "manufacturers": manufacturers, "forms": forms}


def _brand(rng: Rng) -> str:
    """Invented brand: 2-3 syllables + suffix, at least 8 letters (short invented words can coincide with real
    brands; found by the M13 leakage test)."""
    while True:
        b = (N.word(rng, 2 + rng.idx(2)) + rng.pick(N.BRAND_SUFFIX).lower()).upper()
        if len(b) >= 8:
            return b


def build_products(rng: Rng, E: dict) -> list[dict]:
    periods = S.periods()
    sub_w = [s["size"] for s in E["subgroups"]]
    man_w = [rng.pareto(1.5) for _ in E["manufacturers"]]
    brand_taken: set = set()
    products = []
    for i in range(S.N_PRODUCTS):
        mi = rng.weighted(man_w)
        ci = E["manufacturers"][mi]["company"]
        si = rng.weighted(sub_w)
        if products and rng.bern(S.SHARED_BRAND_NAME_RATE):          # same name, another company
            other = [p for p in products[-200:] if p["company"] != ci]
            brand = rng.pick(other)["brand"] if other else None
        elif products and rng.bern(S.BRAND_MULTI_PRODUCT_RATE):      # same name, several products of a company
            same = [p for p in products if p["company"] == ci]
            brand = rng.pick(same)["brand"] if same else None
        else:
            brand = None
        if brand is None:
            brand = _unique(rng, lambda: _brand(rng),
                            1, brand_taken)[0]
        if rng.bern(S.PROD_LAUNCH_UNKNOWN_RATE):
            launch = None
        elif rng.bern(S.LAUNCHED_IN_WINDOW_RATE):
            launch = periods[1 + rng.idx(S.N_PERIODS - 4)]
        else:
            launch = dt.date(1995 + rng.idx(26), 1 + rng.idx(12), 1)
            if launch >= S.FIRST_PERIOD:
                launch = dt.date(2021, 1 + rng.idx(5), 1)
        second = None
        if rng.bern(S.PRODUCT_MULTI_SUBGROUP_RATE):
            area = E["subgroups"][si]["area"]
            same_area = [j for j, s in enumerate(E["subgroups"]) if s["area"] == area and j != si]
            second = rng.pick(same_area) if same_area else None
        products.append({"code": S.FIRST_CODE + 100_000 + i, "brand": brand, "manufacturer": mi, "company": ci,
                         "subgroup": si, "second_subgroup": second, "launch": launch,
                         "mol": rng.pick(E["subgroups"][si]["descs"]), "weight": rng.pareto(S.PRODUCT_WEIGHT_ALPHA),
                         "growth": E["subgroups"][si]["growth"] + rng.normal(0.0, S.PRODUCT_GROWTH_SPREAD)})
    return products


def build_packs(rng: Rng, E: dict, products: list[dict]) -> list[dict]:
    periods = S.periods()
    owner = list(range(len(products)))                               # every product has >= 1 pack
    owner += [i for i, p in enumerate(products) if p["second_subgroup"] is not None]   # 2nd-subgroup pack
    w = [p["weight"] for p in products]
    while len(owner) < S.N_PACKS:
        owner.append(rng.weighted(w))
    rng.shuffle(owner)
    second_done: set = set()
    packs = []
    for k, pi in enumerate(owner):
        p = products[pi]
        si = p["subgroup"]
        if p["second_subgroup"] is not None and pi not in second_done and k > 0 and any(o == pi for o in owner[:k]):
            si = p["second_subgroup"]
            second_done.add(pi)
        sub = E["subgroups"][si]
        mol = p["mol"] if si == p["subgroup"] and not rng.bern(0.03) else rng.pick(sub["descs"])
        form = rng.pick(E["forms"])
        size = rng.pick(S.PACK_SIZES)
        man = E["manufacturers"][p["manufacturer"]]
        comp = E["companies"][p["company"]]
        if p["launch"] is None or rng.bern(S.PACK_LAUNCH_UNKNOWN_RATE):
            pack_launch = None
        else:
            pack_launch = _add_months(p["launch"], rng.idx(24) if rng.bern(0.35) else 0)
            if pack_launch > periods[-1]:
                pack_launch = periods[-1]
        packs.append({
            "source_row": k + 2, "pfc": S.FIRST_CODE + 500_000 + k, "prod": pi, "subgroup": si,
            "molecule_desc": mol, "pack_desc": f"{p['brand']} {form['abbr']} {rng.pick(N.STRENGTHS)} {size}",
            "prod_code": p["code"], "brand": p["brand"], "manufacturer_code": man["code"],
            "manufacturer_desc": man["desc"], "company": comp["name"], "indian_mnc": "INDIAN" if comp["indian"] else "MNC",
            "sub": sub, "form": form, "size": size, "pack_launch": pack_launch, "prod_launch": p["launch"],
        })
    for k in range(len(packs)):                                      # non-unique pack descriptions
        if rng.bern(S.PACK_DESC_REUSE_RATE):
            packs[k]["pack_desc"] = packs[rng.idx(len(packs))]["pack_desc"]
    return packs


# ------------------------------------------------------------------------------------------------ tables
def pack_table(E: dict, packs: list[dict]) -> dict:
    cols = {c: [] for c, _ in SCHEMA["pack"]}
    null_idx = len(packs) // 3                                        # the one pack without molecule data
    for k, pk in enumerate(packs):
        sub, form = pk["sub"], pk["form"]
        mol = None if k == null_idx else pk["molecule_desc"]
        n_mol = None if mol is None else mol.count(" + ") + 1
        area = E["areas"][sub["area"]]
        row = {
            "source_row": pk["source_row"], "pfc": pk["pfc"],
            "plain_combination": None if mol is None else ("Plain" if n_mol == 1 else "Combination"),
            "molecule_count": n_mol, "molecule_desc": mol, "pack_desc": pk["pack_desc"],
            "prod_code": pk["prod_code"], "brand": pk["brand"], "manufacturer_code": pk["manufacturer_code"],
            "manufacturer_desc": pk["manufacturer_desc"], "company": pk["company"], "indian_mnc": pk["indian_mnc"],
            "subgroup": sub["name"], "therapy_group": E["groups"][sub["group"]]["name"], "supergroup": area["name"],
            "acute_chronic": "ACUTE" if sub["acute"] else "CHRONIC",
            "pack_launch_yyyymm": _yyyymm(pk["pack_launch"]), "prod_launch_yyyymm": _yyyymm(pk["prod_launch"]),
            "index_desc": f"{pk['brand']} : {sub['name']} : {pk['manufacturer_desc']} : {pk['prod_code']}",
            "nfc": form["nfc"], "nfc1": form["nfc1"], "nfc2": form["nfc2"], "nfc3": form["nfc3"],
            "form_short_desc": form["short"], "pack_launch_month": pk["pack_launch"], "prod_launch_month": pk["prod_launch"],
        }
        for c in cols:
            cols[c].append(row[c])
    return cols


def monthly_values(rng: Rng, E: dict, products: list[dict], packs: list[dict]):
    """pfc -> list of (value_cr, units_k, qty_k) for the 36 months, and pfc -> list of list prices (Rs)."""
    periods = S.periods()
    fact, prices = {}, {}
    for pk in packs:
        p, sub = products[pk["prod"]], pk["sub"]
        area = E["areas"][sub["area"]]
        base = rng.lognormal(*S.PACK_BASE_VALUE_CR) * math.sqrt(sub["size"] * p["weight"])
        p0 = rng.lognormal(*S.PACK_PRICE_RS)
        inc = rng.u(*S.ANNUAL_PRICE_INCREASE)
        dormant = rng.bern(S.DORMANT_PACK_RATE)
        start = 0
        launch = pk["pack_launch"] or p["launch"]
        if launch is not None and launch > periods[0]:
            start = _month_index(launch)
        end = S.N_PERIODS
        if rng.bern(S.DISCONTINUED_PACK_RATE):
            end = max(start + 3, 6 + rng.idx(S.N_PERIODS - 8))
        rows, plist = [], []
        for t, d in enumerate(periods):
            aprils = sum(1 for m in periods[:t + 1] if m.month == 4)
            price = p0 * (1.0 + inc) ** aprils
            plist.append(price)
            active = (not dormant) and start <= t < end and not rng.bern(S.ZERO_MONTH_RATE)
            if not active:
                rows.append((0.0, 0.0, 0.0))
                continue
            ramp = min(1.0, (t - start + 1) / 6.0) if start > 0 else 1.0
            season = 1.0 + area["season_amp"] * math.sin(2.0 * math.pi * (d.month + area["season_phase"]) / 12.0)
            value = base * (1.0 + p["growth"]) ** (t / 12.0) * season * ramp * math.exp(rng.normal(0.0, S.MONTHLY_NOISE))
            value = max(value, 0.0)
            units = value * 1e4 / price                                  # '000 packs = value_cr x 1e7 / price / 1e3
            rows.append((value, units, units * pk["size"]))
        fact[pk["pfc"]] = rows
        prices[pk["pfc"]] = plist
    return fact, prices


def fact_table(packs, fact) -> dict:
    periods = S.periods()
    cols = {c: [] for c, _ in SCHEMA["fact_pack_month"]}
    for pk in packs:
        for d, (v, u, q) in zip(periods, fact[pk["pfc"]]):
            cols["pfc"].append(pk["pfc"]); cols["period"].append(d)
            cols["value_cr"].append(v); cols["units_k"].append(u); cols["qty_k"].append(q)
    return cols


def snapshot_table(rng: Rng, packs, fact) -> dict:
    periods = S.periods()
    cols = {c: [] for c, _ in SCHEMA["pack_snapshot"]}
    for y in S.SNAPSHOT_YEARS:
        m = periods.index(dt.date(y, 5, 1))
        ytd = [periods.index(dt.date(y, mm, 1)) for mm in range(1, 6)]
        mat = list(range(m - 11, m + 1))
        for pk in packs:
            r = fact[pk["pfc"]]
            agg = {}
            for i, meas in enumerate(("value", "units", "qty")):
                agg[f"{meas}_month"] = r[m][i]
                agg[f"{meas}_ytd"] = math.fsum(r[t][i] for t in ytd)
                agg[f"{meas}_mat"] = math.fsum(r[t][i] for t in mat)
            launch = pk["pack_launch"]
            ni = agg["value_mat"] if launch is not None and launch > dt.date(y - 2, 5, 1) else None
            eq = rng.bern(S.TSA_EQUALS_MAT_RATE)
            f = 1.0 if eq else rng.u(0.85, 1.15)
            s1, s2 = rng.u(0.2, 0.5), rng.u(0.1, 0.3)
            row = {"pfc": pk["pfc"], "snapshot_year": y, "snapshot_month": dt.date(y, 5, 1), "ni_24m_mat_value_cr": ni}
            for meas, suffix in (("value", "value_cr"), ("units", "units_k"), ("qty", "qty_k")):
                tsa = agg[f"{meas}_mat"] * f
                ssa, hsa = tsa * s1, tsa * s2
                row.update({f"tsa_mat_{suffix}": tsa, f"ssa_mat_{suffix}": ssa, f"hsa_mat_{suffix}": hsa,
                            f"dsa_mat_{suffix}": tsa - ssa - hsa})
            row.update(agg)
            for c in cols:
                cols[c].append(row[c])
    return cols


def price_table(packs, fact, prices) -> dict:
    periods = S.periods()
    cols = {c: [] for c, _ in SCHEMA["pack_price_month"]}
    first = S.N_PERIODS - S.PRICE_PERIODS
    for pk in packs:
        last = prices[pk["pfc"]][first]                                  # list price before the window
        for t in range(first, S.N_PERIODS):
            v, u, _ = fact[pk["pfc"]][t]
            price = 1e4 * v / u if u > 0 else last                       # carried forward when units = 0
            last = price
            cols["pfc"].append(pk["pfc"]); cols["period"].append(periods[t]); cols["price_rs"].append(price)
    return cols


# ------------------------------------------------------------------------------------------------ output
def to_arrow(name: str, cols: dict) -> pa.Table:
    schema = pa.schema([(c, _TYPES[t]) for c, t in SCHEMA[name]])
    return pa.table({c: pa.array(cols[c], type=schema.field(c).type) for c, _ in SCHEMA[name]}, schema=schema)


def canonical_hash(tables: dict[str, pa.Table]) -> str:
    """SHA-256 over canonical content (rows sorted by key, JSON-serialised; float repr is exact)."""
    keys = {"pack": ["pfc"], "fact_pack_month": ["pfc", "period"], "pack_snapshot": ["pfc", "snapshot_year"],
            "pack_price_month": ["pfc", "period"]}
    h = hashlib.sha256()
    h.update(json.dumps(S.as_dict(), sort_keys=True).encode())
    for name in ("pack", "fact_pack_month", "pack_snapshot", "pack_price_month"):
        t = tables[name].sort_by([(k, "ascending") for k in keys[name]])
        h.update(name.encode())
        for c in t.column_names:
            h.update(json.dumps(t.column(c).to_pylist(), default=str).encode())
    return h.hexdigest()


def generate(seed: int = S.SEED) -> dict[str, pa.Table]:
    rng = Rng(seed)
    E = build_entities(rng)
    products = build_products(rng, E)
    packs = build_packs(rng, E, products)
    fact, prices = monthly_values(rng, E, products, packs)
    return {"pack": to_arrow("pack", pack_table(E, packs)),
            "fact_pack_month": to_arrow("fact_pack_month", fact_table(packs, fact)),
            "pack_snapshot": to_arrow("pack_snapshot", snapshot_table(rng, packs, fact)),
            "pack_price_month": to_arrow("pack_price_month", price_table(packs, fact, prices))}


def write(out_dir: Path = OUT, seed: int = S.SEED) -> dict:
    t0 = time.time()
    tables = generate(seed)
    fp = canonical_hash(tables)
    out_dir = Path(out_dir)
    # never overwrite another processed layer (e.g. a --out pointing at the private IMS folder). Existence checks
    # only: a synthetic folder always carries the fingerprint file next to its manifest; nothing is read.
    if (out_dir / "_manifest.json").exists() and not (out_dir / FINGERPRINT_FILE).exists():
        raise SystemExit("refusing to write synthetic data into a folder that holds another processed layer")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, t in tables.items():
        pq.write_table(t, out_dir / f"{name}.parquet", compression="zstd")
    periods = S.periods()
    rows = {k: t.num_rows for k, t in tables.items()}
    fingerprint = {"dataset": "synthetic", "generator": "pci_synthetic", "version": S.VERSION, "seed": seed,
                   "fingerprint_sha256": fp, "rows": rows,
                   "note": "Structure-only fictional data; no value derived from the licensed source."}
    manifest = {
        "dataset": "synthetic",
        "source": {"path": f"synthetic://pci_synthetic {S.VERSION} seed={seed}", "sheet": "DATA", "sha256": fp,
                   "data_rows": rows["pack"], "note": fingerprint["note"]},
        "build": {"built_at": dt.datetime.now().isoformat(timespec="seconds"), "seconds": round(time.time() - t0, 1),
                  "generator": "pci_synthetic.generate", "version": S.VERSION, "pyarrow": pa.__version__},
        "periods": [d.isoformat() for d in periods],
        "snapshot_years": list(S.SNAPSHOT_YEARS),
        "price_periods": [d.isoformat() for d in periods[-S.PRICE_PERIODS:]],
        "outputs": {k: {"rows": v} for k, v in rows.items()},
        "spec": S.as_dict(),
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / FINGERPRINT_FILE).write_text(json.dumps(fingerprint, indent=2) + "\n", encoding="utf-8")
    return {**fingerprint, "seconds": round(time.time() - t0, 1), "out": str(out_dir)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Generate the synthetic processed layer (structure-only, fictional).")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--seed", type=int, default=S.SEED)
    a = ap.parse_args()
    print(json.dumps(write(a.out, a.seed), indent=2))
