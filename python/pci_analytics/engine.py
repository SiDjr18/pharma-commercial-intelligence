"""Independent Python data access + entity assignment (no SQL, no DuckDB).

Reads the M3 Parquet files with pyarrow. The 3.8M-row monthly fact is held once in memory as
an Arrow table (5 columns, ~140 MB); window sums per pack are computed with pyarrow.compute
and cached per window. Entity membership is re-implemented here from pack attributes, following
docs/MARKET_DEFINITION.md and docs/SEGMENT_DEFINITIONS.md, independently of sql/entities.sql.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

from pci_data.schema import PROCESSED_DIR

UNCLASSIFIED = "(UNCLASSIFIED)"


def _nz(v):
    return UNCLASSIFIED if v is None else v


# entity_type -> (key function, label function) over one pack record
ENTITY_TYPES = {
    "total":             (lambda p: "TOTAL",                     lambda p: "Total market"),
    "supergroup":        (lambda p: p["supergroup"],             lambda p: p["supergroup"]),
    "therapy_group":     (lambda p: p["therapy_group"],          lambda p: p["therapy_group"]),
    "subgroup":          (lambda p: p["subgroup"],               lambda p: p["subgroup"]),
    "molecule":          (lambda p: _nz(p["molecule_desc"]),     lambda p: _nz(p["molecule_desc"])),
    "company":           (lambda p: p["company"],                lambda p: p["company"]),
    "manufacturer":      (lambda p: str(p["manufacturer_code"]), lambda p: p["manufacturer_desc"]),
    "product":           (lambda p: str(p["prod_code"]),         lambda p: f"{p['brand']} ({p['company']})"),
    "product_subgroup":  (lambda p: p["index_desc"],
                          lambda p: f"{p['brand']} ({p['company']}) in {p['subgroup']}"),
    "acute_chronic":     (lambda p: p["acute_chronic"],          lambda p: p["acute_chronic"]),
    "indian_mnc":        (lambda p: p["indian_mnc"],             lambda p: p["indian_mnc"]),
    "plain_combination": (lambda p: _nz(p["plain_combination"]), lambda p: _nz(p["plain_combination"])),
    "molecule_count":    (lambda p: _nz(None if p["molecule_count"] is None else str(p["molecule_count"])),
                          lambda p: _nz(None if p["molecule_count"] is None else str(p["molecule_count"]))),
    "dosage_form":       (lambda p: p["form_short_desc"],        lambda p: p["form_short_desc"]),
    "nfc1":              (lambda p: p["nfc1"],                   lambda p: p["nfc1"]),
}


class DataEngine:
    """Holds the Parquet data once; provides pack-level window sums and entity membership."""

    FACT_COLUMNS = ["pfc", "period", "value_cr", "units_k", "qty_k"]

    def __init__(self, processed_dir: Path | None = None, *, fact=None, packs=None):
        """Load from data/processed, or (tests) from an in-memory Arrow `fact` table + `packs` list."""
        if fact is None:
            d = Path(processed_dir or PROCESSED_DIR)
            fact = pq.read_table(d / "fact_pack_month.parquet", columns=self.FACT_COLUMNS)
            packs = pq.read_table(d / "pack.parquet").to_pylist()
        self.fact = fact.select(self.FACT_COLUMNS)
        self.packs = packs
        self.pack_by_pfc = {p["pfc"]: p for p in self.packs}
        self.periods = sorted(pc.unique(self.fact["period"]).to_pylist())
        self.first_period, self.last_period = self.periods[0], self.periods[-1]
        self._window_cache = {}

    # ---------------- membership ----------------
    def key(self, entity_type: str, pack: dict) -> str:
        return ENTITY_TYPES[entity_type][0](pack)

    def label(self, entity_type: str, pack: dict) -> str:
        return ENTITY_TYPES[entity_type][1](pack)

    def scope_packs(self, scope_type: str, scope_key: str) -> list[dict]:
        kf = ENTITY_TYPES[scope_type][0]
        return [p for p in self.packs if kf(p) == str(scope_key)]

    def has_key(self, entity_type: str, key: str) -> bool:
        kf = ENTITY_TYPES[entity_type][0]
        return any(kf(p) == str(key) for p in self.packs)

    # ---------------- window sums ----------------
    def pack_sums(self, start: dt.date, end: dt.date) -> dict[int, tuple[float, float, float]]:
        """pfc -> (value, units, qty) summed over months start..end (inclusive). Cached."""
        k = (start, end)
        if k not in self._window_cache:
            f = self.fact.filter(pc.and_(pc.greater_equal(self.fact["period"], start),
                                         pc.less_equal(self.fact["period"], end)))
            g = f.group_by("pfc").aggregate([("value_cr", "sum"), ("units_k", "sum"), ("qty_k", "sum")]).to_pydict()
            if len(self._window_cache) > 16:
                self._window_cache.pop(next(iter(self._window_cache)))
            self._window_cache[k] = {p: (v, u, q) for p, v, u, q in
                                     zip(g["pfc"], g["value_cr_sum"], g["units_k_sum"], g["qty_k_sum"])}
        return self._window_cache[k]

    def monthly_series(self, pfcs: set[int]) -> dict[dt.date, tuple[float, float]]:
        """period -> (value, units) summed over the given packs."""
        import pyarrow as pa
        f = self.fact.filter(pc.is_in(self.fact["pfc"], value_set=pa.array(sorted(pfcs), type=pa.int64())))
        g = f.group_by("period").aggregate([("value_cr", "sum"), ("units_k", "sum")]).to_pydict()
        return {p: (v, u) for p, v, u in zip(g["period"], g["value_cr_sum"], g["units_k_sum"])}


@lru_cache(maxsize=1)
def default_engine() -> DataEngine:
    return DataEngine()
