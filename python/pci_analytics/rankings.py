"""Deterministic ranking rules (independent Python implementation of the M4 rules).

rank_value : value rounded to 1e-9 crore (Rs 0.01) descending, ties by entity_key ascending
             (UTF-8 byte order, as DuckDB compares VARCHAR).
rank_growth: growth ratio (cur/prior, rounded 1e-9) descending with undefined growth LAST,
             then rounded value descending, then entity_key ascending.
Rounding makes ranks immune to last-bit floating-point summation differences.
"""
from __future__ import annotations

import math

RANK_DECIMALS = 9


_SCALE = 10.0 ** RANK_DECIMALS


def _r(x: float) -> float:
    """Round half away from zero on the scaled binary value (x * 1e9), i.e. std::round semantics
    as used by DuckDB round(DOUBLE, 9) — not Python's banker's rounding. Inputs are >= 0."""
    y = abs(x) * _SCALE
    f = math.floor(y)
    r = (f + 1 if y - f >= 0.5 else f) / _SCALE
    return r if x >= 0 else -r


def value_rank_key(row: dict):
    return (-_r(row["value_cur"]), row["entity_key"].encode("utf-8"))


def growth_rank_key(row: dict):
    ratio = row["value_cur"] / row["value_prior"] if row["value_prior"] else None
    return (ratio is None, -_r(ratio) if ratio is not None else 0.0, -_r(row["value_cur"]),
            row["entity_key"].encode("utf-8"))


def assign_ranks(rows: list[dict]) -> list[dict]:
    for i, r in enumerate(sorted(rows, key=value_rank_key), 1):
        r["rank_value"] = i
    for i, r in enumerate(sorted(rows, key=growth_rank_key), 1):
        r["rank_growth"] = i
    n = len(rows)
    for r in rows:
        r["n_entities"] = n
    return rows
