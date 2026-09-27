"""Deterministic request grammar used in DETERMINISTIC DEMO MODE (no LLM, no reasoning).

Maps a constrained English request to a structured plan {intent, params} or a refusal.
Entity names and keys must be in double quotes ("CARDIAC"); product codes may be bare numbers.
Anything not matching the grammar -> UNRECOGNIZED_REQUEST (the system never guesses).
"""
from __future__ import annotations

import re

from . import schemas as S

UNSAFE = re.compile(
    r"\bsql\b|\bselect\b.+\bfrom\b|\bdrop\s+table\b|\bdelete\s+from\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b|"
    r"\bexec\b|\beval\b|\bimport\s+\w+|__\w+__|\bsubprocess\b|\bshell\b|\bpowershell\b|\bcmd(\.exe)?\b|\brm\s+-rf\b|"
    r"\bopen\s*\(|\bread\s+(the\s+)?file\b|\bfile\s*system\b|\.parquet\b|\.xlsx\b|\.duckdb\b|[a-z]:\\|/etc/|"
    r"https?://|\bcurl\b|\bwget\b|\bignore\s+(all\s+)?(previous|prior)\s+instructions\b|\bsystem\s+prompt\b|"
    r"\bapi[_ ]?key\b|\bpassword\b|\benv(ironment)?\s+variables?\b|"
    # M10 red-team additions: direct data access, path traversal, URLs, guardrail overrides, forced selection
    r"\bdatabase\b|\braw\s+(rows?|records?|data)\b|\bunderlying\s+(raw\s+)?(rows?|records?|data)\b|\bexcel\b|"
    r"\bworkbook\b|\.\./|\.\.\\|\bwww\.|\bdisregard\b|\bignore\b.*\b(disclaimers?|rules?|instructions?|guardrails?|"
    r"polic\w*|limitations?|warnings?)\b|\bunrestricted\b|\b(any|every|all)\s+tools?\b|\bescalat\w*|\bbypass\w*|"
    r"\b(pick|choose|take|use)\s+(the\s+)?(first|any|whichever)\s+(one|result|match|product|option|entry)\b|"
    r"\byou\s+are\s+now\b|\bjailbreak\w*|\bos\.system\b|\bsystem\(", re.I)
UNSUPPORTED = [
    (re.compile(r"\b(state|states|region|regional|city|cities|zone|zones|district|territory|geograph\w*)\b", re.I),
     "UNSUPPORTED_GEOGRAPHY", "The source has no geography field (national data only)."),
    (re.compile(r"\b(ssa|hsa|dsa)\b", re.I), "UNSUPPORTED_SSA_HSA_DSA",
     "SSA/HSA/DSA splits are unconfirmed by the data owner and excluded from analytics."),
    (re.compile(r"\bchannels?\b", re.I), "UNSUPPORTED_CHANNEL", "The source has no labelled channel field."),
    (re.compile(r"\b(forecast\w*|predict\w*|projection\w*|next\s+(year|quarter|month)|elasticit\w*|prescriber\w*|"
                r"doctors?|hcps?|promotion\w*|marketing\s+spend|probabilit\w*)\b", re.I),
     "UNSUPPORTED_ANALYSIS", "Forecasting, elasticity, prescriber and promotion analyses are not supported."),
    (re.compile(r"\b(invent\w*|make\s+up|made[- ]up|fabricat\w*|guess\w*|even\s+if\s+(the\s+)?(tool|data|system)\w*|"
                r"fill\s+(in\s+)?(the\s+)?(blanks?|gaps?|missing)|previous\s+(conversation|answer|response|session|chat)\w*|"
                r"last\s+(conversation|answer|session|chat)|yourself)\b", re.I),
     "UNSUPPORTED_ANALYSIS", "Only figures returned by validated tools in this request are reported: values are never "
                             "invented, estimated, carried over from earlier conversations or computed outside the tools."),
]
LEVEL_WORDS = [("therapy group", "therapy_group"), ("therapy area", "supergroup"), ("supergroup", "supergroup"),
               ("subgroup", "subgroup"), ("molecule", "molecule"), ("company", "company"), ("total", "total")]
SEGMENT_WORDS = [(r"acute|chronic", "acute_chronic"), (r"indian|mnc", "indian_mnc"),
                 (r"plain|combination", "plain_combination"), (r"molecule count|number of molecules", "molecule_count"),
                 (r"dosage|form", "dosage_form"), (r"nfc", "nfc1")]
SUPPORTED_FORMS = [
    'market performance [by subgroup|therapy area|therapy group|molecule|total] [MAT|YTD|month] [for YYYY-MM]',
    'market trend for subgroup|therapy area "<name>"   |   total market trend',
    'brand performance for "<brand name>"   |   product <prod_code> performance',
    'top [N] products [in subgroup|therapy area "<name>"]',
    'company performance [in subgroup|therapy area "<name>"]',
    'therapy performance [by subgroup|therapy group] [within "<therapy area>"]',
    'segment analysis by acute chronic|indian mnc|plain combination|dosage form|nfc [in therapy area "<name>"]',
    'product|market opportunities [in therapy area|subgroup "<name>"]   |   opportunity detail for product|market "<key>"',
    'what if price|volume +/-X% [and volume +/-Y%] for <entity type> "<key>"',
    'what if market growth X% for subgroup|therapy area "<name>"',
    'what if share X% for product <code>|company "<name>" in subgroup|therapy area "<name>"',
    'which markets are growing and which products have strong relative momentum',
    'data quality status',
]


def _quoted(t):
    return re.findall(r'"([^"]+)"', t)


def _period(t):
    p = {}
    m = re.search(r"\b(20\d\d)-(\d\d)(?:-(\d\d))?\b", t)
    if m:
        p["anchor"] = f"{m.group(1)}-{m.group(2)}-01"
    if re.search(r"\bmat\b", t, re.I):
        p["basis"] = "MAT"
    elif re.search(r"\bytd\b|year[- ]to[- ]date", t, re.I):
        p["basis"] = "YTD"
    elif re.search(r"\bmonth(ly)?\b", t, re.I):
        p["basis"] = "MONTH"
    return p


def _top(t, default=5):
    m = re.search(r"\btop\s+(\d{1,3})\b", t, re.I)
    return max(1, min(20, int(m.group(1)))) if m else default


def _level_after(t, word_before):
    """Level named right before a quoted key, e.g. 'in therapy area "CARDIAC"'."""
    m = re.search(word_before + r"\s+(therapy group|therapy area|supergroup|subgroup|molecule|company)\s+\"([^\"]+)\"",
                  t, re.I)
    if not m:
        return None, None
    lvl = dict(LEVEL_WORDS)[m.group(1).lower()]
    return lvl, m.group(2)


def _first_level(t, default):
    for w, lvl in LEVEL_WORDS:
        if re.search(r"\b" + w + r"\b", t, re.I):
            return lvl
    return default


def parse(text: str) -> dict:
    """Return {"intent", "params"} or {"refusal": status, "message"}; never guesses."""
    t = " ".join(str(text or "").split())
    if not t or len(t) > 500:
        return {"refusal": S.UNRECOGNIZED_REQUEST, "message": "Empty or overly long request."}
    if UNSAFE.search(t):
        return {"refusal": S.UNSAFE_REQUEST,
                "message": "Requests for code, SQL, files, credentials or instruction overrides are refused."}
    for rx, code, msg in UNSUPPORTED:
        if rx.search(t):
            return {"refusal": code, "message": msg}
    low = t.lower()
    per = _period(t)
    q = _quoted(t)

    # ---- data quality (Phase 3)
    if re.search(r"\bdata[- ]quality\b|\bdq\s+(status|check)", low):
        return {"intent": "DATA_QUALITY", "params": {}}

    # ---- scenarios
    if re.search(r"\bwhat[- ]if\b|\bscenario\b|\bsimulat\w*", low):
        num = lambda rx: (lambda m: float(m.group(1)) if m else None)(re.search(rx, low))  # noqa: E731
        price = num(r"price\s*(?:change\s*)?(?:of\s*)?([+-]?\d+(?:\.\d+)?)\s*%")
        vol = num(r"(?:volume|units?)\s*(?:change\s*)?(?:of\s*)?([+-]?\d+(?:\.\d+)?)\s*%")
        growth = num(r"market\s+growth\s*(?:of\s*)?([+-]?\d+(?:\.\d+)?)\s*%")
        share = num(r"share\s*(?:of\s*)?([+-]?\d+(?:\.\d+)?)\s*%")
        params = dict(per)
        m = re.search(r"\bfor\s+(total market|product\s+(\d+)|(therapy group|therapy area|supergroup|subgroup|molecule|"
                      r"company|product)\s+\"([^\"]+)\")", low if not q else t, re.I)
        if not m:
            return {"refusal": S.UNRECOGNIZED_REQUEST, "message": "Scenario needs 'for <entity type> \"<key>\"'.",
                    "supported": SUPPORTED_FORMS}
        if m.group(1).lower() == "total market":
            params.update(entity_type="total", entity_key="TOTAL")
        elif m.group(2):
            params.update(entity_type="product", entity_key=m.group(2))
        else:
            params.update(entity_type=dict(LEVEL_WORDS + [("product", "product")])[m.group(3).lower()],
                          entity_key=m.group(4))
        if share is not None:
            lvl, key = _level_after(t, r"\bin")
            params.update(scenario_type="MARKET_SHARE", assumptions={"target_share_pct": share})
            if growth is not None:
                params["assumptions"]["market_growth_pct"] = growth
            if lvl:
                params.update(market_level=lvl, market_key=key)
        elif growth is not None:
            params.update(scenario_type="MARKET_GROWTH", assumptions={"market_growth_pct": growth})
        elif price is not None and vol is not None:
            params.update(scenario_type="PRICE_VOLUME_CHANGE",
                          assumptions={"price_change_pct": price, "volume_change_pct": vol})
        elif price is not None:
            params.update(scenario_type="PRICE_CHANGE", assumptions={"price_change_pct": price})
        elif vol is not None:
            params.update(scenario_type="VOLUME_CHANGE", assumptions={"volume_change_pct": vol})
        else:
            return {"refusal": S.UNRECOGNIZED_REQUEST, "message": "Scenario needs an explicit % assumption.",
                    "supported": SUPPORTED_FORMS}
        return {"intent": "SCENARIO", "params": params}

    # ---- multi-step
    if "growing" in low and "momentum" in low:
        return {"intent": "GROWTH_AND_MOMENTUM", "params": {**per, "top_n": _top(t)}}

    # ---- opportunity
    if "opportunit" in low:
        level = "market" if re.search(r"\bmarkets?\b", low) and not re.search(r"\bproducts?\b", low) else "product"
        if "detail" in low and q:
            return {"intent": "OPPORTUNITY_DETAIL", "params": {**per, "level": level, "entity_key": q[-1]}}
        p = {**per, "level": level, "top_n": _top(t)}
        lvl, key = _level_after(t, r"\bin")
        if lvl:
            p.update(market_level=lvl, market_key=key)
        return {"intent": "OPPORTUNITY", "params": p}

    # ---- market trend
    if "trend" in low:
        if "total" in low or not q:
            return {"intent": "MARKET_TREND", "params": {"level": "total", "key": "TOTAL"}}
        lvl, key = _level_after(t, r"\bfor") if _level_after(t, r"\bfor")[0] else (_first_level(t, "subgroup"), q[0])
        return {"intent": "MARKET_TREND", "params": {"level": lvl, "key": key}}

    # ---- brand / product
    m = re.search(r"\bproduct\s+(\d+)\b", low)
    if m and re.search(r"performance|growth|share|sales", low):
        return {"intent": "BRAND_PERFORMANCE", "params": {**per, "prod_code": m.group(1)}}
    if re.search(r"\b(top|leading|best[- ]selling)\b.*\bproducts?\b", low):
        p = {**per, "top_n": _top(t)}
        lvl, key = _level_after(t, r"\bin")
        if lvl:
            p.update(market_level=lvl, market_key=key)
        return {"intent": "TOP_PRODUCTS", "params": p}
    if re.search(r"\bbrand\b|\bproduct\b", low):
        if not q:
            return {"refusal": S.UNRECOGNIZED_REQUEST, "message": 'Put the brand name in double quotes: "NAME".',
                    "supported": SUPPORTED_FORMS}
        return {"intent": "BRAND_PERFORMANCE", "params": {**per, "name": q[0]}}

    # ---- explicit market performance (before therapy/company words, e.g. "market performance by therapy area")
    if re.search(r"\bmarkets?\s+performance\b", low):
        return {"intent": "MARKET_PERFORMANCE",
                "params": {**per, "level": _first_level(re.sub(r"(?i)\bmarkets?\b", "", t), "subgroup"),
                           "top_n": _top(t)}}

    # ---- segment first: "segment analysis ... in company X" is a segment request scoped to a company (M10 fix)
    if "segment" in low:
        seg = next((s for rx, s in SEGMENT_WORDS if re.search(rx, low)), None)
        if not seg:
            return {"refusal": S.UNRECOGNIZED_REQUEST, "message": "Name a supported segment.",
                    "supported": SUPPORTED_FORMS}
        p = {**per, "segment": seg}
        lvl, key = _level_after(t, r"\bin")
        if lvl:
            p.update(market_level=lvl, market_key=key)
        return {"intent": "SEGMENT_ANALYSIS", "params": p}

    # ---- company / therapy
    if re.search(r"\bcompan(y|ies)\b", low):
        p = {**per, "top_n": _top(t)}
        lvl, key = _level_after(t, r"\bin")
        if lvl:
            p.update(market_level=lvl, market_key=key)
        return {"intent": "COMPANY_PERFORMANCE", "params": p}
    if re.search(r"\btherap(y|ies)\b", low) and "segment" not in low:
        level = "subgroup" if "subgroup" in low else "therapy_group" if "therapy group" in low else "supergroup"
        p = {**per, "level": level, "top_n": _top(t)}
        m2 = re.search(r"\bwithin\s+\"([^\"]+)\"", t, re.I)
        if m2:
            p["within_supergroup"] = m2.group(1)
        return {"intent": "THERAPY_PERFORMANCE", "params": p}
    if "segment" in low:
        seg = next((s for rx, s in SEGMENT_WORDS if re.search(rx, low)), None)
        if not seg:
            return {"refusal": S.UNRECOGNIZED_REQUEST, "message": "Name a supported segment.",
                    "supported": SUPPORTED_FORMS}
        p = {**per, "segment": seg}
        lvl, key = _level_after(t, r"\bin")
        if lvl:
            p.update(market_level=lvl, market_key=key)
        return {"intent": "SEGMENT_ANALYSIS", "params": p}

    # ---- market performance
    if re.search(r"\bmarkets?\b", low):
        return {"intent": "MARKET_PERFORMANCE",
                "params": {**per, "level": _first_level(t.replace("market", ""), "subgroup"), "top_n": _top(t)}}
    return {"refusal": S.UNRECOGNIZED_REQUEST, "message": "The request does not match a supported analysis.",
            "supported": SUPPORTED_FORMS}
