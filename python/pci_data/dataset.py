"""Dataset selection and private-data isolation (P1 isolation fixes, 2026-09-28).

Two modes, chosen ONLY by the PCI_DATASET environment variable:

    unset / empty / "synthetic"  -> PUBLIC MODE: the fictional dataset in <repo>/data/synthetic (default).
                                    Needs no private data or configuration.
    "ims"                        -> PRIVATE LOCAL MODE: the licensed IMS processed layer in PCI_IMS_DATA_DIR.

Any other value fails closed. There is no fallback between the two modes and no discovery: nothing searches
drives, parent folders, user folders, Power BI folders or other environment variables. The only data paths are
<repo>/data/synthetic and the explicit PCI_IMS_DATA_DIR.

PCI_IMS_DATA_DIR must be an existing, absolute directory OUTSIDE the git repository: not inside it, not a parent
of it, not reached through a link that points into it (or placed inside it), not inside any other git work tree
and not a drive root. IMS-derived outputs (Power BI exports, reports, profiles, caches) are written to
subfolders of that directory, never into the repository.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"
DATASET_ENV = "PCI_DATASET"
IMS_DIR_ENV = "PCI_IMS_DATA_DIR"
DATASET_NAMES = ("synthetic", "ims")
DEFAULT_DATASET = "synthetic"
# subfolders of PCI_IMS_DATA_DIR that receive IMS-derived outputs
OUTPUT_KINDS = ("powerbi", "reports", "profile", "cache")


class DatasetConfigError(ValueError):
    """Invalid dataset configuration. The application refuses to start (fail closed)."""


def dataset_name(env=None) -> str:
    """Active dataset name from PCI_DATASET (unset or empty = synthetic)."""
    env = os.environ if env is None else env
    name = (env.get(DATASET_ENV) or "").strip().lower() or DEFAULT_DATASET
    if name not in DATASET_NAMES:
        hint = (" The licensed dataset is selected with PCI_DATASET=ims plus PCI_IMS_DATA_DIR."
                if name == "private" else "")
        raise DatasetConfigError(f"PCI_DATASET must be one of {sorted(DATASET_NAMES)} (unset = synthetic), "
                                 f"got {name[:40]!r}.{hint}")
    return name


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def is_within(child, parent) -> bool:
    """True if `child` is `parent` or lies below it (textual comparison of normalised absolute paths)."""
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def validate_ims_dir(raw, repo_root: Path = REPO_ROOT) -> Path:
    """Return the resolved IMS data directory or raise DatasetConfigError. Error messages never echo the path."""
    if raw is None or not str(raw).strip():
        raise DatasetConfigError("PCI_DATASET=ims requires PCI_IMS_DATA_DIR: the absolute path of the private IMS "
                                 "data directory, outside the repository.")
    text = str(raw).strip().strip('"')
    p = Path(text)
    if not p.is_absolute():
        raise DatasetConfigError("PCI_IMS_DATA_DIR must be an absolute path.")
    if not p.exists():
        raise DatasetConfigError("PCI_IMS_DATA_DIR does not exist.")
    if not p.is_dir():
        raise DatasetConfigError("PCI_IMS_DATA_DIR is not a directory.")
    literal = Path(os.path.normpath(text))      # as configured ('..' collapsed): catches links placed in the repo
    resolved = p.resolve(strict=True)           # links/junctions followed: catches links pointing into the repo
    repo = Path(repo_root).resolve(strict=True)
    for cand in (literal, resolved):
        if is_within(cand, repo):
            raise DatasetConfigError("PCI_IMS_DATA_DIR must be outside the git repository (it is inside it, "
                                     "directly or through a link).")
        if is_within(repo, cand):
            raise DatasetConfigError("PCI_IMS_DATA_DIR must not contain the git repository (it is a parent of it).")
        if cand.parent == cand:
            raise DatasetConfigError("PCI_IMS_DATA_DIR must not be a drive or filesystem root.")
    for d in (resolved, *resolved.parents):
        if (d / ".git").exists():
            raise DatasetConfigError("PCI_IMS_DATA_DIR must not be inside a git work tree (git could track it).")
    return resolved


def dataset_dir(name: str, env=None, repo_root: Path = REPO_ROOT) -> Path:
    """Folder of the processed layer for dataset `name`. Explicit paths only; nothing is searched."""
    if name == "synthetic":
        return Path(repo_root) / "data" / "synthetic"
    if name == "ims":
        env = os.environ if env is None else env
        return validate_ims_dir(env.get(IMS_DIR_ENV), repo_root)
    raise DatasetConfigError(f"unknown dataset {name!r}")


def assert_outside_repo(path, repo_root: Path = REPO_ROOT) -> Path:
    """Refuse an IMS-derived output location inside the repository (checked before anything is written)."""
    p = Path(path)
    probe = p
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    for cand in (p.absolute(), probe.resolve()):
        if is_within(cand, Path(repo_root).resolve()):
            raise DatasetConfigError("refusing to write IMS-derived output inside the git repository; IMS outputs "
                                     "belong under PCI_IMS_DATA_DIR.")
    return p


def check_manifest(manifest: dict, name: str) -> None:
    """Fail closed when the active folder holds the other dataset (e.g. licensed data copied into data/synthetic)."""
    is_synthetic = manifest.get("dataset") == "synthetic"
    if name == "synthetic" and not is_synthetic:
        raise DatasetConfigError("data/synthetic does not hold the generated synthetic dataset (manifest not marked "
                                 "synthetic). Regenerate it: cd python && python -m pci_synthetic.generate")
    if name == "ims" and is_synthetic:
        raise DatasetConfigError("PCI_IMS_DATA_DIR holds the synthetic dataset, not the IMS processed layer.")
