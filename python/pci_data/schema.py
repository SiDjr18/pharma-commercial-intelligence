"""Source-to-analytical column mapping for the IMS DATA sheet.

Every one of the 203 source columns is classified here. Source column names are
never altered in the source; snake_case names exist only in the processed layer.
Unknown or unexpected headers make the build fail loudly.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from .dataset import (REPO_ROOT, SYNTHETIC_DIR, DatasetConfigError, assert_outside_repo, check_manifest,
                      dataset_dir, dataset_name)

PROJECT_ROOT = REPO_ROOT
# Private licensed source (never copied). M14 portability: override with PCI_SOURCE_PATH on another machine.
import os as _os
SOURCE_PATH = Path(_os.environ.get("PCI_SOURCE_PATH") or "<PRIVATE_WORKBOOK_PATH>")   # set PCI_SOURCE_PATH to the private workbook
SOURCE_SHEET = "DATA"

# Dataset switch (P1 isolation, see pci_data/dataset.py). PCI_DATASET selects the processed layer every consumer
# reads (DuckDB views, Python engine, app, agents, Power BI). Unset / "synthetic" = the fictional public dataset in
# data/synthetic (default). "ims" = the licensed IMS layer in the explicit external PCI_IMS_DATA_DIR. Anything
# else, or an invalid IMS directory, fails closed here, at import, before any data is read.
DATASET = dataset_name()
PROCESSED_DIR = dataset_dir(DATASET)
MANIFEST_PATH = PROCESSED_DIR / "_manifest.json"
IMS_DIR = PROCESSED_DIR if DATASET == "ims" else None
# configured datasets only (the IMS folder is known only when explicitly configured and active)
DATASETS = {"synthetic": SYNTHETIC_DIR, **({"ims": IMS_DIR} if IMS_DIR else {})}


def require_ims(what: str) -> Path:
    """Private-only tools (workbook build, profiling) run only in explicit IMS mode."""
    if DATASET != "ims":
        raise DatasetConfigError(f"{what} reads the licensed source and runs only with PCI_DATASET=ims and "
                                 "PCI_IMS_DATA_DIR set (outputs go there, never into the repository).")
    return IMS_DIR


def output_dir(kind: str, public_default: Path) -> Path:
    """Where a generated output goes. IMS mode: <PCI_IMS_DATA_DIR>/<kind> (checked to be outside the repository).
    Synthetic mode: `public_default` (repository paths, git-ignored where generated)."""
    if DATASET == "ims":
        return assert_outside_repo(IMS_DIR / kind)
    return Path(public_default)


def load_active_manifest() -> dict:
    """Manifest of the active dataset, checked to belong to that dataset (fails closed on a mismatch)."""
    import json
    if not MANIFEST_PATH.exists():
        hint = ("generate it: cd python && python -m pci_synthetic.generate" if DATASET == "synthetic"
                else "PCI_IMS_DATA_DIR does not contain a processed IMS layer (_manifest.json)")
        raise FileNotFoundError(f"{DATASET} dataset is not available: {hint}")
    m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    check_manifest(m, DATASET)
    return m

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_MON = "|".join(MONTHS)

# (source column, processed column) for the 23 descriptive columns, in source order.
DESCRIPTIVE = [
    ("PFC", "pfc"),
    ("Plain/Combination", "plain_combination"),
    ("Ind", "molecule_count"),
    ("MOLECULE_DESC", "molecule_desc"),
    ("PACK_DESC", "pack_desc"),
    ("PROD_CODE", "prod_code"),
    ("BRANDS", "brand"),
    ("MANUFAC CODE", "manufacturer_code"),
    ("MANUFACT. DESC", "manufacturer_desc"),
    ("COMPANY", "company"),
    ("INDIAN_MNC", "indian_mnc"),
    ("SUBGROUP", "subgroup"),
    ("GROUP", "therapy_group"),
    ("SUPERGROUP", "supergroup"),
    ("ACUTE_CHRONIC", "acute_chronic"),
    ("PACK_LNCH", "pack_launch_yyyymm"),
    ("PROD_LNCH", "prod_launch_yyyymm"),
    ("INDEX", "index_desc"),
    ("NFC", "nfc"),
    ("NFC 1", "nfc1"),
    ("NFC 2", "nfc2"),
    ("NFC 3", "nfc3"),
    ("SHORT DESCRIPTION", "form_short_desc"),
]
INT_DESCRIPTIVE = {"pfc", "molecule_count", "prod_code", "manufacturer_code",
                   "pack_launch_yyyymm", "prod_launch_yyyymm"}

FAMILY_TO_MEASURE = {None: "value_cr", "UNIT": "units_k", "QTY": "qty_k"}
SNAPSHOT_KIND = {"MONTH": "month", "CUMM": "ytd", "MAT": "mat"}
SPLIT_SUFFIX = {"LC": "value_cr", "UN": "units_k", "ST": "qty_k"}

RE_MONTHLY = re.compile(rf"^(?:(UNIT|QTY) )?({_MON})'(\d\d)$")
RE_SNAPSHOT = re.compile(r"^(?:(UNIT|QTY) )?(MONTH|CUMM|MAT) MAY'(\d\d)$")
RE_NI = re.compile(r"^NI 24 MONTHS MAT MAY'(\d\d)$")
RE_SPLIT = re.compile(r"^(SSA|HSA|DSA|TSA) - MAT ~ 05/(\d{4}) - (LC|ST|UN)$")
RE_PRICE = re.compile(rf"^PR_({_MON})'(\d\d)$")


@dataclass(frozen=True)
class ColumnSpec:
    source: str
    position: int
    kind: str                 # descriptive | monthly | snapshot | ni | split | price
    target: str               # processed column name
    period: dt.date | None = None   # first day of month for monthly/price
    snapshot_year: int | None = None


def month_date(mon: str, yy: str) -> dt.date:
    return dt.date(2000 + int(yy), MONTHS.index(mon) + 1, 1)


def classify(header: list[str]) -> list[ColumnSpec]:
    """Classify every source header; raise on anything unexpected."""
    if len(header) != len(set(header)):
        raise ValueError("duplicate header names in source")
    desc = dict(DESCRIPTIVE)
    specs: list[ColumnSpec] = []
    for i, h in enumerate(header):
        if h in desc:
            specs.append(ColumnSpec(h, i, "descriptive", desc[h]))
        elif m := RE_MONTHLY.match(h):
            fam, mon, yy = m.groups()
            specs.append(ColumnSpec(h, i, "monthly", FAMILY_TO_MEASURE[fam], period=month_date(mon, yy)))
        elif m := RE_SNAPSHOT.match(h):
            fam, kind, yy = m.groups()
            measure = FAMILY_TO_MEASURE[fam].split("_")[0]  # value | units | qty
            specs.append(ColumnSpec(h, i, "snapshot", f"{measure}_{SNAPSHOT_KIND[kind]}",
                                    snapshot_year=2000 + int(yy)))
        elif m := RE_NI.match(h):
            specs.append(ColumnSpec(h, i, "ni", "ni_24m_mat_value_cr", snapshot_year=2000 + int(m.group(1))))
        elif m := RE_SPLIT.match(h):
            panel, yyyy, suf = m.groups()
            specs.append(ColumnSpec(h, i, "split", f"{panel.lower()}_mat_{SPLIT_SUFFIX[suf]}",
                                    snapshot_year=int(yyyy)))
        elif m := RE_PRICE.match(h):
            mon, yy = m.groups()
            specs.append(ColumnSpec(h, i, "price", "price_rs", period=month_date(mon, yy)))
        else:
            raise ValueError(f"unclassified source column at position {i}: {h!r}")
    missing = set(desc) - {s.source for s in specs}
    if missing:
        raise ValueError(f"expected descriptive columns missing: {sorted(missing)}")
    return specs


def yyyymm_to_date(v: int | None) -> dt.date | None:
    """Launch fields: YYYYMM int; 0 is the source sentinel for 'unknown'."""
    if not v:
        return None
    y, m = divmod(int(v), 100)
    if not 1 <= m <= 12:
        raise ValueError(f"invalid YYYYMM launch value: {v}")
    return dt.date(y, m, 1)
