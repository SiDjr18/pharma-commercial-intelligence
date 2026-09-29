"""M13 — no-real-data-leakage tests (LOCAL ONLY: need the private processed layer; skipped elsewhere).

Proves that the synthetic dataset shares no entity label, no code, no monthly row vector and no aggregate
shape with the licensed data. Assertion messages report COUNTS only, never real labels or values.
"""
import hashlib

import pytest

from pci_data.schema import DATASETS
from pci_synthetic import generate as G

PRIVATE = DATASETS.get("ims")          # only when PCI_DATASET=ims with a valid external PCI_IMS_DATA_DIR
pytestmark = pytest.mark.skipif(PRIVATE is None or not (PRIVATE / "pack.parquet").exists(),
                                reason="private IMS layer not configured (PCI_DATASET=ims + PCI_IMS_DATA_DIR)")

# entity / code columns that must never coincide with the licensed data
ENTITY_COLUMNS = ["supergroup", "therapy_group", "subgroup", "molecule_desc", "company", "manufacturer_desc", "brand",
                  "pack_desc", "index_desc", "nfc", "nfc1", "nfc2", "nfc3"]
# generic domain vocabularies that MAY coincide (categories, not entities): acute_chronic, indian_mnc,
# plain_combination, molecule_count, form_short_desc
CODE_COLUMNS = ["pfc", "prod_code", "manufacturer_code"]


@pytest.fixture(scope="module")
def tables():
    return G.generate()


@pytest.fixture(scope="module")
def real():
    import duckdb
    c = duckdb.connect()
    yield c, PRIVATE.as_posix()
    c.close()


def _norm(s):
    return " ".join(str(s).casefold().split())


@pytest.mark.parametrize("col", ENTITY_COLUMNS)
def test_no_shared_entity_labels(tables, real, col):
    c, p = real
    real_vals = {_norm(r[0]) for r in c.execute(f"SELECT DISTINCT {col} FROM read_parquet('{p}/pack.parquet') "
                                                f"WHERE {col} IS NOT NULL").fetchall()}
    syn_vals = {_norm(v) for v in tables["pack"].column(col).to_pylist() if v is not None}
    n = len(real_vals & syn_vals)
    assert n == 0, f"{col}: {n} synthetic labels equal a real label"


@pytest.mark.parametrize("col", CODE_COLUMNS)
def test_no_shared_codes(tables, real, col):
    c, p = real
    real_codes = {r[0] for r in c.execute(f"SELECT DISTINCT {col} FROM read_parquet('{p}/pack.parquet')").fetchall()}
    n = len(real_codes & set(tables["pack"].column(col).to_pylist()))
    assert n == 0, f"{col}: {n} shared codes"


def _vectors(rows):
    """Hashes of each pack's 36-month value / units / qty vectors (rounded 1e-9), non-zero vectors only.
    1e-9 (not 1e-6): at 1e-6 a single-month synthetic value of ~1e-3 crore coincided by chance with one real
    single-month value (first M13 run); copy detection needs full precision, as in the triple test below."""
    by = {}
    for pfc, period, v, u, q in rows:
        by.setdefault(pfc, []).append((period, v, u, q))
    out = set()
    for vals in by.values():
        vals.sort()
        for i in (1, 2, 3):
            vec = tuple(round(x[i], 9) for x in vals)
            if any(vec):
                out.add(hashlib.sha1(repr((i, vec)).encode()).hexdigest())
    return out


def test_no_real_monthly_row_vector_reused(tables, real):
    c, p = real
    real_rows = c.execute(f"SELECT pfc, period, value_cr, units_k, qty_k FROM read_parquet('{p}/fact_pack_month.parquet')").fetchall()
    f = tables["fact_pack_month"]
    syn_rows = zip(*(f.column(k).to_pylist() for k in ("pfc", "period", "value_cr", "units_k", "qty_k")))
    n = len(_vectors(real_rows) & _vectors(syn_rows))
    assert n == 0, f"{n} synthetic monthly vectors equal a real pack's vector"


def test_no_real_value_reused_at_pack_month_level(tables, real):
    """No non-zero synthetic pack-month (value, units, qty) triple equals a real one (rounded 1e-9)."""
    c, p = real
    real_trip = {(round(v, 9), round(u, 9), round(q, 9)) for v, u, q in c.execute(
        f"SELECT value_cr, units_k, qty_k FROM read_parquet('{p}/fact_pack_month.parquet') WHERE value_cr <> 0").fetchall()}
    f = tables["fact_pack_month"]
    syn_trip = {(round(v, 9), round(u, 9), round(q, 9)) for v, u, q in zip(
        *(f.column(k).to_pylist() for k in ("value_cr", "units_k", "qty_k"))) if v != 0}
    n = len(real_trip & syn_trip)
    assert n == 0, f"{n} shared pack-month value triples"


def test_aggregates_not_real_or_scaled_real(tables, real):
    """National monthly totals: never near-equal to the real ones, and not a constant multiple (no scaled copy)."""
    c, p = real
    real_tot = dict(c.execute(f"SELECT period, sum(value_cr) FROM read_parquet('{p}/fact_pack_month.parquet') "
                              f"GROUP BY 1").fetchall())
    f = tables["fact_pack_month"]
    syn_tot = {}
    for d, v in zip(f.column("period").to_pylist(), f.column("value_cr").to_pylist()):
        syn_tot[d] = syn_tot.get(d, 0.0) + v
    assert set(real_tot) == set(syn_tot)
    near = sum(1 for d in real_tot if abs(syn_tot[d] - real_tot[d]) <= 0.01 * real_tot[d])
    assert near == 0, f"{near} months with synthetic total within 1% of the real total"
    ratios = [syn_tot[d] / real_tot[d] for d in sorted(real_tot)]
    mean = sum(ratios) / len(ratios)
    cv = (sum((r - mean) ** 2 for r in ratios) / len(ratios)) ** 0.5 / mean
    assert cv > 0.02, "synthetic monthly totals look like a scaled copy of the real series"
