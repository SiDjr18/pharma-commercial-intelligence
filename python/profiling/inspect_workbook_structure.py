"""Inspect XLSX container structure without loading cell data.

Reads only workbook metadata (sheet names, <dimension> tags, part sizes).
Source file is opened read-only and never modified.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pci_data.schema import PROJECT_ROOT, SOURCE_PATH as SOURCE  # noqa: E402  (PCI_SOURCE_PATH-overridable)

OUT = PROJECT_ROOT / "data" / "profile" / "workbook_structure.json"


def main():
    with zipfile.ZipFile(SOURCE) as z:
        parts = [{"name": i.filename, "compressed": i.compress_size, "uncompressed": i.file_size}
                 for i in z.infolist()]
        wb = z.read("xl/workbook.xml").decode("utf-8")
        rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        sheets = re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wb)
        relmap = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
        relmap.update({k: v for v, k in re.findall(r'Target="([^"]+)"[^>]*Id="([^"]+)"', rels)})
        out_sheets = []
        for name, rid in sheets:
            target = relmap.get(rid, "")
            path = "xl/" + target.lstrip("/").replace("xl/", "") if target else None
            dim = None
            if path and path in z.namelist():
                with z.open(path) as f:
                    head = f.read(4096).decode("utf-8", "ignore")
                m = re.search(r'<dimension ref="([^"]+)"', head)
                dim = m.group(1) if m else None
            out_sheets.append({"name": name, "part": path, "dimension": dim})
        defined_names = re.findall(r'<definedName [^>]*name="([^"]+)"', wb)
    result = {"source": str(SOURCE), "sheets": out_sheets, "defined_names": defined_names,
              "has_pivot_caches": any("pivotCache" in p["name"] for p in parts),
              "has_external_links": any("externalLink" in p["name"] for p in parts),
              "parts": parts}
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "parts"}, indent=2))
    print("largest parts:")
    for p in sorted(parts, key=lambda x: -x["uncompressed"])[:8]:
        print(f'  {p["name"]}: {p["uncompressed"]/1e6:.1f} MB uncompressed')


if __name__ == "__main__":
    sys.exit(main())
