"""M5: deterministic ranking rules (synthetic) + rank parity with SQL on real data."""
import datetime as dt

import pytest

from pci_analytics import metrics, rankings
from pci_analytics.validation import _sql_rows


def _rows(spec):
    return [{"entity_key": k, "value_cur": v, "value_prior": p} for k, v, p in spec]


def test_value_rank_desc_ties_by_key():
    r = rankings.assign_ranks(_rows([("B", 5.0, 1.0), ("A", 5.0, 1.0), ("C", 9.0, 1.0), ("D", 0.0, 0.0)]))
    assert [x["entity_key"] for x in sorted(r, key=lambda x: x["rank_value"])] == ["C", "A", "B", "D"]
    assert all(x["n_entities"] == 4 for x in r)


def test_float_noise_is_a_tie():
    """Values equal up to summation noise must tie (then key order decides), never flip on last bits."""
    a = 0.1 + 0.2          # 0.30000000000000004
    r = rankings.assign_ranks(_rows([("Z", a, 1.0), ("A", 0.3, 1.0)]))
    assert {x["entity_key"]: x["rank_value"] for x in r} == {"A": 1, "Z": 2}


def test_growth_rank_nulls_last_then_value_then_key():
    r = rankings.assign_ranks(_rows([("A", 10.0, 5.0),    # +100%
                                     ("B", 10.0, 0.0),    # undefined
                                     ("C", 30.0, 15.0),   # +100% (tie on ratio, higher value)
                                     ("D", 1.0, None),    # undefined
                                     ("E", 6.0, 5.0)]))   # +20%
    order = [x["entity_key"] for x in sorted(r, key=lambda x: x["rank_growth"])]
    assert order == ["C", "A", "E", "B", "D"]


def test_key_order_is_utf8_bytes():
    r = rankings.assign_ranks(_rows([("b", 1.0, 1.0), ("B", 1.0, 1.0), ("(U", 1.0, 1.0), ("Á", 1.0, 1.0)]))
    assert [x["entity_key"] for x in sorted(r, key=lambda x: x["rank_value"])] == ["(U", "B", "b", "Á"]


def test_rounding_to_1e9():
    assert rankings._r(1.4999999e-9) == 1e-9 and rankings._r(1.6e-9) == 2e-9 and rankings._r(0.0) == 0.0
    assert rankings._r(-1.6e-9) == -rankings._r(1.6e-9)


def test_rounding_matches_duckdb_round(con):
    """The rank key must round exactly like SQL round(x, 9), incl. values at/near .5 boundaries."""
    import random
    rnd = random.Random(42)
    xs = [rnd.uniform(0, 1e4) for _ in range(3000)] + [rnd.uniform(0, 1e-6) for _ in range(3000)]
    xs += [(k + 0.5) / 1e9 for k in range(2000)] + [k + 0.5e-9 for k in range(1000)]
    sql = [r[0] for r in con.execute("SELECT round(x, 9) FROM (SELECT unnest(?::DOUBLE[]) AS x)", [xs]).fetchall()]
    assert len(sql) == len(xs)
    mism = [(x, s, rankings._r(x)) for x, s in zip(xs, sql) if s != rankings._r(x)]
    assert not mism, mism[:3]


def test_ranks_are_permutations():
    r = rankings.assign_ranks(_rows([(str(i), float(i % 3), float(i % 2)) for i in range(50)]))
    assert sorted(x["rank_value"] for x in r) == list(range(1, 51))
    assert sorted(x["rank_growth"] for x in r) == list(range(1, 51))


def test_rank_parity_with_sql_on_real_ties(con, py_engine):
    """Products: thousands of exact zero-value ties and near-equal sums; ranks must match SQL exactly."""
    a = dt.date(2024, 5, 1)
    sql = {r["entity_key"]: (r["rank_value"], r["rank_growth"])
           for r in _sql_rows(con, "SELECT entity_key, rank_value, rank_growth FROM entity_period('product', ?, 'MAT')", [a])}
    py = {r["entity_key"]: (r["rank_value"], r["rank_growth"]) for r in metrics.entity_period(py_engine, "product", a, "MAT")}
    ties = sum(1 for r in metrics.entity_period(py_engine, "product", a, "MAT") if r["value_cur"] == 0)
    assert ties > 1000
    assert sql == py
