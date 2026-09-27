"""Report definition (PBIR) for the M12 Power BI layer.

Pages mirror the M11 information architecture, optimised for executive BI use. Every visual binds
only to model columns and to measures defined in model.py (tests assert this), so no number in the
report is computed outside the reconciled semantic model. Titles are questions (M11 hierarchy).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import F_PCT

S = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
V_VISUAL, V_PAGE, V_REPORT = "2.4.0", "2.0.0", "3.0.0"
W, H = 1280, 720
INK, INK2, MUTED, LINE, ACCENT, POS, NEG, WARN, WARN_SOFT, PRIOR, MAIN = (
    "#142231", "#384657", "#677282", "#e3e1d9", "#0d6570", "#1b6f45", "#ad3a2c", "#8d5a00", "#fbf0d9",
    "#a9b0b9", "#1f3b5a")


# ------------------------------------------------------------------------------------------ query helpers
def col(table: str, column: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}}


def mea(name: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "Metrics"}}, "Property": name}}


def lit(v) -> dict:
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, (int, float)):
        s = f"{v}D"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": s}}}


def solid(c: str) -> dict:
    return {"solid": {"color": lit(c)}}


def solid_measure(m: str) -> dict:
    return {"solid": {"color": {"expr": mea(m)}}}


def ref(f: dict) -> tuple[str, str]:
    k = next(iter(f))
    return f[k]["Expression"]["SourceRef"]["Entity"], f[k]["Property"]


def proj(f: dict, display: str | None = None) -> dict:
    ent, prop = ref(f)
    p = {"field": f, "queryRef": f"{ent}.{prop}", "nativeQueryRef": prop}
    if display:
        p["displayName"] = display
    return p


def _field(x):
    """x: ('Table','Column') for a column, 'Measure name' for a measure, or (field, display)."""
    if isinstance(x, dict):
        return x
    if isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], dict):
        return x
    if isinstance(x, tuple):
        return col(*x)
    return mea(x)


def _proj_list(items):
    out = []
    for it in items:
        if isinstance(it, tuple) and len(it) == 2 and isinstance(it[0], dict):
            out.append(proj(it[0], it[1]))
        elif isinstance(it, tuple) and len(it) == 3:          # (table, column, display)
            out.append(proj(col(it[0], it[1]), it[2]))
        elif isinstance(it, list):                            # [measure, display]
            out.append(proj(mea(it[0]), it[1]))
        else:
            out.append(proj(_field(it)))
    return out


# ------------------------------------------------------------------------------------------ visuals
@dataclass
class Visual:
    name: str
    vtype: str
    x: int
    y: int
    w: int
    h: int
    roles: dict = field(default_factory=dict)        # role -> list of items
    title: str | None = None
    title_measure: str | None = None
    subtitle: str | None = None
    objects: dict = field(default_factory=dict)
    container: dict = field(default_factory=dict)
    sort: tuple | None = None                        # (item, "Descending"|"Ascending")
    sync: str | None = None
    hidden_header: bool = False
    filters: list = field(default_factory=list)     # visual-level filters (e.g. Top N)

    def fields(self):
        """All model references used by this visual (for tests and smoke queries)."""
        out = []
        for items in self.roles.values():
            out += [p["field"] for p in _proj_list(items)]
        if self.sort:
            out.append(_proj_list([self.sort[0]])[0]["field"])
        if self.title_measure:
            out.append(mea(self.title_measure))
        return out

    def to_json(self, z: int) -> dict:
        vis: dict = {"visualType": self.vtype}
        if self.roles:
            vis["query"] = {"queryState": {r: {"projections": _proj_list(items)} for r, items in self.roles.items()}}
            if self.sort:
                vis["query"]["sortDefinition"] = {
                    "sort": [{"field": _proj_list([self.sort[0]])[0]["field"], "direction": self.sort[1]}],
                    "isDefaultSort": True}
        if self.objects:
            vis["objects"] = self.objects
        vco = dict(self.container)
        if self.title or self.title_measure:
            t = {"show": lit(True), "text": {"expr": mea(self.title_measure)} if self.title_measure else lit(self.title)}
            vco["title"] = [{"properties": t}]
            if self.subtitle:
                vco["subTitle"] = [{"properties": {"show": lit(True), "text": lit(self.subtitle)}}]
        elif self.vtype not in ("textbox", "actionButton"):
            vco.setdefault("title", [{"properties": {"show": lit(False)}}])
        if self.hidden_header:
            vco["visualHeader"] = [{"properties": {"show": lit(False)}}]
        if vco:
            vis["visualContainerObjects"] = vco
        if self.sync:
            vis["syncGroup"] = {"groupName": self.sync, "fieldChanges": True, "filterChanges": True}
        if self.vtype not in ("textbox", "actionButton"):
            vis["drillFilterOtherVisuals"] = True
        out = {"$schema": f"{S}/visualContainer/{V_VISUAL}/schema.json", "name": self.name,
               "position": {"x": self.x, "y": self.y, "z": z, "width": self.w, "height": self.h, "tabOrder": z},
               "visual": vis}
        if self.filters:
            out["filterConfig"] = {"filters": self.filters}
        return out


def textbox(name, x, y, w, h, runs: list[list[tuple]]) -> Visual:
    """runs: paragraphs -> list of (text, size_pt, color, font, bold)."""
    paragraphs = []
    for para in runs:
        tr = []
        for text, size, color, font, bold in para:
            style = {"fontFamily": font, "fontSize": f"{size}pt", "color": color}
            if bold:
                style["fontWeight"] = "bold"
            tr.append({"value": text, "textStyle": style})
        paragraphs.append({"textRuns": tr})
    return Visual(name, "textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": paragraphs}}]},
                  container={"background": [{"properties": {"show": lit(False)}}],
                             "border": [{"properties": {"show": lit(False)}}]}, hidden_header=True)


def slicer(name, x, y, w, h, column: tuple, header: str, single=True, search=True, sync=None, default=None,
           mode="Dropdown") -> Visual:
    objects = {"data": [{"properties": {"mode": lit(mode)}}],
               "selection": [{"properties": {"singleSelect": lit(single), "strictSingleSelect": lit(False)}}],
               "header": [{"properties": {"show": lit(True), "text": lit(header)}}]}
    general = {"selfFilterEnabled": lit(search)} if mode == "Dropdown" else {}
    if default is not None:
        general["filter"] = {"filter": {
            "Version": 2, "From": [{"Name": "s", "Entity": column[0], "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": column[1]}}],
                "Values": [[{"Literal": {"Value": "'" + default + "'"}}]]}}}]}}
    if general:
        objects["general"] = [{"properties": general}]
    return Visual(name, "slicer", x, y, w, h, roles={"Values": [column]}, objects=objects, sync=sync, hidden_header=True)


def card(name, x, y, w, h, measure: str, size=11, color=INK, title=None) -> Visual:
    return Visual(name, "card", x, y, w, h, roles={"Values": [measure]}, title=title,
                  objects={"labels": [{"properties": {"fontSize": lit(size), "color": solid(color),
                                                      "fontFamily": lit("Segoe UI")}}],
                           "categoryLabels": [{"properties": {"show": lit(False)}}],
                           "wordWrap": [{"properties": {"show": lit(True)}}]},
                  hidden_header=True)


def kpis(name, x, y, w, h, items, title=None, subtitle=None) -> Visual:
    return Visual(name, "multiRowCard", x, y, w, h, roles={"Values": items}, title=title, subtitle=subtitle,
                  objects={"dataLabels": [{"properties": {"fontSize": lit(14)}}],
                           "categoryLabels": [{"properties": {"fontSize": lit(8)}}]})


def color_rules(pairs) -> dict:
    """Table/matrix font colour by a colour measure: [(queryRef of column measure, colour measure)]."""
    return {"values": [{"properties": {"fontColor": solid_measure(cm)},
                        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"Metrics.{m}"}}
                       for m, cm in pairs]}


def is_topn_measure(name: str) -> bool:
    return "(Top " in name


def bar(name, x, y, w, h, category: tuple, value: str, title, subtitle=None, color_measure=None, color=None,
        tooltips=(), ascending=False, label_fmt=None) -> Visual:
    obj = {"labels": [{"properties": {"show": lit(True), "fontSize": lit(8), "labelDisplayUnits": lit(1)}}],
           "categoryAxis": [{"properties": {"showAxisTitle": lit(False), "maxMarginFactor": lit(45)}}],
           "valueAxis": [{"properties": {"showAxisTitle": lit(False), "show": lit(False)}}]}
    if color_measure:
        obj["dataPoint"] = [{"properties": {"fill": solid_measure(color_measure)},
                             "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}]
    elif color:
        obj["dataPoint"] = [{"properties": {"fill": solid(color)}}]
    roles = {"Category": [category], "Y": [value]}
    if tooltips and not is_topn_measure(value):
        # a top-N bar must carry only its own top-N measure: any other measure is non-blank for every
        # category and would pull all categories into the chart (found by the M12 visual smoke test)
        roles["Tooltips"] = list(tooltips)
    return Visual(name, "clusteredBarChart", x, y, w, h, roles=roles, title=title, subtitle=subtitle, objects=obj,
                  sort=(value, "Ascending" if ascending else "Descending"))


def trend(name, x, y, w, h, title, subtitle=None) -> Visual:
    obj = {"dataPoint": [{"properties": {"fill": solid(MAIN)}, "selector": {"metadata": "Metrics.Trend Value"}},
                         {"properties": {"fill": solid(PRIOR)}, "selector": {"metadata": "Metrics.Trend Value Prior Year"}}],
           "categoryAxis": [{"properties": {"showAxisTitle": lit(False)}}],
           "valueAxis": [{"properties": {"showAxisTitle": lit(False)}}],
           "legend": [{"properties": {"show": lit(True), "position": lit("Top")}}]}
    return Visual(name, "lineChart", x, y, w, h,
                  roles={"Category": [("Period", "Period")],
                         "Y": [["Trend Value", "Selected basis"], ["Trend Value Prior Year", "Same basis, prior year"]],
                         "Tooltips": ["Trend Growth %"]},
                  title=title, subtitle=subtitle, objects=obj)


def table(name, x, y, w, h, items, title, subtitle=None, sort=None, colors=(), matrix_rows=None) -> Visual:
    objects = color_rules(colors) if colors else {}
    if matrix_rows:
        objects = {**objects, "rowHeaders": [{"properties": {"showExpandCollapseButtons": lit(True)}}]}
        return Visual(name, "pivotTable", x, y, w, h, roles={"Rows": matrix_rows, "Values": items}, title=title,
                      subtitle=subtitle, objects=objects, sort=sort)
    return Visual(name, "tableEx", x, y, w, h, roles={"Values": items}, title=title, subtitle=subtitle,
                  objects=objects, sort=sort)


def topn_filter(name: str, table: str, column: str, measure: str, n: int) -> dict:
    """Visual-level Top N filter: Power BI evaluates the cheap ranking measure first and computes the other
    measures only for the N kept rows (M12 performance: product tables have 60,079 candidates)."""
    return {"name": name, "field": col(table, column), "type": "TopN", "howCreated": "User",
            "filter": {"Version": 2,
                       "From": [{"Name": "subquery", "Type": 2, "Expression": {"Subquery": {"Query": {
                           "Version": 2,
                           "From": [{"Name": "t", "Entity": table, "Type": 0}, {"Name": "m", "Entity": "Metrics", "Type": 0}],
                           "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column},
                                       "Name": "field"}],
                           "OrderBy": [{"Direction": 2, "Expression": {"Measure": {
                               "Expression": {"SourceRef": {"Source": "m"}}, "Property": measure}}}],
                           "Top": n}}}},
                                {"Name": "t", "Entity": table, "Type": 0}],
                       "Where": [{"Condition": {"In": {
                           "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column}}],
                           "Table": {"SourceRef": {"Source": "subquery"}}}}}]}}


def back_button(name, x, y) -> Visual:
    return Visual(name, "actionButton", x, y, 84, 32,
                  objects={"icon": [{"properties": {"shapeType": lit("back")}, "selector": {"id": "default"}}],
                           "text": [{"properties": {"show": lit(True), "text": lit("Back")}, "selector": {"id": "default"}}]},
                  container={"visualLink": [{"properties": {"show": lit(True), "type": lit("Back")}}]},
                  hidden_header=True)


# ------------------------------------------------------------------------------------------ pages
@dataclass
class Page:
    name: str
    display: str
    visuals: list
    drillthrough: tuple | None = None       # (table, column)
    hidden: bool = False

    def page_json(self) -> dict:
        p = {"$schema": f"{S}/page/{V_PAGE}/schema.json", "name": self.name, "displayName": self.display,
             "displayOption": "FitToPage", "height": H, "width": W}
        if self.drillthrough:
            fname = f"dt_{self.name}"
            p["type"] = "Drillthrough"
            p["pageBinding"] = {"name": f"binding_{self.name}", "type": "Drillthrough",
                                "parameters": [{"name": f"param_{self.name}", "boundFilter": fname,
                                                "fieldExpr": col(*self.drillthrough)}]}
            p["filterConfig"] = {"filters": [{"name": fname, "field": col(*self.drillthrough), "type": "Categorical",
                                              "howCreated": "Drillthrough"}]}
        if self.hidden:
            p["visibility"] = "HiddenInViewMode"
        return p


def header(prefix: str, title: str, subtitle: str, anchor_default: str, sync=True) -> list:
    return [
        textbox(f"{prefix}_title", 16, 6, 760, 38, [[(title, 17, INK, "Georgia", True)]]),
        textbox(f"{prefix}_subtitle", 16, 44, 776, 40, [[(subtitle, 9, MUTED, "Segoe UI", False)]]),
        slicer(f"{prefix}_anchor", 800, 4, 228, 54, ("Anchor", "Anchor"), "Anchor month", sync="anchor" if sync else None,
               default=anchor_default, search=False),
        slicer(f"{prefix}_basis", 1036, 4, 228, 54, ("Basis", "Basis Name"), "Basis", sync="basis" if sync else None,
               default="MAT", search=False),
        card(f"{prefix}_period", 800, 60, 464, 28, "Period Caption", size=9, color=INK2),
    ]


_DATASET = {"name": "private"}     # set by pages(); "synthetic" relabels the source line (M13)


def footer(prefix: str) -> Visual:
    if _DATASET["name"] == "synthetic":
        text = ("Source: SYNTHETIC dataset (fictional, structure-only; pci_synthetic, docs/SYNTHETIC_DATA.md) - values "
                "describe an invented market, not real sales · Definitions: ANALYTICS_METHODS.md · Power BI reconciled "
                "to the SQL/Python engines (docs/POWER_BI_ARCHITECTURE.md)")
    else:
        text = ("Source: processed IMS layer (data/processed, national secondary sales) · Value ₹ crore (confirmed); units/qty '000 "
                "(inferred) · Definitions: ANALYTICS_METHODS.md · Power BI reconciled to the SQL/Python engines "
                "(docs/POWER_BI_ARCHITECTURE.md)")
    return textbox(f"{prefix}_footer", 16, 700, 1248, 18, [[(text, 7, MUTED, "Segoe UI", False)]])


def pages(anchor_default: str, versions: dict) -> list[Page]:
    _DATASET["name"] = versions.get("dataset", "private")
    G = ("Market", "Supergroup")
    SG = ("Market", "Subgroup")
    P = []

    # 1. Executive overview ------------------------------------------------------------------------
    v = header("ex", "What changed in the market?",
               "Headline movement for the selected window, where it came from, who gained share and where the "
               "strongest descriptive opportunity evidence sits. National secondary sales, ₹ crore.", anchor_default)
    v += [
        kpis("ex_kpi", 16, 96, 836, 88, [["Value", "Market value (₹ cr)"], ["Value Growth %", "Value growth"],
                                          ["Value Change", "Value change (₹ cr)"], ["Units Growth %", "Units growth"]],
             title="How big is the market and how fast is it moving?"),
        kpis("ex_opp", 860, 96, 404, 88, [["Top Opportunity Market", "Top-scoring market"],
                                           ["Markets Scored", "Markets scored"],
                                           ["Scored Products Gaining Share", "Scored products gaining share"]],
             title="Where is the opportunity evidence?", subtitle="Descriptive OPP-1.0.0 scoring, not a forecast"),
        bar("ex_contrib", 16, 192, 404, 500, G, "Contribution to Total Growth pp",
            "Which therapy areas drove the change?", "Contribution to national value growth, pp",
            color_measure="Contribution Color", tooltips=["Value", "Value Growth %", "Share of Total %"]),
        trend("ex_trend", 428, 192, 416, 248, "How has market value moved over time?",
              "Selected basis ending each month vs the same window a year earlier"),
        bar("ex_gain", 852, 192, 412, 248, SG, "Subgroup Gain (Top 10)", "Which markets added the most value?",
            "Top 10 subgroups by value change, ₹ cr", color=POS, tooltips=["Value Growth %", "Share of Total %"]),
        bar("ex_share", 428, 448, 416, 244, ("Company", "Company"), "Company Share Mover (Top 8 each way)",
            "Which companies gained or lost national share?", "Top 8 gainers and top 8 decliners, pp",
            color_measure="Company Mover Color", tooltips=["Value", "Share of Market %"]),
        bar("ex_decl", 852, 448, 412, 244, SG, "Subgroup Decline (Top 10)", "Which markets lost the most value?",
            "Top 10 subgroups by value decline, ₹ cr", color=NEG, ascending=True,
            tooltips=["Value Growth %", "Share of Total %"]),
        footer("ex"),
    ]
    P.append(Page("p01_executive", "Executive Overview", v))

    # 2. Market intelligence -----------------------------------------------------------------------
    v = header("mk", "How are therapy markets moving?",
               "Primary market = therapy SUBGROUP; expand a therapy area to see its subgroups. Select a row to "
               "filter the trend. Shares and contributions are national.", anchor_default)
    v += [
        slicer("mk_sg", 16, 90, 300, 52, G, "Therapy area (supergroup)", single=False),
        slicer("mk_ac", 324, 90, 200, 52, ("Market", "Acute/Chronic"), "Acute / chronic", single=False, search=False),
        table("mk_matrix", 16, 146, 776, 546,
              [["Value", "Value (₹ cr)"], ["Value Growth %", "Growth"], ["Share of Total %", "Share of total"],
               ["Share of Total Change pp", "Share chg"], ["Contribution to Total Growth pp", "Contribution"],
               ["Evolution Index vs Total", "EI vs total"]],
              "Which markets are growing, and how much do they matter?",
              "Expand a therapy area to see subgroups. EI 100 = kept pace with the national market",
              sort=("Value", "Descending"), matrix_rows=[G, SG],
              colors=[("Value Growth %", "Growth Color"), ("Share of Total Change pp", "Total Share Change Color"),
                      ("Contribution to Total Growth pp", "Contribution Color")]),
        trend("mk_trend", 800, 146, 464, 268, "Is the selected market accelerating?",
              "Selected basis ending each month vs the same window a year earlier"),
        bar("mk_contrib", 800, 422, 464, 270, SG, "Subgroup Contribution (Top 12)",
            "Which subgroups contributed most to national growth?", "Top 12 by absolute contribution, pp",
            color_measure="Subgroup Contribution Color", tooltips=["Value", "Value Growth %"]),
        footer("mk"),
    ]
    P.append(Page("p02_market", "Market Intelligence", v))

    # 3. Brand & portfolio -------------------------------------------------------------------------
    v = header("br", "Which products are winning in their markets?",
               "Product identity = product code; brand names are not unique and are never used as a key. Shares are "
               "within the selected market (no market selected = national). Right-click a row → Drill through → Product detail.",
               anchor_default)
    v += [
        slicer("br_sg", 16, 90, 240, 52, G, "Therapy area", single=False),
        slicer("br_sub", 264, 90, 300, 52, SG, "Market (subgroup)", single=False),
        slicer("br_co", 572, 90, 300, 52, ("Company", "Company"), "Company", single=False),
        slicer("br_prod", 880, 90, 384, 52, ("Product", "Product"), "Product (brand · company · code)", single=False),
        table("br_table", 16, 146, 836, 546,
              [("Product", "Product Code", "Code"), ("Product", "Product", "Product"),
               ["Value", "Value (₹ cr)"], ["Value Growth %", "Growth"], ["Share of Market %", "Share of market"],
               ["Share Change pp", "Share chg"], ["Evolution Index", "EI"], ["Rank Value (Product)", "Rank"],
               ["Growth Evidence", "Growth evidence"]],
              "How is each product performing in the selected market?",
              "Top 500 products by value in the selection (visual filter); rank = value rank in the selection",
              sort=("Value", "Descending"),
              colors=[("Value Growth %", "Growth Color"), ("Share Change pp", "Share Change Color"),
                      ("Evolution Index", "Evolution Color")]),
        bar("br_top", 860, 146, 404, 300, ("Product", "Product"), "Product Value (Top 15)",
            "Which products are largest in the selection?", "Top 15 by value, ₹ cr", color=MAIN,
            tooltips=["Value Growth %", "Share of Market %"]),
        trend("br_trend", 860, 454, 404, 238, "How is the selection trending?", "Select product rows to focus"),
        footer("br"),
    ]
    v[[x.name for x in v].index("br_table")].filters = [topn_filter("topn_br_table", "Product", "Product Code", "Value", 500)]
    P.append(Page("p03_brand", "Brand & Portfolio", v))

    # 4. Company intelligence ----------------------------------------------------------------------
    v = header("co", "How are companies performing, and where?",
               "Shares and contributions are within the selected market (none = national). Select a company to see "
               "where it competes; right-click → Drill through → Company detail.", anchor_default)
    v += [
        slicer("co_sg", 16, 90, 240, 52, G, "Therapy area", single=False),
        slicer("co_sub", 264, 90, 300, 52, SG, "Market (subgroup)", single=False),
        slicer("co_mnc", 572, 90, 200, 52, ("Company", "Indian/MNC"), "Indian / MNC", single=False, search=False),
        table("co_table", 16, 146, 700, 546,
              [("Company", "Company", "Company"), ("Company", "Indian/MNC", "Indian/MNC"),
               ["Value", "Value (₹ cr)"], ["Value Growth %", "Growth"], ["Share of Market %", "Share of market"],
               ["Share Change pp", "Share chg"], ["Contribution to Market Growth pp", "Contribution"],
               ["Evolution Index", "EI"], ["Rank Value (Company)", "Rank"]],
              "Which companies lead the selected market?", "Sorted by value; rank within the selection",
              sort=("Value", "Descending"),
              colors=[("Value Growth %", "Growth Color"), ("Share Change pp", "Share Change Color"),
                      ("Contribution to Market Growth pp", "Market Contribution Color"),
                      ("Evolution Index", "Evolution Color")]),
        bar("co_share", 724, 146, 540, 268, ("Company", "Company"), "Company Share Mover (Top 8 each way)",
            "Who gained and lost share in the selected market?", "Top 8 gainers and decliners, pp",
            color_measure="Company Mover Color", tooltips=["Value", "Share of Market %"]),
        table("co_where", 724, 422, 540, 270,
              [["Value", "Value (₹ cr)"], ["Value Growth %", "Growth"], ["Share of Market %", "Share of market"],
               ["Evolution Index", "EI"]],
              "Where does the selected company compete?", "Select a company row; share = its share of each market",
              sort=("Value", "Descending"), matrix_rows=[G, SG],
              colors=[("Value Growth %", "Growth Color"), ("Evolution Index", "Evolution Color")]),
        footer("co"),
    ]
    P.append(Page("p04_company", "Company Intelligence", v))

    # 5. Opportunity intelligence ------------------------------------------------------------------
    OP, OM = "Opportunity Product", "Opportunity Market"
    v = header("op", "Where is the strongest evidence of opportunity?",
               "DESCRIPTIVE OPPORTUNITY SCORING (OPP-1.0.0): percentile evidence relative to all scored entities "
               "nationally. It is not a forecast, a probability or a guarantee. Filters select rows; they never change scores.",
               anchor_default)
    v += [
        card("op_avail", 16, 92, 1248, 30, "Opportunity Availability", size=9, color=WARN),
        slicer("op_sg", 16, 126, 240, 52, G, "Therapy area", single=False),
        slicer("op_sub", 264, 126, 300, 52, SG, "Market (subgroup)", single=False),
        slicer("op_co", 572, 126, 300, 52, ("Company", "Company"), "Company", single=False),
        slicer("op_ev", 880, 126, 384, 52, (OP, "Evidence"), "Evidence status", single=False, search=False),
        table("op_mkt", 16, 182, 548, 256,
              [SG, ["Market Opportunity Score", "Score"], ["Market Opportunity Rank", "Rank"],
               ["Market Opp Growth %", "Growth"], ["Market Opp Size Share %", "Size"], ["Market Evidence", "Evidence"]],
              "Which markets show the strongest evidence?", "Score = 60% growth percentile + 40% size percentile",
              sort=("Market Opportunity Score", "Descending")),
        table("op_quad", 572, 182, 312, 256, ["Scored Products"],
              "Opportunity matrix: how are scored products positioned?",
              "Momentum (EI vs its market) x market growth vs national; descriptive, not prescriptive",
              matrix_rows=[(OP, "Momentum Side")]),
        bar("op_evid", 892, 182, 372, 256, (OP, "Evidence"), "Products in Population",
            "How much of the population has sufficient evidence?",
            "Insufficient evidence is shown separately; it is never scored as zero", color=MAIN),
        table("op_prod", 16, 446, 828, 246,
              [(OP, "Product Code", "Code"), (OP, "Product in Market", "Product in market"),
               ["Product Opportunity Score", "Score"], ["Product Opportunity Rank", "Rank"],
               ["Product Opp Market Growth %", "Market growth"], ["Product Opp Evolution Index", "EI"],
               ["Product Opp Share in Market %", "Share in market"], ["Product Evidence", "Evidence"]],
              "Which products show the strongest evidence in their markets?",
              "Score = 30% market growth + 40% relative momentum (EI) + 30% market position (share), as percentiles",
              sort=("Product Opportunity Score", "Descending")),
        Visual("op_scatter", "scatterChart", 852, 446, 412, 246,
               roles={"Category": [(OP, "Entity Key")], "X": [["Momentum Percentile", "Momentum percentile"]],
                      "Y": [["Market Growth Percentile (Product)", "Market growth percentile"]],
                      "Tooltips": ["Product Opportunity Score", "Product Evidence"]},
               title="Where do the top-scored products sit?",
               subtitle="Top 500 by score (visual filter); percentiles within the national scored population",
               objects={"dataPoint": [{"properties": {"fill": solid(ACCENT)}}],
                        "categoryAxis": [{"properties": {"showAxisTitle": lit(True)}}],
                        "valueAxis": [{"properties": {"showAxisTitle": lit(True)}}]}),
        footer("op"),
    ]
    # matrix columns for the 2x2 view
    v[[x.name for x in v].index("op_quad")].roles["Columns"] = [(OP, "Market Growth Side")]
    v[[x.name for x in v].index("op_scatter")].filters = [
        topn_filter("topn_op_scatter", OP, "Entity Key", "Product Opportunity Score", 500)]
    P.append(Page("p05_opportunity", "Opportunity Intelligence", v))

    # 6. Scenario planning -------------------------------------------------------------------------
    v = header("sc", "What if price, volume, market growth or share changed?",
               "SCENARIO ANALYSIS — NOT A FORECAST. Deterministic what-if arithmetic on the observed baseline of one "
               f"entity (methodology {versions['scenario']}). No elasticity, no competitor reaction, no seasonality.",
               anchor_default)
    v += [
        slicer("sc_type", 16, 92, 360, 150, ("Scenario Type", "Scenario Type"), "Scenario type", mode="Basic", search=False),
        slicer("sc_prod", 16, 248, 360, 50, ("Product", "Product"), "Product (one)", single=True),
        slicer("sc_co", 16, 300, 360, 50, ("Company", "Company"), "Company (one)", single=True),
        slicer("sc_sg", 16, 352, 360, 50, G, "Market: therapy area", single=True),
        slicer("sc_tg", 16, 404, 360, 50, ("Market", "Therapy Group"), "Market: therapy group", single=True),
        slicer("sc_sub", 16, 456, 360, 50, SG, "Market: subgroup", single=True),
        slicer("sc_mol", 16, 508, 360, 50, ("Pack", "Molecule"), "Market: molecule", single=True),
        textbox("sc_note", 16, 560, 360, 132, [
            [("How the entity is chosen", 9, INK, "Segoe UI Semibold", False)],
            [("One product, or one company, or no product/company for a market entity. The most specific market "
              "filter is the market (share denominator). Assumptions come only from the allowed grids; anything "
              "missing, extra or ambiguous is REJECTED, never corrected. These controls are not synced to other pages.",
              8, MUTED, "Segoe UI", False)]]),
        slicer("sc_p", 384, 90, 280, 52, ("Price Change", "Price Change %"), "price_change_pct  (-100, 1000]", single=True),
        slicer("sc_v", 384, 144, 280, 50, ("Volume Change", "Volume Change %"), "volume_change_pct  [-100, 1000]", single=True),
        slicer("sc_g", 384, 196, 280, 50, ("Market Growth Assumption", "Market Growth %"), "market_growth_pct  [-100, 1000]", single=True),
        slicer("sc_t", 384, 248, 280, 50, ("Target Share", "Target Share %"), "target_share_pct  [0, 100]", single=True),
        card("sc_status", 672, 92, 592, 40, "Scenario Status", size=10, color=WARN, title=None),
        card("sc_entity", 672, 138, 592, 40, "Scenario Entity", size=10, color=INK),
        card("sc_formula", 672, 184, 592, 110, "Scenario Formula", size=9, color=INK2),
        kpis("sc_base", 384, 302, 436, 190,
             [["Baseline Value (OBSERVED)", "Value (₹ cr)"], ["Baseline Units (OBSERVED)", "Units ('000)"],
              ["Baseline Price per Pack (OBSERVED)", "Price (₹/pack)"],
              ["Baseline Market Value (OBSERVED)", "Market value (₹ cr)"], ["Baseline Share (OBSERVED)", "Share of market"]],
             title="Baseline — OBSERVED", subtitle="Selected entity, selected window"),
        kpis("sc_res", 828, 302, 436, 190,
             [["Scenario Value (CALCULATED)", "Scenario value (₹ cr)"], ["Scenario Value Change (CALCULATED)", "Change (₹ cr)"],
              ["Scenario Value Change % (CALCULATED)", "Change"], ["Scenario Units (CALCULATED)", "Units ('000)"],
              ["Scenario Price per Pack (CALCULATED)", "Price (₹/pack)"],
              ["Scenario Share Change pp (CALCULATED)", "Share change"]],
             title="Scenario — CALCULATED", subtitle="What-if arithmetic under the stated assumptions"),
        kpis("sc_dec", 384, 500, 880, 192,
             [["Price Effect (CALCULATED)", "Price effect (₹ cr)"], ["Volume Effect (CALCULATED)", "Volume effect (₹ cr)"],
              ["Price x Volume Interaction (CALCULATED)", "Interaction (₹ cr)"],
              ["Scenario Units Change % (CALCULATED)", "Units change"], ["Scenario Price Change % (CALCULATED)", "Price change"],
              ["Scenario Market Value (CALCULATED)", "Scenario market value (₹ cr)"],
              ["Scenario Market Value Change % (CALCULATED)", "Market value change"]],
             title="How does the change decompose?", subtitle="Price effect + volume effect + interaction = value change"),
        footer("sc"),
    ]
    P.append(Page("p06_scenario", "Scenario Planning", v))

    # 7. Methodology & data quality ----------------------------------------------------------------
    v = header("md", "How are these numbers defined, and can they be trusted?",
               "Every number comes from the reconciled semantic model; definitions are identical to the SQL engine "
               "and the independent Python engine.", anchor_default)

    def para(title, lines):
        return [[(title, 10, INK, "Segoe UI Semibold", False)]] + [[(ln, 8, INK2, "Segoe UI", False)] for ln in lines]

    v += [
        kpis("md_cov", 16, 96, 1248, 84, [["First Data Month", "First month"], ["Last Data Month", "Last month"],
                                          ["Packs (all)", "Packs (PFC)"], ["Products (count)", "Products (PROD_CODE)"],
                                          ["Companies (count)", "Companies"], ["Subgroups (count)", "Markets (subgroups)"]],
             title="What does the data cover?"),
        textbox("md_metrics", 16, 188, 408, 504, para("Metric definitions", [
            "Value = Σ value (₹ crore, confirmed) over the window; Units/Qty = Σ '000 (inferred scale).",
            "Growth % = current / prior × 100 − 100, only when prior > 0. Otherwise BLANK with a reason "
            "(no prior sales, no sales, comparison window before the data). Never 0.",
            "Share of market = entity / market (current filters minus product and company). Share of total = national.",
            "Share change (pp) = current share − prior share. Units share uses units.",
            "Contribution (pp) = (current − prior) / market prior × 100; sums to market growth.",
            "Evolution index = share now / share a year ago × 100 (100 = kept pace).",
            "Rank = deterministic rank within the current selection (value rounded to 1e-9, ties by key).",
        ])),
        textbox("md_periods", 432, 188, 408, 504, para("Periods, market and identity", [
            "MONTH = anchor month vs the same month a year earlier.",
            "YTD = calendar January..anchor vs the same months a year earlier (not the Indian fiscal year).",
            "MAT = 12 months ending at the anchor vs the previous 12 months.",
            "A window that starts before the first data month shows no values; a comparison window before the data "
            "gives BLANK growth.",
            "Primary market = therapy SUBGROUP (each pack belongs to exactly one). Therapy areas roll up via subgroup.",
            "Product identity = product code. Brand names are not unique and are display labels only.",
            "Not in the data (never shown): geography, channel, prescriber, promotion. SSA/HSA/DSA meaning is unknown and unused.",
        ])),
        textbox("md_gov", 848, 188, 416, 504, para("Governance and limitations", [
            f"Opportunity score {versions['opportunity']} (fingerprint {versions['fingerprint']}): imported from the "
            "canonical Python engine; Power BI never recomputes it. Descriptive percentiles, not a forecast, "
            "probability or guarantee. Insufficient evidence is a status, never a zero score.",
            f"Scenarios {versions['scenario']}: M7 formulas on the observed baseline; outputs are CALCULATED, "
            "never OBSERVED; invalid assumptions are rejected, never corrected. Integer-percent grids (target share 0.1).",
            "Reconciliation: every measure is compared with the SQL engine (and opportunity/scenario with the "
            "canonical Python engine) by `python -m pci_powerbi.reconcile`; see docs/POWER_BI_ARCHITECTURE.md.",
            "Real IMS data is licensed: do not publish screenshots of this report; public screenshots use the "
            "synthetic dataset (M13).",
        ])),
        footer("md"),
    ]
    P.append(Page("p07_methodology", "Methodology & Data Quality", v))

    # 8. Drill-through: product ---------------------------------------------------------------------
    v = [
        textbox("pd_title", 16, 6, 700, 36, [[("Product detail", 17, INK, "Georgia", True)]]),
        back_button("pd_back", 1180, 10),
        card("pd_label", 16, 44, 1150, 36, "Selected Product Label", size=12, color=ACCENT),
        slicer("pd_anchor", 800, 82, 228, 52, ("Anchor", "Anchor"), "Anchor month", sync="anchor", default=anchor_default, search=False),
        slicer("pd_basis", 1036, 82, 228, 52, ("Basis", "Basis Name"), "Basis", sync="basis", default="MAT", search=False),
        card("pd_period", 16, 88, 776, 38, "Period Caption", size=9, color=INK2),
        kpis("pd_kpi", 16, 136, 1248, 84, [["Value", "Value (₹ cr)"], ["Value Growth %", "Growth"],
                                           ["Share of Market %", "Share of market"], ["Evolution Index", "EI"],
                                           ["Units Growth %", "Units growth"]],
             title="How is this product performing?"),
        trend("pd_trend", 16, 228, 620, 464, "How has it moved over time?", "Selected basis vs prior year"),
        table("pd_markets", 644, 228, 620, 464,
              [SG, G, ["Value", "Value (₹ cr)"], ["Value Growth %", "Growth"], ["Share of Market %", "Share of market"],
               ["Share Change pp", "Share chg"], ["Evolution Index", "EI"]],
              "In which markets does it compete?", "Share of each subgroup market",
              sort=("Value", "Descending"),
              colors=[("Value Growth %", "Growth Color"), ("Share Change pp", "Share Change Color"),
                      ("Evolution Index", "Evolution Color")]),
        footer("pd"),
    ]
    P.append(Page("p08_product_detail", "Product detail", v, drillthrough=("Product", "Product Code"), hidden=True))

    # 9. Drill-through: company ---------------------------------------------------------------------
    v = [
        textbox("cd_title", 16, 6, 700, 36, [[("Company detail", 17, INK, "Georgia", True)]]),
        back_button("cd_back", 1180, 10),
        card("cd_label", 16, 44, 1150, 36, "Selected Company Label", size=12, color=ACCENT),
        slicer("cd_anchor", 800, 82, 228, 52, ("Anchor", "Anchor"), "Anchor month", sync="anchor", default=anchor_default, search=False),
        slicer("cd_basis", 1036, 82, 228, 52, ("Basis", "Basis Name"), "Basis", sync="basis", default="MAT", search=False),
        card("cd_period", 16, 88, 776, 38, "Period Caption", size=9, color=INK2),
        kpis("cd_kpi", 16, 136, 1248, 84, [["Value", "Value (₹ cr)"], ["Value Growth %", "Growth"],
                                           ["Share of Market %", "Share of total market"], ["Evolution Index", "EI"],
                                           ["Units Growth %", "Units growth"]],
             title="How is this company performing nationally?"),
        table("cd_where", 16, 228, 620, 464,
              [["Value", "Value (₹ cr)"], ["Value Growth %", "Growth"], ["Share of Market %", "Share of market"],
               ["Evolution Index", "EI"]],
              "Where does it compete?", "Share = the company's share of each market",
              sort=("Value", "Descending"), matrix_rows=[G, SG],
              colors=[("Value Growth %", "Growth Color"), ("Evolution Index", "Evolution Color")]),
        table("cd_products", 644, 228, 620, 464,
              [("Product", "Product Code", "Code"), ("Product", "Product", "Product"), ["Value", "Value (₹ cr)"],
               ["Value Growth %", "Growth"], ["Share of Market %", "Share of total market"], ["Rank Value (Product)", "Rank"]],
              "Which products make up its portfolio?", "Top 500 products by value (visual filter)",
              sort=("Value", "Descending"), colors=[("Value Growth %", "Growth Color")]),
        footer("cd"),
    ]
    v[[x.name for x in v].index("cd_products")].filters = [topn_filter("topn_cd_products", "Product", "Product Code", "Value", 500)]
    P.append(Page("p09_company_detail", "Company detail", v, drillthrough=("Company", "Company"), hidden=True))
    return P


def report_json(theme_file: str) -> dict:
    return {"$schema": f"{S}/report/{V_REPORT}/schema.json",
            "themeCollection": {"customTheme": {"name": theme_file,
                                                "reportVersionAtImport": {"visual": V_VISUAL, "page": V_PAGE, "report": V_REPORT},
                                                "type": "RegisteredResources"}},
            "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                                  "items": [{"name": theme_file, "path": theme_file, "type": "CustomTheme"}]}],
            "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                         "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                         "useEnhancedTooltips": True}}


__all__ = ["pages", "report_json", "Page", "Visual", "F_PCT"]
