-- Domain analytics (M4): thin, documented wrappers over metrics.sql.
-- Each macro restricts the entity types it accepts; an invalid type returns zero rows
-- (the Python API layer turns that into an explicit error).

-- A. MARKET — levels: total | supergroup | therapy_group | subgroup (primary) | molecule
CREATE OR REPLACE MACRO market_performance(p_level, p_anchor, p_basis) AS TABLE
SELECT * FROM entity_period(p_level, p_anchor, p_basis)
WHERE p_level IN ('total', 'supergroup', 'therapy_group', 'subgroup', 'molecule');

CREATE OR REPLACE MACRO market_trend(p_level, p_key) AS TABLE
SELECT * FROM entity_trend(p_level, p_key)
WHERE p_level IN ('total', 'supergroup', 'therapy_group', 'subgroup', 'molecule');

-- B. BRAND / PRODUCT — analytical key = prod_code; brand name is a display label only.
--    Optional market scope: product performance and share WITHIN a market.
CREATE OR REPLACE MACRO product_performance(p_anchor, p_basis,
                                            p_scope_type := 'total', p_scope_key := 'TOTAL') AS TABLE
SELECT ep.*, dp.brand, dp.company, dp.manufacturer_code, dp.prod_launch_month
FROM entity_period('product', p_anchor, p_basis, p_scope_type, p_scope_key) ep
JOIN dim_product dp ON CAST(dp.prod_code AS VARCHAR) = ep.entity_key
WHERE p_scope_type IN ('total', 'supergroup', 'therapy_group', 'subgroup', 'molecule', 'company',
                       'acute_chronic', 'indian_mnc', 'plain_combination', 'molecule_count',
                       'dosage_form', 'nfc1');

CREATE OR REPLACE MACRO product_trend(p_prod_code, p_scope_type := 'total', p_scope_key := 'TOTAL') AS TABLE
SELECT * FROM entity_trend('product', p_prod_code, p_scope_type, p_scope_key);

-- C. COMPANY — source COMPANY (parent of manufacturers); manufacturer level also available.
CREATE OR REPLACE MACRO company_performance(p_anchor, p_basis,
                                            p_scope_type := 'total', p_scope_key := 'TOTAL') AS TABLE
SELECT ep.*, dc.indian_mnc
FROM entity_period('company', p_anchor, p_basis, p_scope_type, p_scope_key) ep
JOIN dim_company dc ON dc.company = ep.entity_key
WHERE p_scope_type IN ('total', 'supergroup', 'therapy_group', 'subgroup', 'molecule',
                       'acute_chronic', 'plain_combination', 'molecule_count', 'dosage_form', 'nfc1');

-- D. THERAPY — supergroup | therapy_group | subgroup with validated hierarchy attributes.
--    Subgroup rows carry their (unique) supergroup and acute/chronic; rolling subgroup rows up to
--    supergroup reproduces supergroup totals exactly (tested). therapy_group is NOT nested in
--    supergroup (2 source exceptions), so no supergroup attribute is attached to it.
CREATE OR REPLACE MACRO therapy_performance(p_level, p_anchor, p_basis,
                                            p_scope_type := 'total', p_scope_key := 'TOTAL') AS TABLE
SELECT ep.*,
       CASE WHEN p_level = 'subgroup' THEN dt.therapy_group END AS therapy_group,
       CASE WHEN p_level = 'subgroup' THEN dt.supergroup END AS supergroup,
       CASE WHEN p_level = 'subgroup' THEN dt.acute_chronic END AS acute_chronic
FROM entity_period(p_level, p_anchor, p_basis, p_scope_type, p_scope_key) ep
LEFT JOIN dim_therapy dt ON dt.subgroup = ep.entity_key AND p_level = 'subgroup'
WHERE p_level IN ('supergroup', 'therapy_group', 'subgroup');

-- E. SEGMENT — only source-confirmed classification fields (docs/SEGMENT_DEFINITIONS.md).
CREATE OR REPLACE MACRO segment_performance(p_segment, p_anchor, p_basis,
                                            p_scope_type := 'total', p_scope_key := 'TOTAL') AS TABLE
SELECT * FROM entity_period(p_segment, p_anchor, p_basis, p_scope_type, p_scope_key)
WHERE p_segment IN ('acute_chronic', 'indian_mnc', 'plain_combination', 'molecule_count',
                    'dosage_form', 'nfc1');
