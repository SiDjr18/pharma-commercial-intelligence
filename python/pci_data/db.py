"""DuckDB connection over the Parquet analytical layer (views only, no data copy)."""
from __future__ import annotations

from pathlib import Path

import duckdb

from .schema import PROCESSED_DIR, PROJECT_ROOT

SQL_DIR = PROJECT_ROOT / "sql"
# Executed in order: base views (M3), then the M4 analytics layer.
SQL_FILES = ["views.sql", "periods.sql", "entities.sql", "metrics.sql", "domains.sql"]
TABLES = ["pack", "fact_pack_month", "pack_snapshot", "pack_price_month"]


def connect(processed_dir: Path | None = None, threads: int = 4, memory_limit: str = "2GB"):
    """In-memory DuckDB with all analytical views registered.

    threads / memory_limit are capped so the laptop stays responsive.
    """
    d = Path(processed_dir or PROCESSED_DIR)
    missing = [t for t in TABLES if not (d / f"{t}.parquet").exists()]
    if missing:
        raise FileNotFoundError(f"processed tables missing in {d}: {missing} — run pci_data.build_processed")
    con = duckdb.connect(":memory:")
    con.execute(f"SET threads = {int(threads)}")
    con.execute(f"SET memory_limit = '{memory_limit}'")
    for name in SQL_FILES:
        con.execute((SQL_DIR / name).read_text(encoding="utf-8").replace("{processed}", d.as_posix()))
    return con
