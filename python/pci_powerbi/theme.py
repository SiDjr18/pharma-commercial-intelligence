"""Power BI theme derived from the M11 design tokens (app/web/style.css :root).

The token values are read from style.css at build time, so the report and the web app share one
palette: warm off-white canvas, white panels, ink-first charts, one teal accent, green/red only
paired with signs, grey for "no evidence". No gradients, shadows or rainbow palettes.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STYLE = ROOT / "app" / "web" / "style.css"
TOKENS = ("paper", "surface", "surface-2", "surface-3", "ink", "ink-2", "muted", "faint", "line", "line-2",
          "accent", "pos", "neg", "warn", "info", "na", "c-main", "c-prior", "c-accent", "c-2", "c-3", "grid",
          "s1", "s2", "s3", "s4", "s5", "navy")


def tokens() -> dict[str, str]:
    root = STYLE.read_text(encoding="utf-8").split(":root", 1)[1].split("}", 1)[0]
    found = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", root))
    missing = [t for t in TOKENS if t not in found]
    if missing:
        raise ValueError(f"style.css tokens missing: {missing}")
    return {t: found[t] for t in TOKENS}


SANS = "Segoe UI"            # M11 --sans resolves to Segoe UI on Windows
SANS_BOLD = "Segoe UI Semibold"
SERIF = "Georgia"            # M11 --serif fallback available in Power BI


def _solid(c):
    return {"solid": {"color": c}}


def theme() -> dict:
    t = tokens()
    table_common = {
        "grid": [{"gridVertical": False, "gridHorizontal": True, "gridHorizontalColor": _solid(t["grid"]),
                  "rowPadding": 3, "outlineColor": _solid(t["line"])}],
        "columnHeaders": [{"fontColor": _solid(t["ink-2"]), "backColor": _solid(t["surface-2"]),
                           "fontFamily": SANS_BOLD, "fontSize": 9, "outline": "Bottom only", "wordWrap": True}],
        "values": [{"fontColorPrimary": _solid(t["ink"]), "fontColorSecondary": _solid(t["ink"]),
                    "backColorPrimary": _solid(t["surface"]), "backColorSecondary": _solid(t["surface-2"]),
                    "fontFamily": SANS, "fontSize": 9}],
        "total": [{"fontFamily": SANS_BOLD, "fontColor": _solid(t["ink"]), "backColor": _solid(t["surface-3"])}],
    }
    return {
        "name": "Pharma Commercial Intelligence (M11)",
        "dataColors": [t["c-main"], t["c-accent"], t["c-2"], t["c-3"], t["c-prior"], t["s4"], t["info"], t["warn"]],
        "good": t["pos"], "bad": t["neg"], "neutral": t["na"],
        "maximum": t["s5"], "center": t["s3"], "minimum": t["s1"],
        "background": t["surface"], "foreground": t["ink"], "tableAccent": t["accent"],
        "foregroundNeutralSecondary": t["muted"], "foregroundNeutralTertiary": t["faint"],
        "backgroundLight": t["surface-2"], "backgroundNeutral": t["line"],
        "textClasses": {
            "callout": {"fontFace": SANS_BOLD, "fontSize": 20, "color": t["ink"]},
            "title": {"fontFace": SANS_BOLD, "fontSize": 11, "color": t["ink"]},
            "header": {"fontFace": SANS_BOLD, "fontSize": 12, "color": t["ink"]},
            "label": {"fontFace": SANS, "fontSize": 9, "color": t["ink-2"]},
            "largeTitle": {"fontFace": SERIF, "fontSize": 18, "color": t["ink"]},
        },
        "visualStyles": {
            "*": {"*": {
                "background": [{"show": True, "color": _solid(t["surface"]), "transparency": 0}],
                "border": [{"show": True, "color": _solid(t["line"]), "radius": 6}],
                "dropShadow": [{"show": False}],
                "title": [{"show": True, "fontFamily": SANS_BOLD, "fontSize": 11, "fontColor": _solid(t["ink"]),
                           "titleWrap": True, "alignment": "left"}],
                "subTitle": [{"fontFamily": SANS, "fontSize": 9, "fontColor": _solid(t["muted"])}],
                "padding": [{"top": 6, "bottom": 6, "left": 10, "right": 10}],
                "categoryAxis": [{"gridlineShow": False, "labelColor": _solid(t["muted"]), "fontSize": 9,
                                  "titleColor": _solid(t["muted"])}],
                "valueAxis": [{"gridlineShow": True, "gridlineColor": _solid(t["grid"]), "labelColor": _solid(t["muted"]),
                               "fontSize": 9, "titleColor": _solid(t["muted"])}],
                "legend": [{"labelColor": _solid(t["ink-2"]), "fontSize": 9, "position": "Top"}],
                "labels": [{"color": _solid(t["ink-2"]), "fontSize": 8}],
            }},
            "page": {"*": {
                "background": [{"color": _solid(t["paper"]), "transparency": 0}],
                "outspace": [{"color": _solid(t["paper"]), "transparency": 0}],
            }},
            "tableEx": {"*": table_common},
            "pivotTable": {"*": {**table_common,
                                 "rowHeaders": [{"fontColor": _solid(t["ink"]), "fontFamily": SANS, "fontSize": 9,
                                                 "backColor": _solid(t["surface"])}]}},
            "slicer": {"*": {
                "background": [{"show": True, "color": _solid(t["surface"]), "transparency": 0}],
                "border": [{"show": True, "color": _solid(t["line"]), "radius": 6}],
                "title": [{"show": False}],
                "header": [{"show": True, "fontColor": _solid(t["muted"]), "fontFamily": SANS_BOLD, "textSize": 8}],
                "items": [{"fontColor": _solid(t["ink"]), "fontFamily": SANS, "textSize": 10}],
            }},
            "textbox": {"*": {"background": [{"show": False}], "border": [{"show": False}], "title": [{"show": False}],
                              "padding": [{"top": 0, "bottom": 0, "left": 2, "right": 2}]}},
            "card": {"*": {
                "labels": [{"color": _solid(t["ink"]), "fontSize": 18, "fontFamily": SANS_BOLD}],
                "categoryLabels": [{"show": True, "color": _solid(t["muted"]), "fontSize": 9}],
            }},
            "multiRowCard": {"*": {
                "dataLabels": [{"color": _solid(t["ink"]), "fontSize": 15, "fontFamily": SANS_BOLD}],
                "categoryLabels": [{"color": _solid(t["muted"]), "fontSize": 9, "fontFamily": SANS}],
                "card": [{"barShow": True, "barColor": _solid(t["accent"]), "barWeight": 2,
                          "outlineColor": _solid(t["line"]), "cardBackground": _solid(t["surface"])}],
            }},
            "actionButton": {"*": {"border": [{"show": False}], "background": [{"show": False}]}},
        },
    }
