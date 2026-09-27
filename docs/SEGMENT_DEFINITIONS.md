# SEGMENT DEFINITIONS (M4)

"Segment" here means a **source-confirmed, pack-level classification field that is not a market or company hierarchy**, which can be used to split any market, or the total, into mutually exclusive groups. Only fields marked CONFIRMED in DATA_DICTIONARY.md §4 are used. No segment is invented or derived from business assumptions.

| `segment` (API / entity_type) | Source column | Members | Unclassified | Notes |
|---|---|---|---|---|
| `acute_chronic` | `ACUTE_CHRONIC` | ACUTE, CHRONIC | 0 | Determined by subgroup (each subgroup is one or the other) |
| `indian_mnc` | `INDIAN_MNC` | INDIAN, MNC | 0 | Company ownership attribute (company → 1 value) |
| `plain_combination` | `Plain/Combination` | Plain, Combination | 1 pack → `(UNCLASSIFIED)` | Molecule attribute |
| `molecule_count` | `Ind` | 1 … 10 | 1 pack → `(UNCLASSIFIED)` | Number of molecules in `MOLECULE_DESC` (confirmed) |
| `dosage_form` | `SHORT DESCRIPTION` | 13 forms, source spelling kept (e.g. "Parentral") | 0 | Simplified form bucket |
| `nfc1` | `NFC 1` | 17 NFC level-1 forms | 0 | EphMRA-style form level 1 |

## Rules
- Every segment is a **partition** of packs, so segment totals reconcile exactly to the scope total, and shares sum to 100% (tested).
- Segments can be evaluated within any market scope (`get_segment_analysis(..., market_level, market_key)`) or within a company.
- **Not segments** (not supported): geography, channel, prescriber/HCP, SSA/HSA/DSA (meaning unconfirmed), new-introduction status. NI exists in the source only as May-snapshot MAT values, not as a segment flag for arbitrary periods; it is deferred until a period-consistent definition is agreed.
