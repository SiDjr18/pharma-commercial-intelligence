import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from pci_data.schema import MANIFEST_PATH, PROCESSED_DIR  # noqa: E402

# Baseline facts established in M2 profiling (DATA_DICTIONARY.md / DATA_QUALITY_BASELINE.md).
# Any change here means the source or the transformation changed and must be reviewed.
BASELINE = {
    "source_rows": 105_317,
    "source_columns": 203,
    "n_periods": 36,
    "first_period": "2021-06-01",
    "last_period": "2024-05-01",
    "snapshot_years": [2022, 2023, 2024],
    "n_price_periods": 6,
    "products": 60_079,
    "product_subgroups": 65_398,
    "manufacturers": 1_106,
    "companies": 1_077,
    "subgroups": 1_889,
    "groups": 261,
    "supergroups": 25,
    "nfc_codes": 261,
    "group_supergroup_exceptions": 2,
    "brand_names_shared_across_companies": 658,
    "dormant_packs_all_36_months_zero": 8_895,
    "pack_launch_unknown": 682,
    "prod_launch_unknown": 1_953,
    "molecule_null_rows": 1,
}


def pytest_configure(config):
    config.addinivalue_line("markers", "source: re-reads the private source workbook (slow, ~2 min)")


@pytest.fixture(scope="session")
def manifest():
    if not MANIFEST_PATH.exists():
        pytest.skip("processed layer not built (data/processed/_manifest.json missing)")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def con(manifest):
    from pci_data.db import connect
    c = connect(PROCESSED_DIR)
    yield c
    c.close()


@pytest.fixture(scope="session")
def baseline():
    return BASELINE


def skip_source():
    return os.environ.get("PCI_SKIP_SOURCE") == "1"


@pytest.fixture(scope="session")
def py_engine(manifest):
    """Independent Python engine (pyarrow; no SQL) over the same Parquet files — loaded once."""
    from pci_analytics.engine import DataEngine
    return DataEngine(PROCESSED_DIR)
