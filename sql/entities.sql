-- Pack-level entity membership (M4). Depends on views.sql.
-- Every entity type below is a PARTITION of packs: each pack belongs to exactly one entity of
-- each type (tested). Therefore totals over any entity type reconcile exactly to the market total,
-- and shares within a type sum to 100%.
--   entity_key   : analytical key (VARCHAR; codes where the source has codes)
--   entity_label : display label only — never used for grouping
-- Missing source values are placed in an explicit '(UNCLASSIFIED)' member (never dropped).

CREATE OR REPLACE VIEW entity_type_catalog AS
SELECT * FROM (VALUES
    ('total',              'market',   'Total national market (all packs)'),
    ('supergroup',         'market',   'Therapy area (source SUPERGROUP) — broad market'),
    ('therapy_group',      'market',   'Therapy group (source GROUP) — intermediate market; not nested in supergroup'),
    ('subgroup',           'market',   'Therapy subgroup (source SUBGROUP) — PRIMARY market definition'),
    ('molecule',           'market',   'Molecule / combination (source MOLECULE_DESC) — alternative molecule-market lens'),
    ('company',            'company',  'Company (source COMPANY)'),
    ('manufacturer',       'company',  'Manufacturer (source MANUFAC CODE / MANUFACT. DESC)'),
    ('product',            'product',  'Product / brand (source PROD_CODE; label = BRANDS + COMPANY)'),
    ('product_subgroup',   'product',  'Product within subgroup (source INDEX)'),
    ('acute_chronic',      'segment',  'Source ACUTE_CHRONIC'),
    ('indian_mnc',         'segment',  'Source INDIAN_MNC'),
    ('plain_combination',  'segment',  'Source Plain/Combination'),
    ('molecule_count',     'segment',  'Source Ind (number of molecules)'),
    ('dosage_form',        'segment',  'Source SHORT DESCRIPTION'),
    ('nfc1',               'segment',  'Source NFC 1 (form level 1)')
) AS t(entity_type, category, description);

CREATE OR REPLACE VIEW pack_entity AS
SELECT pfc, 'total' AS entity_type, 'TOTAL' AS entity_key, 'Total market' AS entity_label FROM pack
UNION ALL SELECT pfc, 'supergroup',    supergroup,    supergroup    FROM pack
UNION ALL SELECT pfc, 'therapy_group', therapy_group, therapy_group FROM pack
UNION ALL SELECT pfc, 'subgroup',      subgroup,      subgroup      FROM pack
UNION ALL SELECT pfc, 'molecule',      coalesce(molecule_desc, '(UNCLASSIFIED)'),
                                       coalesce(molecule_desc, '(UNCLASSIFIED)') FROM pack
UNION ALL SELECT pfc, 'company',       company,       company       FROM pack
UNION ALL SELECT pfc, 'manufacturer',  CAST(manufacturer_code AS VARCHAR), manufacturer_desc FROM pack
UNION ALL SELECT pfc, 'product',       CAST(prod_code AS VARCHAR), brand || ' (' || company || ')' FROM pack
UNION ALL SELECT pfc, 'product_subgroup', index_desc, brand || ' (' || company || ') in ' || subgroup FROM pack
UNION ALL SELECT pfc, 'acute_chronic', acute_chronic, acute_chronic FROM pack
UNION ALL SELECT pfc, 'indian_mnc',    indian_mnc,    indian_mnc    FROM pack
UNION ALL SELECT pfc, 'plain_combination', coalesce(plain_combination, '(UNCLASSIFIED)'),
                                       coalesce(plain_combination, '(UNCLASSIFIED)') FROM pack
UNION ALL SELECT pfc, 'molecule_count', coalesce(CAST(molecule_count AS VARCHAR), '(UNCLASSIFIED)'),
                                       coalesce(CAST(molecule_count AS VARCHAR), '(UNCLASSIFIED)') FROM pack
UNION ALL SELECT pfc, 'dosage_form',   form_short_desc, form_short_desc FROM pack
UNION ALL SELECT pfc, 'nfc1',          nfc1,          nfc1          FROM pack;
