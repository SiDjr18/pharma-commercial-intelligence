"""Fictional vocabularies (M13). Plausible pharma-style, deliberately not real IMS entities.

Entity names are built from invented syllables, and codes carry an 'S' (synthetic) prefix or live in numeric
ranges that start at spec.FIRST_CODE. The local-only leakage test proves that no synthetic entity label or
code equals a real one. Only generic category words (acute/chronic, Indian/MNC, plain/combination, dosage
forms) are shared with the source vocabulary, because they are domains, not entities.
"""
from __future__ import annotations

THERAPY_AREAS = [  # (name, acute?) — fictional therapy-area labels
    ("CARDIOMETABOLIC CARE", False), ("GLUCOSE MANAGEMENT", False), ("DIGESTIVE HEALTH", True),
    ("SKIN SCIENCE", True), ("AIRWAY CARE", True), ("NEURO WELLBEING", False), ("PAIN & INFLAMMATION CARE", True),
    ("INFECTION DEFENCE", True), ("WOMEN'S VITALITY", True), ("BONE & JOINT CARE", False),
    ("VISION & HEARING", True), ("NUTRITION & VITALITY", True),
]

# syllables chosen to produce pronounceable invented words
_ONSET = ["b", "d", "f", "k", "l", "m", "n", "p", "r", "s", "t", "v", "z", "br", "kr", "pl", "tr", "vr", "zl", "qu"]
_VOWEL = ["a", "e", "i", "o", "u", "ae", "io", "ea"]
_CODA = ["", "", "n", "r", "l", "x", "s", "v"]
MOLECULE_SUFFIX = ["olinex", "avorin", "eprazine", "ozidate", "umetrol", "ilvane", "aprozin", "exitol",
                   "oravant", "ulimex", "idroxate", "enoquil"]
CLASS_WORDS = ["MODULATORS", "BLOCKERS", "AGONISTS", "INHIBITORS", "ANALOGUES", "COMBINATIONS", "COMPLEXES",
               "STABILISERS", "ENHANCERS", "REGULATORS"]
COMPANY_SUFFIX = ["PHARMA", "LIFESCIENCES", "HEALTHCARE", "LABS", "THERAPEUTICS", "REMEDIES", "BIOSCIENCES"]
DIVISION_WORDS = ["CARE", "PRIME", "SPECIALTY", "VITAL", "CRITICAL", "WELLNESS", "ALLIANCE"]
BRAND_SUFFIX = ["IX", "ORA", "EXA", "IVA", "ONE", "UMA", "ELA", "OXY", "AVIN", "IRAN", "ETTE", "OLIX"]
# dosage forms: generic category words (domain vocabulary, not entities)
FORMS = [  # (short description, form-level-1 group, abbreviation for pack descriptions)
    ("Tablet", "SF-ORAL SOLID", "TAB"), ("Capsule", "SF-ORAL SOLID", "CAP"), ("Syrup", "SF-ORAL LIQUID", "SYR"),
    ("Suspension", "SF-ORAL LIQUID", "SUSP"), ("Injection", "SF-PARENTERAL", "INJ"),
    ("Cream", "SF-TOPICAL", "CRM"), ("Ointment", "SF-TOPICAL", "OINT"), ("Gel", "SF-TOPICAL", "GEL"),
    ("Drops", "SF-OPHTHALMIC/OTIC", "DRP"), ("Inhaler", "SF-RESPIRATORY", "INH"), ("Powder", "SF-ORAL SOLID", "PWD"),
    ("Sachet", "SF-ORAL SOLID", "SACH"), ("Lotion", "SF-TOPICAL", "LOT"),
]
STRENGTHS = ["2.5MG", "5MG", "10MG", "20MG", "25MG", "40MG", "50MG", "100MG", "250MG", "500MG", "650MG", "1G",
             "0.1%", "1%", "5ML", "60ML", "100ML", "200ML"]


def word(rng, syllables: int) -> str:
    """Invented pronounceable word from spec'd syllables (uses rng.random() only)."""
    s = ""
    for i in range(syllables):
        s += _ONSET[int(rng.random() * len(_ONSET))] + _VOWEL[int(rng.random() * len(_VOWEL))]
        if i == syllables - 1:
            s += _CODA[int(rng.random() * len(_CODA))]
    return s
