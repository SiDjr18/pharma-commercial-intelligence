"""Semantic model (TMDL) for the M12 Power BI layer.

Every measure reproduces a definition that already exists and is validated in the SQL engine
(sql/periods.sql, sql/metrics.sql) or the canonical Python engines (opportunity.py, scenario.py).
Nothing here is a new metric; tests/test_powerbi_m12.py and `python -m pci_powerbi.reconcile`
prove parity against the reference engines.

Model (star schema, import mode, same Parquet files as DuckDB):
  'Pack Month' (fact, pack x month)   *-1  Period          (calendar months, derived from the fact)
  'Pack Month'                         *-1  Pack            (pack attributes; segment columns)
  Pack                                 *-1  Product / Market / Company
  'Opportunity Product' (canonical M6 export) *-1 Product / Market / Company
  'Opportunity Market'  (canonical M6 export) *-1 Market
  Anchor, Basis, Scenario parameter tables: disconnected (selection only; never filter facts directly)
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ------------------------------------------------------------------------------------------------ formats
F_VALUE = "#,0.00"                  # Rs crore
F_UNITS = "#,0.0"                   # '000
F_PCT = r"+0.0\%;-0.0\%;0.0\%"      # growth / change in percent (number already x100)
F_SHARE = r"0.00\%"                 # share in percent (number already x100)
F_PP = "+0.00 pp;-0.00 pp;0.00 pp"   # percentage points
F_INDEX = "0.0"
F_INT = "0"
F_SCORE = "0.0"
F_PRICE = "#,0.00"                  # Rs per pack
F_DATE = "mmm yyyy"

# ------------------------------------------------------------------------------------------------ helpers
SCOPE_MARKET = "REMOVEFILTERS ( 'Product' ), REMOVEFILTERS ( 'Company' )"
SCOPE_TOTAL = "REMOVEFILTERS ( 'Pack' ), REMOVEFILTERS ( 'Product' ), REMOVEFILTERS ( 'Company' ), REMOVEFILTERS ( 'Market' )"


def _window_sum(col: str, which: str) -> str:
    """Sum of a fact column over the current or prior window of the selected anchor/basis (periods.sql)."""
    if which == "cur":
        return f"""VAR s = [Current Start]
VAR e = [Current End]
RETURN
    IF ( [Current Complete],
        CALCULATE ( SUM ( 'Pack Month'[{col}] ), REMOVEFILTERS ( 'Period' ), 'Period'[Period] >= s, 'Period'[Period] <= e ) )"""
    return f"""VAR s = [Prior Start]
VAR e = [Prior End]
RETURN
    IF ( [Current Complete] && [Prior Complete],
        CALCULATE ( SUM ( 'Pack Month'[{col}] ), REMOVEFILTERS ( 'Period' ), 'Period'[Period] >= s, 'Period'[Period] <= e ) )"""


def _growth(cur: str, prior: str) -> str:
    # metrics.sql: cur / prior * 100 - 100 only when prior > 0; otherwise NULL (never 0). BLANK > 0 is FALSE.
    return f"""VAR c = [{cur}]
VAR p = [{prior}]
RETURN IF ( p > 0, c / p * 100 - 100 )"""


def _trend(col: str, shift: int) -> str:
    """Trend on the Period axis (entity_trend): the axis month is the anchor; shift = months back (0 or 12)."""
    return f"""VAR a = EDATE ( MAX ( 'Period'[Period] ), -{shift} )
VAR b = [Basis Selected]
VAR s = SWITCH ( b, "MONTH", a, "YTD", DATE ( YEAR ( a ), 1, 1 ), "MAT", EDATE ( a, -11 ) )
VAR first = [First Data Month]
RETURN
    IF ( HASONEVALUE ( 'Period'[Period] ) && NOT ISBLANK ( s ) && s >= first,
        CALCULATE ( SUM ( 'Pack Month'[{col}] ), REMOVEFILTERS ( 'Period' ), 'Period'[Period] >= s, 'Period'[Period] <= a ) )"""


def _rank(key: str, key_measure: str, relation: str, tiebreak: str, scope_cols: tuple, extra_order: str = "") -> str:
    """Deterministic rank within the current selection (metrics.sql: row_number over the key rounded to 1e-9,
    ties broken by entity key ascending in BYTE order -> ordinal '<key> Order' column, or the digit-only
    product code). The relation spans every attribute of the entity (the whole dimension table where the
    entity is its grain) so that other grouped attributes (label, Indian/MNC, ...) are overridden per row;
    MATCHBY identifies the current entity. Found by the M12 visual-shape reconciliation."""
    inscope = " || ".join(f"ISINSCOPE ( {c} )" for c in scope_cols)
    return f"""IF ( NOT ISBLANK ( [Value] ) && ( {inscope} ),
    RANK ( SKIP, {relation},
        ORDERBY ( [{key_measure}], DESC,{extra_order} {tiebreak}, ASC ), LAST, , MATCHBY ( {key} ) ) )"""


def _topn(col: str, measure: str, n: int, direction: str, sign: str) -> str:
    cmp = "> 0" if sign == "pos" else "< 0"
    return f"""IF ( ISINSCOPE ( {col} ),
    VAR x = [{measure}]
    VAR r = RANK ( SKIP, ALLSELECTED ( {col} ), ORDERBY ( [{measure}], {direction}, {col}, ASC ), LAST )
    RETURN IF ( r <= {n} && x {cmp}, x ) )"""


def _color(measure: str) -> str:
    return f"""VAR x = [{measure}]
RETURN SWITCH ( TRUE (), ISBLANK ( x ), "#8f98a4", x > 0, "#1b6f45", x < 0, "#ad3a2c", "#384657" )"""


def _opp_field(table: str, col: str, text: bool = False) -> str:
    if text:   # CONCATENATEX turns a NULL into "": return BLANK instead (NULL must stay NULL; M12 reconciliation)
        return f"""VAR a = [Anchor Month]
VAR b = [Basis Selected]
VAR t = CALCULATETABLE ( '{table}', '{table}'[Anchor] = a, '{table}'[Basis] = b )
VAR v = IF ( COUNTROWS ( t ) = 1, CONCATENATEX ( t, '{table}'[{col}] ) )
RETURN IF ( v <> "", v )"""
    return f"""VAR a = [Anchor Month]
VAR b = [Basis Selected]
VAR t = CALCULATETABLE ( '{table}', '{table}'[Anchor] = a, '{table}'[Basis] = b )
RETURN IF ( COUNTROWS ( t ) = 1, MAXX ( t, '{table}'[{col}] ) )"""


def _opp_count(table: str, extra: str = "") -> str:
    return f"""VAR a = [Anchor Month]
VAR b = [Basis Selected]
RETURN CALCULATE ( COUNTROWS ( '{table}' ), '{table}'[Anchor] = a, '{table}'[Basis] = b{extra} )"""


# ------------------------------------------------------------------------------------------------ spec types
@dataclass
class Measure:
    name: str
    expr: str
    fmt: str | None = None
    folder: str | None = None
    hidden: bool = False
    desc: str | None = None


@dataclass
class Column:
    name: str
    dtype: str                    # string | int64 | double | dateTime | boolean
    source: str | None = None     # M column name (defaults to name) or [Name] for calculated tables
    hidden: bool = False
    fmt: str | None = None
    sort_by: str | None = None
    summarize: str = "none"
    desc: str | None = None
    mdx: bool = True              # False: no attribute hierarchy (aggregate-only numeric columns; saves memory)


@dataclass
class Table:
    name: str
    columns: list
    kind: str                     # "m" | "calculated"
    source: str
    hidden: bool = False
    desc: str | None = None
    measures: list = field(default_factory=list)
    hierarchies: list = field(default_factory=list)   # (name, [column names])


# ------------------------------------------------------------------------------------------------ M queries
def _parquet(file: str, folder: str = "ProcessedFolder") -> str:
    return f'Parquet.Document ( File.Contents ( {folder} & "\\{file}" ) )'


def _ordinal(prev: str, col: str, step: str) -> str:
    """M steps adding '<col> Order' = position of the key in ORDINAL (byte-order) sort. SQL breaks rank ties
    by entity key in byte order; DAX text ordering is culture-aware (e.g. it ignores hyphens), so ranks
    tie-break on this integer instead of the text (found by the M12 reconciliation)."""
    k, m = step + "Keys", step + "Map"
    return (f'    {k} = List.Sort ( List.Distinct ( Table.Column ( {prev}, "{col}" ) ), Comparer.Ordinal ),\n'
            f'    {m} = Record.FromList ( List.Positions ( {k} ), {k} ),\n'
            f'    {step} = Table.AddColumn ( {prev}, "{col} Order", each Record.Field ( {m}, [{col}] ), Int64.Type ),\n')


M_FACT = f"""let
    Source = {_parquet("fact_pack_month.parquet")},
    Kept = Table.SelectColumns ( Source, {{"pfc", "period", "value_cr", "units_k", "qty_k"}} ),
    Typed = Table.TransformColumnTypes ( Kept, {{{{"pfc", Int64.Type}}, {{"period", type date}}, {{"value_cr", type number}}, {{"units_k", type number}}, {{"qty_k", type number}}}} )
in
    Typed"""

M_PACK = f"""let
    Source = {_parquet("pack.parquet")},
    Kept = Table.SelectColumns ( Source, {{"pfc", "prod_code", "company", "subgroup", "index_desc", "molecule_desc", "plain_combination", "molecule_count", "nfc1", "form_short_desc"}} ),
    Unclassified = Table.TransformColumns ( Kept, {{
        {{"molecule_desc", each if _ = null then "(UNCLASSIFIED)" else _, type text}},
        {{"plain_combination", each if _ = null then "(UNCLASSIFIED)" else _, type text}},
        {{"molecule_count", each if _ = null then "(UNCLASSIFIED)" else Text.From ( _ ), type text}} }} ),
    Renamed = Table.RenameColumns ( Unclassified, {{{{"molecule_desc", "Molecule"}}, {{"plain_combination", "Plain/Combination"}}, {{"molecule_count", "Molecule Count"}}, {{"nfc1", "Form (NFC1)"}}, {{"form_short_desc", "Dosage Form"}}}} ),
{_ordinal("Renamed", "Molecule", "WithMol")}    Typed = Table.TransformColumnTypes ( WithMol, {{{{"pfc", Int64.Type}}, {{"prod_code", Int64.Type}}, {{"company", type text}}, {{"subgroup", type text}}, {{"index_desc", type text}}, {{"Form (NFC1)", type text}}, {{"Dosage Form", type text}}}} )
in
    Typed"""

M_PRODUCT = f"""let
    Source = {_parquet("pack.parquet")},
    Kept = Table.SelectColumns ( Source, {{"prod_code", "brand", "company", "prod_launch_month"}} ),
    Grouped = Table.Group ( Kept, {{"prod_code"}}, {{
        {{"Brand", each List.Min ( [brand] ), type text}},
        {{"Company Label", each List.Min ( [company] ), type text}},
        {{"Launch Month", each List.Min ( [prod_launch_month] ), type nullable date}} }} ),
    WithCode = Table.AddColumn ( Grouped, "Product Code", each Text.From ( [prod_code] ), type text ),
    WithLabel = Table.AddColumn ( WithCode, "Product", each [Brand] & " (" & [Company Label] & ") · " & [Product Code], type text ),
    Typed = Table.TransformColumnTypes ( WithLabel, {{{{"prod_code", Int64.Type}}}} )
in
    Typed"""

M_MARKET = f"""let
    Source = {_parquet("pack.parquet")},
    Kept = Table.SelectColumns ( Source, {{"subgroup", "therapy_group", "supergroup", "acute_chronic"}} ),
    Grouped = Table.Group ( Kept, {{"subgroup"}}, {{
        {{"Therapy Group", each List.Min ( [therapy_group] ), type text}},
        {{"Supergroup", each List.Min ( [supergroup] ), type text}},
        {{"Acute/Chronic", each List.Min ( [acute_chronic] ), type text}} }} ),
    Renamed = Table.RenameColumns ( Grouped, {{{{"subgroup", "Subgroup"}}}} ),
{_ordinal("Renamed", "Subgroup", "WithSub")}{_ordinal("WithSub", "Supergroup", "WithSg")}{_ordinal("WithSg", "Therapy Group", "WithTg")}    Done = WithTg
in
    Done"""

M_COMPANY = f"""let
    Source = {_parquet("pack.parquet")},
    Kept = Table.SelectColumns ( Source, {{"company", "indian_mnc"}} ),
    Grouped = Table.Group ( Kept, {{"company"}}, {{{{"Indian/MNC", each List.Min ( [indian_mnc] ), type text}}}} ),
    Renamed = Table.RenameColumns ( Grouped, {{{{"company", "Company"}}}} ),
{_ordinal("Renamed", "Company", "WithCo")}    Done = WithCo
in
    Done"""

OPP_PRODUCT_TYPES = [("Anchor", "type date"), ("Basis", "type text"), ("Entity Key", "type text"),
                     ("Product in Market", "type text"), ("prod_code", "Int64.Type"), ("Product Code", "type text"),
                     ("Subgroup", "type text"), ("Company", "type text"),
                     ("Score Status", "type text"), ("Insufficient Reason", "type text"), ("Evidence", "type text"),
                     ("Score", "type number"), ("Opportunity Rank", "Int64.Type"),
                     ("Market Growth %", "type number"), ("Evolution Index", "type number"),
                     ("Share in Market %", "type number"), ("Market Growth Normalized", "type number"),
                     ("Momentum Normalized", "type number"),
                     ("Matrix Quadrant", "type text"),
                     ("Momentum Side", "type text"), ("Market Growth Side", "type text"),
                     ("Methodology Version", "type text")]
OPP_MARKET_TYPES = [("Anchor", "type date"), ("Basis", "type text"), ("Subgroup", "type text"),
                    ("Score Status", "type text"), ("Insufficient Reason", "type text"), ("Evidence", "type text"),
                    ("Score", "type number"), ("Opportunity Rank", "Int64.Type"),
                    ("Market Growth %", "type number"), ("Market Size Share %", "type number"),
                    ("Growth Normalized", "type number"), ("Size Normalized", "type number"),
                    ("Value Cur", "type number"), ("Value Prior", "type number"), ("Methodology Version", "type text")]


def _m_typed(file: str, types: list) -> str:
    t = ", ".join(f'{{"{n}", {ty}}}' for n, ty in types)
    return f"""let
    Source = {_parquet(file, "PowerBIFolder")},
    Typed = Table.TransformColumnTypes ( Source, {{{t}}} )
in
    Typed"""


_DT = {"type text": "string", "type number": "double", "Int64.Type": "int64", "type date": "dateTime"}


def _opp_columns(types, fmt_map, hidden=()):
    cols = []
    for n, ty in types:
        cols.append(Column(n, _DT[ty], n, hidden=n in hidden, fmt=fmt_map.get(n),
                           summarize="none", mdx=_DT[ty] != "double"))
    return cols


# ------------------------------------------------------------------------------------------------ measures
def _measures() -> list[Measure]:
    M = Measure
    ms: list[Measure] = []
    P, C, S, N, T, R, D, O, X = ("0 Period", "1 Core", "2 Share & contribution (market scope)",
                                 "3 Share & contribution (national)", "4 Trend", "5 Rank", "6 Display",
                                 "7 Opportunity (M6, canonical export)", "8 Scenario (M7 formulas)")
    # ---- period logic (sql/periods.sql)
    ms += [
        M("Anchor Month", """VAR n = COUNTROWS ( VALUES ( 'Anchor'[Anchor Month] ) )
RETURN
    SWITCH ( TRUE (),
        n = 1, SELECTEDVALUE ( 'Anchor'[Anchor Month] ),
        NOT ISFILTERED ( 'Anchor' ), CALCULATE ( MAX ( 'Anchor'[Anchor Month] ), REMOVEFILTERS ( 'Anchor' ) ),
        BLANK () )""", F_DATE, P, desc="Selected anchor month (single selection). Default: latest data month. Several selected: BLANK (never silently picked)."),
        M("Basis Selected", """VAR n = COUNTROWS ( VALUES ( 'Basis'[Basis] ) )
RETURN
    SWITCH ( TRUE (),
        n = 1, SELECTEDVALUE ( 'Basis'[Basis] ),
        NOT ISFILTERED ( 'Basis' ), "MAT",
        BLANK () )""", None, P, desc="MONTH | YTD (calendar, Jan..anchor) | MAT (12 months ending at anchor). Default MAT, as in the application."),
        M("Basis Label", """SWITCH ( [Basis Selected], "MONTH", "Month", "YTD", "Calendar YTD", "MAT", "MAT (moving annual total)" )""", None, P),
        M("First Data Month", "CALCULATE ( MIN ( 'Period'[Period] ), REMOVEFILTERS ( 'Period' ) )", F_DATE, P),
        M("Last Data Month", "CALCULATE ( MAX ( 'Period'[Period] ), REMOVEFILTERS ( 'Period' ) )", F_DATE, P),
        M("Current Start", """VAR a = [Anchor Month]
VAR b = [Basis Selected]
RETURN
    IF ( NOT ISBLANK ( a ),
        SWITCH ( b, "MONTH", a, "YTD", DATE ( YEAR ( a ), 1, 1 ), "MAT", EDATE ( a, -11 ) ) )""", F_DATE, P),
        M("Current End", "IF ( NOT ISBLANK ( [Current Start] ), [Anchor Month] )", F_DATE, P),
        M("Prior Start", "VAR s = [Current Start] RETURN IF ( NOT ISBLANK ( s ), EDATE ( s, -12 ) )", F_DATE, P),
        M("Prior End", "VAR e = [Current End] RETURN IF ( NOT ISBLANK ( e ), EDATE ( e, -12 ) )", F_DATE, P),
        M("Current Complete", "VAR s = [Current Start] RETURN NOT ISBLANK ( s ) && s >= [First Data Month]", None, P,
          desc="Window complete only if every month exists (periods.sql current_complete)."),
        M("Prior Complete", "VAR s = [Prior Start] RETURN NOT ISBLANK ( s ) && s >= [First Data Month]", None, P),
        M("Window Months", "IF ( [Current Complete], DATEDIFF ( [Current Start], [Current End], MONTH ) + 1 )", F_INT, P),
        M("Period Caption", """VAR a = [Anchor Month]
VAR b = [Basis Selected]
RETURN
    SWITCH ( TRUE (),
        ISBLANK ( a ) || ISBLANK ( b ), "Select one anchor month and one basis",
        NOT [Current Complete], [Basis Label] & " ending " & FORMAT ( a, "mmm yyyy" ) & " starts before the first data month (" & FORMAT ( [First Data Month], "mmm yyyy" ) & "): no values",
        [Basis Label] & " · " & FORMAT ( [Current Start], "mmm yyyy" ) & " – " & FORMAT ( [Current End], "mmm yyyy" )
            & IF ( [Prior Complete],
                " vs " & FORMAT ( [Prior Start], "mmm yyyy" ) & " – " & FORMAT ( [Prior End], "mmm yyyy" ),
                " · comparison window predates the data: growth not available" ) )""", None, P),
    ]
    # ---- core entity metrics (metrics.sql entity_period)
    ms += [
        M("Value", _window_sum("value_cr", "cur"), F_VALUE, C, desc="Value, Rs crore, current window (confirmed unit)."),
        M("Value Prior", _window_sum("value_cr", "prior"), F_VALUE, C, desc="Same window shifted back 12 months; BLANK when it predates the data."),
        M("Value Change", "VAR c = [Value] VAR p = [Value Prior] RETURN IF ( NOT ISBLANK ( c ) && NOT ISBLANK ( p ), c - p )", F_VALUE, C),
        M("Value Growth %", _growth("Value", "Value Prior"), F_PCT, C, desc="cur / prior x 100 - 100 when prior > 0; otherwise BLANK (never 0)."),
        M("Growth Status", """VAR c = [Value]
VAR p = [Value Prior]
RETURN
    IF ( NOT ISBLANK ( c ),
        SWITCH ( TRUE (),
            ISBLANK ( p ), "prior_unavailable",
            p = 0 && c = 0, "no_sales",
            p = 0, "prior_zero",
            "ok" ) )""", None, C),
        M("Growth Evidence", """SWITCH ( [Growth Status],
    "ok", "Measured",
    "prior_zero", "n/a: no prior-period sales",
    "no_sales", "n/a: no sales in either window",
    "prior_unavailable", "n/a: comparison window predates the data" )""", None, C),
        M("Units", _window_sum("units_k", "cur"), F_UNITS, C, desc="'000 packs (inferred scale)."),
        M("Units Prior", _window_sum("units_k", "prior"), F_UNITS, C),
        M("Units Change", "VAR c = [Units] VAR p = [Units Prior] RETURN IF ( NOT ISBLANK ( c ) && NOT ISBLANK ( p ), c - p )", F_UNITS, C),
        M("Units Growth %", _growth("Units", "Units Prior"), F_PCT, C),
        M("Qty", _window_sum("qty_k", "cur"), F_UNITS, C, desc="'000 counting units (inferred scale)."),
        M("Qty Prior", _window_sum("qty_k", "prior"), F_UNITS, C),
        M("Qty Growth %", _growth("Qty", "Qty Prior"), F_PCT, C),
        M("Packs", "IF ( NOT ISBLANK ( [Value] ), COUNTROWS ( 'Pack' ) )", "#,0", C),
    ]
    # ---- raw scope totals (hidden). The ratio measures below use these: the entity filter is removed, so the
    #      engine evaluates each once per query instead of once per row (M12 performance finding).
    ms += [
        M("Scope Market Value", f"CALCULATE ( [Value], {SCOPE_MARKET} )", F_VALUE, S, hidden=True),
        M("Scope Market Value Prior", f"CALCULATE ( [Value Prior], {SCOPE_MARKET} )", F_VALUE, S, hidden=True),
        M("Scope Market Units", f"CALCULATE ( [Units], {SCOPE_MARKET} )", F_UNITS, S, hidden=True),
        M("Scope National Value", f"CALCULATE ( [Value], {SCOPE_TOTAL} )", F_VALUE, N, hidden=True),
        M("Scope National Value Prior", f"CALCULATE ( [Value Prior], {SCOPE_TOTAL} )", F_VALUE, N, hidden=True),
        M("Scope National Units", f"CALCULATE ( [Units], {SCOPE_TOTAL} )", F_UNITS, N, hidden=True),
        M("Scope Therapy Area Value", """IF ( HASONEVALUE ( 'Market'[Supergroup] ),
    CALCULATE ( [Value], REMOVEFILTERS ( 'Market' ), VALUES ( 'Market'[Supergroup] ) ) )""", F_VALUE, N, hidden=True),
        M("Scope Therapy Area Value Prior", """IF ( HASONEVALUE ( 'Market'[Supergroup] ),
    CALCULATE ( [Value Prior], REMOVEFILTERS ( 'Market' ), VALUES ( 'Market'[Supergroup] ) ) )""", F_VALUE, N, hidden=True),
    ]
    # ---- market scope: product/company within the markets selected (Market / Pack filters kept)
    ms += [
        M("Market Value", "IF ( NOT ISBLANK ( [Value] ), [Scope Market Value] )", F_VALUE, S,
          desc="Scope total: current filters minus Product and Company (the market the entity is measured in)."),
        M("Market Value Prior", "IF ( NOT ISBLANK ( [Value] ), [Scope Market Value Prior] )", F_VALUE, S),
        M("Market Units", "IF ( NOT ISBLANK ( [Value] ), [Scope Market Units] )", F_UNITS, S),
        M("Market Growth %", "IF ( NOT ISBLANK ( [Value] ), VAR c = [Scope Market Value] VAR p = [Scope Market Value Prior] RETURN IF ( p > 0, c / p * 100 - 100 ) )", F_PCT, S),
        M("Share of Market %", "VAR c = [Value] VAR m = [Scope Market Value] RETURN IF ( NOT ISBLANK ( c ) && m > 0, c / m * 100 )", F_SHARE, S),
        M("Share of Market Prior %", "VAR p = [Value Prior] VAR m = [Scope Market Value Prior] RETURN IF ( NOT ISBLANK ( p ) && m > 0, p / m * 100 )", F_SHARE, S),
        M("Share Change pp", """VAR c = [Value]
VAR p = [Value Prior]
VAR m = [Scope Market Value]
VAR mp = [Scope Market Value Prior]
RETURN IF ( NOT ISBLANK ( c ) && NOT ISBLANK ( p ) && m > 0 && mp > 0, c / m * 100 - p / mp * 100 )""", F_PP, S),
        M("Units Share of Market %", "VAR u = [Units] VAR m = [Scope Market Units] RETURN IF ( NOT ISBLANK ( u ) && m > 0, u / m * 100 )", F_SHARE, S),
        M("Contribution to Market Growth pp", """VAR c = [Value]
VAR p = [Value Prior]
VAR mp = [Scope Market Value Prior]
RETURN IF ( NOT ISBLANK ( c ) && NOT ISBLANK ( p ) && mp > 0, ( c - p ) / mp * 100 )""", F_PP, S,
          desc="(cur - prior) / scope prior x 100; sums to scope growth."),
        M("Evolution Index", """VAR c = [Value]
VAR p = [Value Prior]
VAR m = [Scope Market Value]
VAR mp = [Scope Market Value Prior]
RETURN IF ( p > 0 && m > 0 && mp > 0, ( c / m ) / ( p / mp ) * 100 )""", F_INDEX, S,
          desc="share_cur / share_prior x 100 = 100 x (1 + g_entity) / (1 + g_market). 100 = kept pace with its market."),
    ]
    # ---- national scope: markets, segments and headline (all dimension filters removed)
    ms += [
        M("National Value", "IF ( NOT ISBLANK ( [Value] ), [Scope National Value] )", F_VALUE, N),
        M("National Value Prior", "IF ( NOT ISBLANK ( [Value] ), [Scope National Value Prior] )", F_VALUE, N),
        M("National Units", "IF ( NOT ISBLANK ( [Value] ), [Scope National Units] )", F_UNITS, N),
        M("National Growth %", "VAR c = [Scope National Value] VAR p = [Scope National Value Prior] RETURN IF ( p > 0, c / p * 100 - 100 )", F_PCT, N),
        M("Share of Total %", "VAR c = [Value] VAR m = [Scope National Value] RETURN IF ( NOT ISBLANK ( c ) && m > 0, c / m * 100 )", F_SHARE, N),
        M("Share of Total Prior %", "VAR p = [Value Prior] VAR m = [Scope National Value Prior] RETURN IF ( NOT ISBLANK ( p ) && m > 0, p / m * 100 )", F_SHARE, N),
        M("Share of Total Change pp", """VAR c = [Value]
VAR p = [Value Prior]
VAR m = [Scope National Value]
VAR mp = [Scope National Value Prior]
RETURN IF ( NOT ISBLANK ( c ) && NOT ISBLANK ( p ) && m > 0 && mp > 0, c / m * 100 - p / mp * 100 )""", F_PP, N),
        M("Units Share of Total %", "VAR u = [Units] VAR m = [Scope National Units] RETURN IF ( NOT ISBLANK ( u ) && m > 0, u / m * 100 )", F_SHARE, N),
        M("Contribution to Total Growth pp", """VAR c = [Value]
VAR p = [Value Prior]
VAR mp = [Scope National Value Prior]
RETURN IF ( NOT ISBLANK ( c ) && NOT ISBLANK ( p ) && mp > 0, ( c - p ) / mp * 100 )""", F_PP, N),
        M("Evolution Index vs Total", """VAR c = [Value]
VAR p = [Value Prior]
VAR m = [Scope National Value]
VAR mp = [Scope National Value Prior]
RETURN IF ( p > 0 && m > 0 && mp > 0, ( c / m ) / ( p / mp ) * 100 )""", F_INDEX, N),
        M("Therapy Area Value", "IF ( NOT ISBLANK ( [Value] ), [Scope Therapy Area Value] )", F_VALUE, N,
          desc="Value of the (single) supergroup in context: denominator for subgroup share within its therapy area."),
        M("Therapy Area Value Prior", "IF ( NOT ISBLANK ( [Value] ), [Scope Therapy Area Value Prior] )", F_VALUE, N),
        M("Share of Therapy Area %", "VAR c = [Value] VAR m = [Scope Therapy Area Value] RETURN IF ( NOT ISBLANK ( c ) && m > 0, c / m * 100 )", F_SHARE, N),
        M("Contribution to Therapy Area Growth pp", """VAR c = [Value]
VAR p = [Value Prior]
VAR mp = [Scope Therapy Area Value Prior]
RETURN IF ( NOT ISBLANK ( c ) && NOT ISBLANK ( p ) && mp > 0, ( c - p ) / mp * 100 )""", F_PP, N),
    ]
    # ---- trend (entity_trend): Period axis month = anchor
    ms += [
        M("Trend Value", _trend("value_cr", 0), F_VALUE, T, desc="Value for the selected basis ending at the axis month."),
        M("Trend Value Prior Year", _trend("value_cr", 12), F_VALUE, T, desc="Same basis, window ending 12 months earlier."),
        M("Trend Growth %", _growth("Trend Value", "Trend Value Prior Year"), F_PCT, T),
        M("Trend Units", _trend("units_k", 0), F_UNITS, T),
        M("Trend Units Prior Year", _trend("units_k", 12), F_UNITS, T),
    ]
    # ---- ranks (deterministic; within the current selection)
    ms += [
        M("Rank Key Value", "VAR v = [Value] RETURN IF ( NOT ISBLANK ( v ), ROUND ( v, 9 ) )", None, R, hidden=True),
        M("Rank Key Growth", "VAR c = [Value] VAR p = [Value Prior] RETURN IF ( p > 0, ROUND ( c / p, 9 ) )", None, R, hidden=True),
    ]
    RANK_SPECS = [
        ("Product", "'Product'[Product Code]", "ALLSELECTED ( 'Product' )", "'Product'[Product Code]",
         ("'Product'[Product Code]", "'Product'[Product]")),
        ("Company", "'Company'[Company]", "ALLSELECTED ( 'Company' )", "'Company'[Company Order]", ("'Company'[Company]",)),
        ("Subgroup", "'Market'[Subgroup]", "ALLSELECTED ( 'Market' )", "'Market'[Subgroup Order]", ("'Market'[Subgroup]",)),
        ("Supergroup", "'Market'[Supergroup]", "ALLSELECTED ( 'Market'[Supergroup], 'Market'[Supergroup Order] )",
         "'Market'[Supergroup Order]", ("'Market'[Supergroup]",)),
        ("Therapy Group", "'Market'[Therapy Group]", "ALLSELECTED ( 'Market'[Therapy Group], 'Market'[Therapy Group Order] )",
         "'Market'[Therapy Group Order]", ("'Market'[Therapy Group]",)),
        ("Molecule", "'Pack'[Molecule]", "ALLSELECTED ( 'Pack'[Molecule], 'Pack'[Molecule Order] )",
         "'Pack'[Molecule Order]", ("'Pack'[Molecule]",)),
    ]
    for label, key, rel, tb, scope in RANK_SPECS:
        ms.append(M(f"Rank Value ({label})", _rank(key, "Rank Key Value", rel, tb, scope), F_INT, R))
        ms.append(M(f"Rank Growth ({label})", _rank(key, "Rank Key Growth", rel, tb, scope, " [Rank Key Value], DESC,"), F_INT, R))
    # ---- display helpers (selection / formatting only; values come from the measures above)
    ms += [
        M("Subgroup Gain (Top 10)", _topn("'Market'[Subgroup]", "Value Change", 10, "DESC", "pos"), F_VALUE, D),
        M("Subgroup Decline (Top 10)", _topn("'Market'[Subgroup]", "Value Change", 10, "ASC", "neg"), F_VALUE, D),
        M("Subgroup Contribution (Top 12)", """IF ( ISINSCOPE ( 'Market'[Subgroup] ),
    VAR x = [Contribution to Total Growth pp]
    VAR r = RANK ( SKIP, ALLSELECTED ( 'Market'[Subgroup] ), ORDERBY ( ABS ( [Contribution to Total Growth pp] ), DESC, 'Market'[Subgroup], ASC ), LAST )
    RETURN IF ( r <= 12, x ) )""", F_PP, D),
        M("Company Share Mover (Top 8 each way)", """IF ( ISINSCOPE ( 'Company'[Company] ),
    VAR x = [Share Change pp]
    VAR up = RANK ( SKIP, ALLSELECTED ( 'Company'[Company] ), ORDERBY ( [Share Change pp], DESC, 'Company'[Company], ASC ), LAST )
    VAR dn = RANK ( SKIP, ALLSELECTED ( 'Company'[Company] ), ORDERBY ( [Share Change pp], ASC, 'Company'[Company], ASC ), LAST )
    RETURN IF ( ( up <= 8 && x > 0 ) || ( dn <= 8 && x < 0 ), x ) )""", F_PP, D),
        M("Product Value (Top 15)", "VAR r = [Rank Value (Product)] RETURN IF ( r <= 15, [Value] )", F_VALUE, D),
        M("Subgroup Value (Top 15)", "VAR r = [Rank Value (Subgroup)] RETURN IF ( r <= 15, [Value] )", F_VALUE, D),
        M("Growth Color", _color("Value Growth %"), None, D, hidden=True),
        M("Value Change Color", _color("Value Change"), None, D, hidden=True),
        M("Share Change Color", _color("Share Change pp"), None, D, hidden=True),
        M("Total Share Change Color", _color("Share of Total Change pp"), None, D, hidden=True),
        M("Contribution Color", _color("Contribution to Total Growth pp"), None, D, hidden=True),
        M("Market Contribution Color", _color("Contribution to Market Growth pp"), None, D, hidden=True),
        M("Subgroup Contribution Color", _color("Subgroup Contribution (Top 12)"), None, D, hidden=True),
        M("Company Mover Color", _color("Company Share Mover (Top 8 each way)"), None, D, hidden=True),
        M("Evolution Color", """VAR x = [Evolution Index]
RETURN SWITCH ( TRUE (), ISBLANK ( x ), "#8f98a4", x >= 100, "#1b6f45", "#ad3a2c" )""", None, D, hidden=True),
        M("Selected Product Label", """IF ( HASONEVALUE ( 'Product'[Product Code] ), SELECTEDVALUE ( 'Product'[Product] ), "Select one product (brand names are not unique; identity = product code)" )""", None, D),
        M("Selected Company Label", """IF ( HASONEVALUE ( 'Company'[Company] ), SELECTEDVALUE ( 'Company'[Company] ), "Select one company" )""", None, D),
        M("Products (count)", "CALCULATE ( DISTINCTCOUNT ( 'Pack'[prod_code] ) )", "#,0", D),
        M("Companies (count)", "CALCULATE ( DISTINCTCOUNT ( 'Pack'[company] ) )", "#,0", D),
        M("Subgroups (count)", "CALCULATE ( DISTINCTCOUNT ( 'Pack'[subgroup] ) )", "#,0", D),
        M("Packs (all)", "COUNTROWS ( 'Pack' )", "#,0", D),
    ]
    # ---- opportunity (canonical M6 scores, imported; never recomputed in DAX)
    OP, OM = "Opportunity Product", "Opportunity Market"
    ms += [
        M("Opportunity Availability", f"""VAR b = [Basis Selected]
VAR a = [Anchor Month]
VAR n = CALCULATE ( COUNTROWS ( '{OM}' ), '{OM}'[Anchor] = a, '{OM}'[Basis] = b, REMOVEFILTERS ( 'Market' ) )
RETURN
    SWITCH ( TRUE (),
        ISBLANK ( a ) || ISBLANK ( b ), "Select one anchor month and one basis",
        NOT ( b IN {{ "MAT", "YTD" }} ), "Opportunity scoring uses MAT or calendar YTD only; MONTH is excluded as too noisy (OPP-1.0.0).",
        n = 0, "Insufficient history: the comparison window predates the data, so every entity is INSUFFICIENT_EVIDENCE (insufficient_history).",
        "DESCRIPTIVE OPPORTUNITY SCORING (OPP-1.0.0) — relative percentile evidence within scored entities nationally. Not a forecast, probability or guarantee." )""", None, O),
        M("Market Opportunity Score", _opp_field(OM, "Score"), F_SCORE, O),
        M("Market Opportunity Rank", _opp_field(OM, "Opportunity Rank"), F_INT, O),
        M("Market Evidence", _opp_field(OM, "Evidence", True), None, O),
        M("Market Opp Growth %", _opp_field(OM, "Market Growth %"), F_PCT, O, desc="Raw component: subgroup value growth (validated metric)."),
        M("Market Opp Size Share %", _opp_field(OM, "Market Size Share %"), F_SHARE, O),
        M("Market Growth Percentile", _opp_field(OM, "Growth Normalized"), "0.000", O),
        M("Market Size Percentile", _opp_field(OM, "Size Normalized"), "0.000", O),
        M("Product Opportunity Score", _opp_field(OP, "Score"), F_SCORE, O),
        M("Product Opportunity Rank", _opp_field(OP, "Opportunity Rank"), F_INT, O),
        M("Product Evidence", _opp_field(OP, "Evidence", True), None, O),
        M("Product Opp Market Growth %", _opp_field(OP, "Market Growth %"), F_PCT, O),
        M("Product Opp Evolution Index", _opp_field(OP, "Evolution Index"), F_INDEX, O),
        M("Product Opp Share in Market %", _opp_field(OP, "Share in Market %"), F_SHARE, O),
        M("Product Opp Quadrant", _opp_field(OP, "Matrix Quadrant", True), None, O),
        M("Momentum Percentile", _opp_field(OP, "Momentum Normalized"), "0.000", O, desc="Normalized component (mid-rank percentile, 0..1)."),
        M("Market Growth Percentile (Product)", _opp_field(OP, "Market Growth Normalized"), "0.000", O),
        M("Products in Population", _opp_count(OP), "#,0", O),
        M("Scored Products", _opp_count(OP, f", '{OP}'[Score Status] = \"SCORED\""), "#,0", O),
        M("Insufficient-Evidence Products", _opp_count(OP, f", '{OP}'[Score Status] = \"INSUFFICIENT_EVIDENCE\""), "#,0", O),
        M("Markets Scored", _opp_count(OM, f", '{OM}'[Score Status] = \"SCORED\""), "#,0", O),
        M("Scored Products Gaining Share", _opp_count(OP, f", '{OP}'[Score Status] = \"SCORED\", '{OP}'[Evolution Index] >= 100"), "#,0", O),
        M("Top Opportunity Market", f"""VAR a = [Anchor Month]
VAR b = [Basis Selected]
RETURN CALCULATE ( SELECTEDVALUE ( '{OM}'[Subgroup] ), '{OM}'[Anchor] = a, '{OM}'[Basis] = b, '{OM}'[Opportunity Rank] = 1, REMOVEFILTERS ( 'Market' ) )""", None, O),
    ]
    ms += _scenario_measures(X)
    return ms


def _scenario_measures(X: str) -> list[Measure]:
    """M7 (SCN-1.0.0) formulas on the selected entity's observed baseline. Assumptions come only from the
    parameter tables (allowed grids); anything missing, extra or ambiguous is REJECTED, never corrected."""
    M = Measure
    seg_cols = ["'Market'[Acute/Chronic]", "'Company'[Indian/MNC]", "'Pack'[Plain/Combination]",
                "'Pack'[Molecule Count]", "'Pack'[Dosage Form]", "'Pack'[Form (NFC1)]"]
    return [
        M("Scenario Type Selected", "IF ( HASONEVALUE ( 'Scenario Type'[Scenario Type] ), VALUES ( 'Scenario Type'[Scenario Type] ) )", None, X),
        M("Price Change Assumption", "IF ( HASONEVALUE ( 'Price Change'[Price Change %] ), VALUES ( 'Price Change'[Price Change %] ) )", F_PCT, X),
        M("Volume Change Assumption", "IF ( HASONEVALUE ( 'Volume Change'[Volume Change %] ), VALUES ( 'Volume Change'[Volume Change %] ) )", F_PCT, X),
        M("Market Growth Assumption", "IF ( HASONEVALUE ( 'Market Growth Assumption'[Market Growth %] ), VALUES ( 'Market Growth Assumption'[Market Growth %] ) )", F_PCT, X),
        M("Target Share Assumption", "IF ( HASONEVALUE ( 'Target Share'[Target Share %] ), VALUES ( 'Target Share'[Target Share %] ) )", F_SHARE, X),
        M("Scenario Market Level", """VAR f1 = ISFILTERED ( 'Market'[Supergroup] )
VAR f2 = ISFILTERED ( 'Market'[Therapy Group] )
VAR f3 = ISFILTERED ( 'Market'[Subgroup] )
VAR f4 = ISFILTERED ( 'Pack'[Molecule] )
RETURN
    SWITCH ( TRUE (),
        f4 && ( f1 || f2 || f3 ), "invalid",
        f4, "molecule",
        f3, "subgroup",
        f1 && f2, "invalid",
        f2, "therapy_group",
        f1, "supergroup",
        "total" )""", None, X, hidden=True,
          desc="The most specific market filter defines the market (subgroup filters combined with their own supergroup/group are consistent). Molecule is a separate lens and cannot be combined."),
        M("Scenario Market Key", """SWITCH ( [Scenario Market Level],
    "total", "TOTAL",
    "subgroup", SELECTEDVALUE ( 'Market'[Subgroup] ),
    "therapy_group", SELECTEDVALUE ( 'Market'[Therapy Group] ),
    "supergroup", SELECTEDVALUE ( 'Market'[Supergroup] ),
    "molecule", SELECTEDVALUE ( 'Pack'[Molecule] ) )""", None, X, hidden=True),
        M("Scenario Entity Type", """VAR np = IF ( ISFILTERED ( 'Product' ), COUNTROWS ( VALUES ( 'Product'[Product Code] ) ), 0 )
VAR nc = IF ( ISFILTERED ( 'Company' ), COUNTROWS ( VALUES ( 'Company'[Company] ) ), 0 )
RETURN
    SWITCH ( TRUE (),
        np > 1, "invalid:select exactly one product",
        np = 1, "product",
        nc > 1, "invalid:select exactly one company",
        nc = 1, "company",
        [Scenario Market Level] )""", None, X, hidden=True),
        M("Scenario Status", f"""VAR st = [Scenario Type Selected]
VAR et = [Scenario Entity Type]
VAR ml = [Scenario Market Level]
VAR seg = {" || ".join(f"ISFILTERED ( {c} )" for c in seg_cols)}
VAR isEntity = et IN {{ "product", "company" }}
VAR usesPrice = st IN {{ "PRICE_CHANGE", "PRICE_VOLUME_CHANGE" }}
VAR usesVolume = st IN {{ "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE" }}
VAR pSel = ISFILTERED ( 'Price Change' )
VAR vSel = ISFILTERED ( 'Volume Change' )
VAR gSel = ISFILTERED ( 'Market Growth Assumption' )
VAR tSel = ISFILTERED ( 'Target Share' )
RETURN
    SWITCH ( TRUE (),
        ISBLANK ( st ), "SELECT: choose one scenario type",
        ISBLANK ( [Anchor Month] ) || ISBLANK ( [Basis Selected] ), "SELECT: choose one anchor month and one basis",
        NOT [Current Complete], "PERIOD UNAVAILABLE: the window starts before the first data month",
        LEFT ( et, 8 ) = "invalid:", "SELECT: " & MID ( et, 9, 200 ),
        ml = "invalid", "SELECT: use one market level (molecule cannot be combined; therapy group and supergroup together are not a defined market)",
        ml <> "total" && ISBLANK ( [Scenario Market Key] ), "SELECT: choose exactly one market",
        seg, "SELECT: remove segment filters; a scenario entity is one product, company or market",
        st = "MARKET_GROWTH" && isEntity, "INVALID PARAMETER: MARKET_GROWTH applies to market entities (total, supergroup, therapy group, subgroup, molecule)",
        st = "MARKET_SHARE" && NOT isEntity, "INVALID PARAMETER: MARKET_SHARE applies to one product or company within a market",
        NOT usesPrice && pSel, "INVALID ASSUMPTION: price_change_pct is not an assumption of " & st & " (clear it)",
        NOT usesVolume && vSel, "INVALID ASSUMPTION: volume_change_pct is not an assumption of " & st & " (clear it)",
        NOT ( st IN {{ "MARKET_GROWTH", "MARKET_SHARE" }} ) && gSel, "INVALID ASSUMPTION: market_growth_pct is not an assumption of " & st & " (clear it)",
        st <> "MARKET_SHARE" && tSel, "INVALID ASSUMPTION: target_share_pct is not an assumption of " & st & " (clear it)",
        usesPrice && ISBLANK ( [Price Change Assumption] ), "SELECT: choose one price change % (allowed > -100 and <= 1000)",
        usesVolume && ISBLANK ( [Volume Change Assumption] ), "SELECT: choose one volume change % (allowed -100 to 1000)",
        st = "MARKET_GROWTH" && ISBLANK ( [Market Growth Assumption] ), "SELECT: choose one market growth % (allowed -100 to 1000)",
        st = "MARKET_SHARE" && ISBLANK ( [Target Share Assumption] ), "SELECT: choose one target share % (allowed 0 to 100)",
        st = "MARKET_SHARE" && gSel && ISBLANK ( [Market Growth Assumption] ), "SELECT: choose one market growth % or clear it (default 0)",
        ISBLANK ( [Value] ), "NOT FOUND: the entity has no packs in the selected market",
        ( usesPrice || usesVolume ) && NOT ( [Units] > 0 ), "INSUFFICIENT BASELINE: baseline units are 0 in this window, so price is undefined",
        st = "MARKET_GROWTH" && NOT ( [Value] > 0 ), "INSUFFICIENT BASELINE: baseline market value is 0",
        st = "MARKET_SHARE" && NOT ( [Market Value] > 0 ), "INSUFFICIENT BASELINE: baseline market value is 0; share is undefined",
        "OK" )""", None, X),
        M("Scenario OK", "[Scenario Status] = \"OK\"", None, X, hidden=True),
        M("Scenario Entity", """VAR et = [Scenario Entity Type]
VAR ml = [Scenario Market Level]
VAR mk = [Scenario Market Key]
VAR mlabel = IF ( ml = "total", "total market", SUBSTITUTE ( ml, "_", " " ) & " " & mk )
RETURN
    SWITCH ( et,
        "product", "Product " & SELECTEDVALUE ( 'Product'[Product] ) & " within " & mlabel,
        "company", "Company " & SELECTEDVALUE ( 'Company'[Company] ) & " within " & mlabel,
        "total", "Total market",
        "supergroup", "Market: " & mlabel, "therapy_group", "Market: " & mlabel,
        "subgroup", "Market: " & mlabel, "molecule", "Market: " & mlabel,
        "No single entity selected" )""", None, X),
        M("Scenario Price Pct Effective", """SWITCH ( [Scenario Type Selected], "PRICE_CHANGE", [Price Change Assumption], "PRICE_VOLUME_CHANGE", [Price Change Assumption], 0 )""", None, X, hidden=True),
        M("Scenario Volume Pct Effective", """SWITCH ( [Scenario Type Selected], "VOLUME_CHANGE", [Volume Change Assumption], "PRICE_VOLUME_CHANGE", [Volume Change Assumption], 0 )""", None, X, hidden=True),
        M("Scenario Growth Pct Effective", "IF ( ISFILTERED ( 'Market Growth Assumption' ), [Market Growth Assumption], 0 )", None, X, hidden=True),
        # OBSERVED baseline
        M("Baseline Value (OBSERVED)", "IF ( [Scenario OK], [Value] )", F_VALUE, X),
        M("Baseline Units (OBSERVED)", "IF ( [Scenario OK], [Units] )", F_UNITS, X),
        M("Baseline Qty (OBSERVED)", "IF ( [Scenario OK], [Qty] )", F_UNITS, X),
        M("Baseline Price per Pack (OBSERVED)", """VAR v = [Value]
VAR u = [Units]
RETURN IF ( [Scenario OK] && u > 0, v * 10000000 / ( u * 1000 ) )""", F_PRICE, X,
          desc="Rs per pack = value_cr x 10^7 / (units_k x 10^3) = 1e4 x value_cr / units_k (equals source PR)."),
        M("Baseline Market Value (OBSERVED)", "IF ( [Scenario OK] && [Scenario Type Selected] = \"MARKET_SHARE\", [Market Value] )", F_VALUE, X),
        M("Baseline Share (OBSERVED)", "IF ( [Scenario OK] && [Scenario Type Selected] = \"MARKET_SHARE\", [Share of Market %] )", F_SHARE, X),
        # CALCULATED results
        M("Scenario Price per Pack (CALCULATED)", """VAR p0 = [Baseline Price per Pack (OBSERVED)]
RETURN IF ( NOT ISBLANK ( p0 ) && [Scenario Type Selected] IN { "PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE" },
    p0 * ( 1 + [Scenario Price Pct Effective] / 100 ) )""", F_PRICE, X),
        M("Scenario Units (CALCULATED)", """IF ( [Scenario OK] && [Scenario Type Selected] IN { "PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE" },
    [Units] * ( 1 + [Scenario Volume Pct Effective] / 100 ) )""", F_UNITS, X),
        M("Scenario Qty (CALCULATED)", """IF ( [Scenario OK] && [Scenario Type Selected] IN { "PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE" },
    [Qty] * ( 1 + [Scenario Volume Pct Effective] / 100 ) )""", F_UNITS, X),
        M("Scenario Market Value (CALCULATED)", """IF ( [Scenario OK] && [Scenario Type Selected] = "MARKET_SHARE",
    [Market Value] * ( 1 + [Scenario Growth Pct Effective] / 100 ) )""", F_VALUE, X),
        M("Scenario Value (CALCULATED)", """VAR st = [Scenario Type Selected]
RETURN
    IF ( [Scenario OK],
        SWITCH ( st,
            "MARKET_GROWTH", [Value] * ( 1 + [Market Growth Assumption] / 100 ),
            "MARKET_SHARE", [Target Share Assumption] / 100 * [Scenario Market Value (CALCULATED)],
            [Scenario Price per Pack (CALCULATED)] * [Scenario Units (CALCULATED)] * 1000 / 10000000 ) )""", F_VALUE, X),
        M("Scenario Value Change (CALCULATED)", "IF ( [Scenario OK], [Scenario Value (CALCULATED)] - [Value] )", F_VALUE, X),
        M("Scenario Value Change % (CALCULATED)", """VAR v0 = [Value]
RETURN IF ( [Scenario OK] && v0 <> 0, ( [Scenario Value (CALCULATED)] - v0 ) / v0 * 100 )""", F_PCT, X,
          desc="Undefined (BLANK) when the baseline is 0."),
        M("Scenario Units Change % (CALCULATED)", """VAR u0 = [Units]
VAR u1 = [Scenario Units (CALCULATED)]
RETURN IF ( NOT ISBLANK ( u1 ) && u0 <> 0, ( u1 - u0 ) / u0 * 100 )""", F_PCT, X),
        M("Scenario Price Change % (CALCULATED)", """VAR p0 = [Baseline Price per Pack (OBSERVED)]
VAR p1 = [Scenario Price per Pack (CALCULATED)]
RETURN IF ( NOT ISBLANK ( p1 ) && p0 <> 0, ( p1 - p0 ) / p0 * 100 )""", F_PCT, X),
        M("Price Effect (CALCULATED)", """IF ( [Scenario OK] && [Scenario Type Selected] IN { "PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE" },
    [Value] * ( [Scenario Price Pct Effective] / 100 ) )""", F_VALUE, X),
        M("Volume Effect (CALCULATED)", """IF ( [Scenario OK] && [Scenario Type Selected] IN { "PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE" },
    [Value] * ( [Scenario Volume Pct Effective] / 100 ) )""", F_VALUE, X),
        M("Price x Volume Interaction (CALCULATED)", """IF ( [Scenario OK] && [Scenario Type Selected] IN { "PRICE_CHANGE", "VOLUME_CHANGE", "PRICE_VOLUME_CHANGE" },
    [Value] * ( [Scenario Price Pct Effective] / 100 ) * ( [Scenario Volume Pct Effective] / 100 ) )""", F_VALUE, X),
        M("Scenario Market Value Change % (CALCULATED)", """VAR m0 = [Market Value]
VAR m1 = [Scenario Market Value (CALCULATED)]
RETURN IF ( NOT ISBLANK ( m1 ) && m0 <> 0, ( m1 - m0 ) / m0 * 100 )""", F_PCT, X),
        M("Scenario Share Change pp (CALCULATED)", """IF ( [Scenario OK] && [Scenario Type Selected] = "MARKET_SHARE",
    [Target Share Assumption] - [Share of Market %] )""", F_PP, X),
        M("Scenario Formula", """SWITCH ( [Scenario Type Selected],
    "PRICE_CHANGE", "price_1 = price_0 x (1 + price_change_pct/100); units_1 = units_0 (no elasticity); value_1 [Rs crore] = price_1 [Rs/pack] x units_1 ['000 packs] x 10^3 / 10^7",
    "VOLUME_CHANGE", "units_1 = units_0 x (1 + volume_change_pct/100); qty_1 = qty_0 x (1 + volume_change_pct/100); price_1 = price_0; value_1 = price_1 x units_1 x 10^3 / 10^7",
    "PRICE_VOLUME_CHANGE", "price_1 = price_0 x (1 + p); units_1 = units_0 x (1 + v); value_1 = value_0 x (1 + p)(1 + v); price effect = value_0 x p, volume effect = value_0 x v, interaction = value_0 x p x v",
    "MARKET_GROWTH", "value_1 = value_0 x (1 + market_growth_pct/100); units, qty and price are not modelled",
    "MARKET_SHARE", "market_1 = market_0 x (1 + market_growth_pct/100); value_1 = target_share_pct/100 x market_1; share moves to/from the rest of the market",
    "Select a scenario type" )""", None, X),
    ]


# ------------------------------------------------------------------------------------------------ tables
def tables() -> list[Table]:
    T, Col = Table, Column
    out = [
        T("Pack Month", [
            Col("pfc", "int64", hidden=True), Col("period", "dateTime", hidden=True, fmt="yyyy-mm-dd"),
            Col("value_cr", "double", hidden=True, summarize="sum", desc="Rs crore", mdx=False),
            Col("units_k", "double", hidden=True, summarize="sum", desc="'000 packs (inferred)", mdx=False),
            Col("qty_k", "double", hidden=True, summarize="sum", desc="'000 counting units (inferred)", mdx=False)],
          "m", M_FACT, desc="ONE ROW = one pack (PFC) in one calendar month (dense, zero months kept). Source: data/processed/fact_pack_month.parquet."),
        T("Pack", [
            Col("pfc", "int64", hidden=True), Col("prod_code", "int64", hidden=True), Col("company", "string", hidden=True),
            Col("subgroup", "string", hidden=True), Col("index_desc", "string", hidden=True),
            Col("Molecule", "string"), Col("Molecule Order", "int64", hidden=True), Col("Plain/Combination", "string"), Col("Molecule Count", "string"),
            Col("Form (NFC1)", "string"), Col("Dosage Form", "string")],
          "m", M_PACK, desc="ONE ROW = one pack (PFC). Pack-grain attributes (molecule and form vary within a product)."),
        T("Product", [
            Col("prod_code", "int64", hidden=True),
            Col("Product Code", "string", desc="Authoritative product identity (source PROD_CODE)."),
            Col("Product", "string", desc="Display label: BRAND (COMPANY) · product code. Brand names are NOT unique."),
            Col("Brand", "string", desc="Display only; never a key."),
            Col("Company Label", "string", desc="Company of the product (display attribute)."),
            Col("Launch Month", "dateTime", fmt=F_DATE)],
          "m", M_PRODUCT, desc="ONE ROW = one product (PROD_CODE)."),
        T("Market", [
            Col("Subgroup", "string", desc="PRIMARY market definition (source SUBGROUP)."),
            Col("Therapy Group", "string", desc="Source GROUP; not nested in supergroup (2 exceptions)."),
            Col("Supergroup", "string", desc="Therapy area (source SUPERGROUP)."),
            Col("Acute/Chronic", "string"),
            Col("Subgroup Order", "int64", hidden=True), Col("Supergroup Order", "int64", hidden=True),
            Col("Therapy Group Order", "int64", hidden=True)],
          "m", M_MARKET, desc="ONE ROW = one therapy subgroup. Roll up to supergroup via subgroup.",
          hierarchies=[("Therapy Hierarchy", ["Supergroup", "Subgroup"])]),
        T("Company", [Col("Company", "string"), Col("Indian/MNC", "string"),
                      Col("Company Order", "int64", hidden=True)], "m", M_COMPANY,
          desc="ONE ROW = one company (source COMPANY)."),
        T("Period", [
            Col("Period", "dateTime", "[Period]", fmt=F_DATE),
            Col("Year", "int64", "[Year]", fmt="0"),
            Col("Month No", "int64", "[Month No]", hidden=True),
            Col("Period Label", "string", "[Period Label]", sort_by="Period Index"),
            Col("Period Index", "int64", "[Period Index]", hidden=True)],
          "calculated", """VAR mn = MIN ( 'Pack Month'[period] )
VAR mx = MAX ( 'Pack Month'[period] )
RETURN
    SELECTCOLUMNS (
        FILTER ( CALENDAR ( mn, mx ), DAY ( [Date] ) = 1 ),
        "Period", [Date],
        "Year", YEAR ( [Date] ),
        "Month No", MONTH ( [Date] ),
        "Period Label", FORMAT ( [Date], "mmm yyyy" ),
        "Period Index", DATEDIFF ( mn, [Date], MONTH ) + 1 )""",
          desc="ONE ROW = one calendar month present in the fact (Jun 2021 .. May 2024)."),
        T("Anchor", [
            Col("Anchor Month", "dateTime", "[Anchor Month]", fmt=F_DATE, hidden=True),
            Col("Anchor", "string", "[Anchor]", sort_by="Anchor Index"),
            Col("Anchor Index", "int64", "[Anchor Index]", hidden=True)],
          "calculated", """SELECTCOLUMNS ( 'Period', "Anchor Month", 'Period'[Period], "Anchor", 'Period'[Period Label], "Anchor Index", 'Period'[Period Index] )""",
          desc="Disconnected: the anchor month of the analysis window (single select)."),
        T("Basis", [
            Col("Basis", "string", "[Basis]", hidden=True),
            Col("Basis Name", "string", "[Basis Name]", sort_by="Basis Order"),
            Col("Basis Order", "int64", "[Basis Order]", hidden=True)],
          "calculated", """DATATABLE ( "Basis", STRING, "Basis Name", STRING, "Basis Order", INTEGER,
    { { "MONTH", "Month", 1 }, { "YTD", "Calendar YTD", 2 }, { "MAT", "MAT", 3 } } )""",
          desc="Disconnected: MONTH | calendar YTD | MAT (same definitions as sql/periods.sql)."),
        T("Opportunity Product", _opp_columns(OPP_PRODUCT_TYPES, {"Anchor": F_DATE, "Score": F_SCORE, "Market Growth %": F_PCT,
                                                                   "Evolution Index": F_INDEX, "Share in Market %": F_SHARE},
                                              hidden=("prod_code", "Anchor", "Basis", "Methodology Version")),
          "m", _m_typed("opportunity_product.parquet", OPP_PRODUCT_TYPES),
          desc="Canonical OPP-1.0.0 product-in-market scores exported by pci_powerbi.export (one row per anchor x basis x product-in-subgroup). Never recomputed in DAX."),
        T("Opportunity Market", _opp_columns(OPP_MARKET_TYPES, {"Anchor": F_DATE, "Score": F_SCORE, "Market Growth %": F_PCT,
                                                                 "Market Size Share %": F_SHARE},
                                             hidden=("Anchor", "Basis", "Methodology Version")),
          "m", _m_typed("opportunity_market.parquet", OPP_MARKET_TYPES),
          desc="Canonical OPP-1.0.0 market (subgroup) scores exported by pci_powerbi.export."),
        T("Scenario Type", [
            Col("Scenario Type", "string", "[Scenario Type]"),
            Col("Scenario Order", "int64", "[Scenario Order]", hidden=True)],
          "calculated", """DATATABLE ( "Scenario Type", STRING, "Scenario Order", INTEGER,
    { { "PRICE_CHANGE", 1 }, { "VOLUME_CHANGE", 2 }, { "PRICE_VOLUME_CHANGE", 3 }, { "MARKET_GROWTH", 4 }, { "MARKET_SHARE", 5 } } )""",
          desc="Supported M7 scenario types. Elasticity and forecasts are not supported."),
        T("Price Change", [Col("Price Change %", "double", "[Price Change %]", fmt=F_PCT)], "calculated",
          """SELECTCOLUMNS ( GENERATESERIES ( -99, 1000, 1 ), "Price Change %", [Value] * 1.0 )""",
          desc="Allowed grid for price_change_pct: (-100, 1000], step 1. Values outside the M7 range do not exist here."),
        T("Volume Change", [Col("Volume Change %", "double", "[Volume Change %]", fmt=F_PCT)], "calculated",
          """SELECTCOLUMNS ( GENERATESERIES ( -100, 1000, 1 ), "Volume Change %", [Value] * 1.0 )""",
          desc="Allowed grid for volume_change_pct: [-100, 1000], step 1."),
        T("Market Growth Assumption", [Col("Market Growth %", "double", "[Market Growth %]", fmt=F_PCT)], "calculated",
          """SELECTCOLUMNS ( GENERATESERIES ( -100, 1000, 1 ), "Market Growth %", [Value] * 1.0 )""",
          desc="Allowed grid for market_growth_pct: [-100, 1000], step 1."),
        T("Target Share", [Col("Target Share %", "double", "[Target Share %]", fmt=F_SHARE)], "calculated",
          """SELECTCOLUMNS ( GENERATESERIES ( 0, 1000, 1 ), "Target Share %", [Value] / 10 )""",
          desc="Allowed grid for target_share_pct: [0, 100], step 0.1."),
        T("Metrics", [Col("Placeholder", "int64", "[Placeholder]", hidden=True)], "calculated",
          """ROW ( "Placeholder", 0 )""", measures=_measures(),
          desc="All measures. Definitions: ANALYTICS_METHODS.md, sql/metrics.sql, docs/OPPORTUNITY_SCORING.md, docs/SCENARIO_ENGINE.md."),
    ]
    return out


RELATIONSHIPS = [
    ("r_fact_period", "'Pack Month'.period", "Period.Period"),
    ("r_fact_pack", "'Pack Month'.pfc", "Pack.pfc"),
    ("r_pack_product", "Pack.prod_code", "Product.prod_code"),
    ("r_pack_market", "Pack.subgroup", "Market.Subgroup"),
    ("r_pack_company", "Pack.company", "Company.Company"),
    ("r_oppp_product", "'Opportunity Product'.prod_code", "Product.prod_code"),
    ("r_oppp_market", "'Opportunity Product'.Subgroup", "Market.Subgroup"),
    ("r_oppp_company", "'Opportunity Product'.Company", "Company.Company"),
    ("r_oppm_market", "'Opportunity Market'.Subgroup", "Market.Subgroup"),
]


# ------------------------------------------------------------------------------------------------ TMDL writer
def q(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _expr_lines(expr: str, indent: int) -> list[str]:
    pad = "\t" * indent
    return [pad + ln.rstrip() for ln in expr.strip("\n").splitlines() if ln.strip()]


def _desc(desc: str | None, indent: int) -> list[str]:
    return [("\t" * indent) + "/// " + desc] if desc else []


def table_tmdl(t: Table) -> str:
    L = _desc(t.desc, 0) + [f"table {q(t.name)}"]
    if t.hidden:
        L.append("\tisHidden")
    L.append("")
    for m in t.measures:
        L += _desc(m.desc, 1)
        L.append(f"\tmeasure {q(m.name)} =")
        L += _expr_lines(m.expr, 3)
        if m.fmt:
            L.append(f"\t\tformatString: {m.fmt}")
        if m.folder:
            L.append(f"\t\tdisplayFolder: {m.folder}")
        if m.hidden:
            L.append("\t\tisHidden")
        L.append("")
    for c in t.columns:
        L += _desc(c.desc, 1)
        L.append(f"\tcolumn {q(c.name)}")
        L.append(f"\t\tdataType: {c.dtype}")
        if c.fmt:
            L.append(f"\t\tformatString: {c.fmt}")
        if c.hidden:
            L.append("\t\tisHidden")
        if not c.mdx:
            L.append("\t\tisAvailableInMdx: false")
        if t.kind == "calculated":
            L.append("\t\tisNameInferred")
        L.append(f"\t\tsummarizeBy: {c.summarize}")
        L.append(f"\t\tsourceColumn: {c.source or c.name}")
        if c.sort_by:
            L.append(f"\t\tsortByColumn: {q(c.sort_by)}")
        L.append("")
    for hname, levels in t.hierarchies:
        L.append(f"\thierarchy {q(hname)}")
        L.append("")
        for lv in levels:
            L.append(f"\t\tlevel {q(lv)}")
            L.append(f"\t\t\tcolumn: {q(lv)}")
            L.append("")
    L.append(f"\tpartition {q(t.name)} = {'m' if t.kind == 'm' else 'calculated'}")
    L.append("\t\tmode: import")
    L.append("\t\tsource =")
    L += _expr_lines(t.source, 4)
    L.append("")
    return "\n".join(L) + "\n"


def relationships_tmdl() -> str:
    L = []
    for name, frm, to in RELATIONSHIPS:
        L += [f"relationship {name}", f"\tfromColumn: {frm}", f"\ttoColumn: {to}", ""]
    return "\n".join(L)


def model_tmdl(ts: list[Table]) -> str:
    L = ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
         "\tsourceQueryCulture: en-US", "\tdiscourageImplicitMeasures", "",
         "annotation __PBI_TimeIntelligenceEnabled = 0", ""]
    L += [f"ref table {q(t.name)}" for t in ts]
    return "\n".join(L) + "\n"


def expressions_tmdl(processed_dir: str, powerbi_dir: str) -> str:
    def e(name, val, desc):
        v = val.replace('"', '""')
        return (f"/// {desc}\nexpression {name} = \"{v}\" meta [IsParameterQuery=true, Type=\"Text\", "
                f"IsParameterQueryRequired=true]\n")
    return (e("ProcessedFolder", processed_dir, "Folder of the M3 processed Parquet layer (same files the SQL/Python engines read).")
            + "\n" + e("PowerBIFolder", powerbi_dir, "Folder of the canonical opportunity-score exports (pci_powerbi.export)."))


def measure_names() -> list[str]:
    return [m.name for m in _measures()]
