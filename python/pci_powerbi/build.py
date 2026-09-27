"""Write the Power BI Project (PBIP) for M12: TMDL semantic model + PBIR report + M11 theme.

Output: dashboards/PCI_Commercial_Intelligence.pbip (+ .SemanticModel/, .Report/). Text only: no data is
written here. Power BI Desktop caches imported data in `<name>.SemanticModel/.pbi/cache.abf`, which is
git-ignored, as are *.pbix / *.pbit.

Run:  cd python && ..\\.venv\\Scripts\\python.exe -m pci_powerbi.build
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from pci_analytics.opportunity_config import DEFAULT_CONFIG
from pci_data.schema import PROCESSED_DIR
from pci_analytics.scenario import METHODOLOGY_VERSION as SCN_VERSION

from . import model, report, theme

ROOT = Path(__file__).resolve().parents[2]
DASH = ROOT / "dashboards"
NAME = "PCI_Commercial_Intelligence"
# Public snapshot: the committed private project never records a machine path. Set the Power Query parameters
# in Power BI Desktop, or regenerate with `--data-root <folder> --out <folder>` (writes the real folder).
PRIVATE_DATA_ROOT = "<PRIVATE_DATA_ROOT>"
PROCESSED = PROCESSED_DIR               # active dataset (PCI_DATASET; default private)
THEME_FILE = "PCI_M11_Theme.json"
S = report.S


def latest_anchor_label() -> str:
    """Default anchor = latest data month (derived from the processed layer, never typed)."""
    from pci_data.db import connect
    return connect().execute("SELECT max(period) FROM dim_period").fetchone()[0].strftime("%b %Y")


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


SYNTHETIC_OUT = DASH / "_synthetic"     # git-ignored target of the synthetic build (M13)


def build(out_dir: Path | None = None, processed_dir: Path = PROCESSED, anchor_label: str | None = None) -> dict:
    """Private dataset (default): writes the committed project in dashboards/ (M12, unchanged).
    Synthetic dataset (PCI_DATASET=synthetic): writes the same model/report to dashboards/_synthetic/ with its
    parameters pointing at data/synthetic; it can never overwrite the committed private project."""
    from pci_data.schema import DATASETS
    synthetic = Path(processed_dir).resolve() == DATASETS["synthetic"].resolve()
    out_dir = Path(out_dir) if out_dir else (SYNTHETIC_OUT if synthetic else DASH)
    if synthetic and out_dir.resolve() == DASH.resolve():
        raise ValueError("refusing to write a synthetic-data project over the committed private dashboards/ project")
    anchor_label = anchor_label or latest_anchor_label()
    sm, rp = out_dir / f"{NAME}.SemanticModel", out_dir / f"{NAME}.Report"
    for d in (sm / "definition", rp / "definition", rp / "StaticResources"):
        if d.exists():
            shutil.rmtree(d)           # regenerate definitions; never touches .pbi/ caches or other files
    _dump(out_dir / f"{NAME}.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True}})
    # ---- semantic model
    _dump(sm / "definition.pbism", {"version": "4.2", "settings": {}})
    _text(sm / "definition" / "database.tmdl", "database\n\tcompatibilityLevel: 1601\n")
    ts = model.tables()
    _text(sm / "definition" / "model.tmdl", model.model_tmdl(ts))
    private_default = Path(processed_dir).resolve() == DATASETS["private"].resolve()
    folder = PRIVATE_DATA_ROOT if private_default else str(processed_dir)
    _text(sm / "definition" / "expressions.tmdl",
          model.expressions_tmdl(folder, folder + "\\powerbi" if private_default else str(processed_dir / "powerbi")))
    _text(sm / "definition" / "relationships.tmdl", model.relationships_tmdl())
    for t in ts:
        _text(sm / "definition" / "tables" / f"{t.name}.tmdl", model.table_tmdl(t))
    # ---- report
    versions = {"opportunity": DEFAULT_CONFIG.version, "fingerprint": DEFAULT_CONFIG.fingerprint(),
                "scenario": SCN_VERSION, **({"dataset": "synthetic"} if synthetic else {})}
    _dump(rp / "definition.pbir", {"version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    _dump(rp / "definition" / "version.json", {"$schema": f"{S}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    _dump(rp / "definition" / "report.json", report.report_json(THEME_FILE))
    _dump(rp / "StaticResources" / "RegisteredResources" / THEME_FILE, theme.theme())
    ps = report.pages(anchor_label, versions)
    _dump(rp / "definition" / "pages" / "pages.json", {"$schema": f"{S}/pagesMetadata/1.0.0/schema.json",
                                                       "pageOrder": [p.name for p in ps], "activePageName": ps[0].name})
    n_vis = 0
    for p in ps:
        _dump(rp / "definition" / "pages" / p.name / "page.json", p.page_json())
        for z, v in enumerate(p.visuals):
            _dump(rp / "definition" / "pages" / p.name / "visuals" / v.name / "visual.json", v.to_json(z * 1000))
            n_vis += 1
    return {"pbip": str(out_dir / f"{NAME}.pbip"), "tables": len(ts), "measures": len(model.measure_names()),
            "pages": len(ps), "visuals": n_vis, "anchor_default": anchor_label, **versions}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Generate the PBIP for the active dataset (PCI_DATASET).")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default: dashboards/ for private, "
                                                            "dashboards/_synthetic/ for synthetic)")
    ap.add_argument("--data-root", type=Path, default=None,
                    help="M14 portability: absolute folder holding the processed-layer Parquet on THIS machine; written "
                         "into the Power Query parameters (default: the active dataset folder)")
    a = ap.parse_args()
    root = a.data_root.resolve() if a.data_root else PROCESSED
    if a.data_root and a.out is None and root != PROCESSED.resolve():
        ap.error("--data-root other than the active dataset needs an explicit --out (keeps the committed project intact)")
    print(json.dumps(build(out_dir=a.out, processed_dir=root), indent=2))
