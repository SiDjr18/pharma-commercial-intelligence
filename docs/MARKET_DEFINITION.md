# MARKET DEFINITION (M4)

Status: **ANALYTICAL DEFINITION**. It is derived from the source's own classification hierarchy. The source contains no field called "market", so this is a documented analytical choice, not a source-stated fact.

## 1. Is there an explicit market field?
**No.** None of the 203 source columns is a market/category definition assigned for competitive analysis. The candidates are classification fields present at pack level (DATA_DICTIONARY §4):

| Candidate | Distinct | Nulls | Packs per key (median) | Nesting | Assessment |
|---|---|---|---|---|---|
| `SUPERGROUP` | 25 | 0 | ~2,400 | top of therapy hierarchy | Too broad for competitive share (e.g. a whole therapy area). **Used as a broad market level.** |
| `GROUP` | 261 | 0 | ~160 | **not nested** in SUPERGROUP (2 groups span 2 supergroups) | Valid pack partition, but it can't be rolled into supergroups. **Used as an intermediate level only; no supergroup attribute.** |
| `SUBGROUP` | 1,889 | 0 | ~20 | → exactly 1 GROUP, 1 SUPERGROUP, 1 ACUTE_CHRONIC | Finest source-assigned therapeutic class, complete and consistently nested. **Chosen as the PRIMARY market.** |
| `MOLECULE_DESC` | 16,135 (+1 null) | 1 | 1 | not nested in SUBGROUP (1,108 molecules span >1 subgroup; 410 span >1 supergroup) | Valid "same molecule / generic competition" lens, but very fine: most molecules have one pack. **Offered as an alternative market level.** |
| `NFC` / `SHORT DESCRIPTION` | 261 / 13 | 0 | – | form hierarchy | Dosage form describes *how* a drug is delivered, not what it competes for. **Segment, not market.** |
| `ACUTE_CHRONIC`, `INDIAN_MNC`, `Plain/Combination`, `Ind` | 2–10 | 0–1 | – | – | Cross-cutting attributes. **Segments, not markets.** |
| `INDEX` | 65,398 | 0 | – | product × subgroup | Product-in-market key, not a market. **Used for product-within-market analysis.** |
| `BRANDS` | 59,052 | 0 | – | not unique | **Never a market or product key.** |

## 2. Definition adopted
**Primary market = therapy subgroup (`SUBGROUP`).**

- **Assignment rule:** every pack is assigned to the single subgroup recorded on its own row (`pack.subgroup`). There is no derivation, mapping or inference.
- **Market levels available** (`entity_type`): `total` → `supergroup` → `subgroup`, plus `therapy_group` (stand-alone) and `molecule` (alternative lens).
- **Market key** = the source code/label itself (`entity_key`). Labels are never used as keys for products.

## 3. Properties (all verified by tests in `tests/test_sql_analytics.py`)
| Question | Answer |
|---|---|
| Can a pack belong to multiple markets **at one level**? | **No.** Each level is a partition: exactly one row per pack per level (`test_every_pack_in_exactly_one_entity`). |
| Do market totals overlap? | **No** within a level. Across levels they nest (subgroup ⊂ supergroup) except therapy_group and molecule, which are separate partitions. |
| Do all packs receive a market? | **Yes.** SUBGROUP is 100% populated (0 nulls). |
| Unknown / unclassified packs | Subgroup: none. Molecule level: 1 pack with no molecule → explicit member `(UNCLASSIFIED)`, never dropped. |
| Combination products | Assigned by their own subgroup, like any pack. Combinations share subgroups with plain products in 362 subgroups. For a combination-only view, apply the `plain_combination` segment within the market. |
| Products spanning several subgroups | 4,519 products have packs in >1 subgroup (e.g. different forms or strengths classified differently). Each **pack** counts in its own subgroup, so a product's value is **split** across markets, never duplicated. The product total equals the sum of its product-in-subgroup parts (`test_product_value_equals_sum_over_its_subgroup_markets`). |
| Impact on total reconciliation | Because each level is a partition, Σ markets = national total exactly, for value, units and qty, for every basis (`test_partition_totals_reconcile_to_market`, 15 entity types × 3 bases). |
| Market share denominator | The market's own total (all packs in that market); product/company shares within a market sum to 100%. |

## 4. Limitations
- SUBGROUP is the source provider's therapeutic classification, not a client-specific competitive-market definition. A brand team may define its market differently (e.g. specific molecules across subgroups). The model supports that through the `molecule` level, and future custom definitions can be added as extra partitions in `sql/entities.sql`.
- GROUP → SUPERGROUP has 2 source exceptions. Roll-ups to supergroup always go via subgroup.
- No geography, channel or prescriber dimension exists, so markets are national only.
