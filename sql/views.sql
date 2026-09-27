-- Analytical views over the Parquet layer (data/processed).
-- {processed} is substituted with the absolute processed directory by pci_data.db.connect().
-- No data is copied: every view reads Parquet directly.

-- ---------- base tables ----------
-- One row per pack (PFC). All 23 source descriptive columns + launch dates.
CREATE OR REPLACE VIEW pack AS
SELECT * FROM read_parquet('{processed}/pack.parquet');

-- One row per pack x calendar month (Jun 2021 .. May 2024). value (Rs crore), units ('000), qty ('000).
CREATE OR REPLACE VIEW fact_pack_month AS
SELECT * FROM read_parquet('{processed}/fact_pack_month.parquet');

-- One row per pack x snapshot year (May 2022/2023/2024): source-provided MONTH / YTD / MAT,
-- new-introduction MAT and SSA/HSA/DSA/TSA MAT splits. Retained for reconciliation.
CREATE OR REPLACE VIEW pack_snapshot AS
SELECT * FROM read_parquet('{processed}/pack_snapshot.parquet');

-- One row per pack x month (Dec 2023 .. May 2024): source price per pack (Rs).
CREATE OR REPLACE VIEW pack_price_month AS
SELECT * FROM read_parquet('{processed}/pack_price_month.parquet');

-- ---------- dimensions (derived; keys verified unique by tests) ----------
CREATE OR REPLACE VIEW dim_period AS
SELECT period,
       year(period)                                             AS year,
       month(period)                                            AS month,
       upper(strftime(period, '%b')) || '''' || strftime(period, '%y') AS source_label,
       CAST(row_number() OVER (ORDER BY period) AS INTEGER)     AS period_index,
       CASE WHEN month(period) <= 5 THEN year(period) ELSE year(period) + 1 END AS mat_year_ending_may
FROM (SELECT DISTINCT period FROM fact_pack_month);

-- Product (PROD_CODE). Only attributes functionally dependent on PROD_CODE.
-- NOTE: subgroup / molecule / plain_combination are NOT product-level (vary by pack).
CREATE OR REPLACE VIEW dim_product AS
SELECT prod_code,
       any_value(brand)              AS brand,
       any_value(manufacturer_code)  AS manufacturer_code,
       any_value(company)            AS company,
       any_value(prod_launch_yyyymm) AS prod_launch_yyyymm,
       any_value(prod_launch_month)  AS prod_launch_month
FROM pack GROUP BY prod_code;

-- Product within therapy subgroup; source INDEX = BRANDS : SUBGROUP : MANUFACT. DESC : PROD_CODE.
CREATE OR REPLACE VIEW dim_product_subgroup AS
SELECT index_desc, prod_code, subgroup, any_value(brand) AS brand
FROM pack GROUP BY index_desc, prod_code, subgroup;

CREATE OR REPLACE VIEW dim_manufacturer AS
SELECT manufacturer_code,
       any_value(manufacturer_desc) AS manufacturer_desc,
       any_value(company)           AS company,
       any_value(indian_mnc)        AS indian_mnc
FROM pack GROUP BY manufacturer_code;

CREATE OR REPLACE VIEW dim_company AS
SELECT company, any_value(indian_mnc) AS indian_mnc FROM pack GROUP BY company;

-- Therapy at SUBGROUP grain. GROUP is not strictly nested in SUPERGROUP (2 source exceptions),
-- so roll up to supergroup via subgroup, never via therapy_group.
CREATE OR REPLACE VIEW dim_therapy AS
SELECT subgroup,
       any_value(therapy_group) AS therapy_group,
       any_value(supergroup)    AS supergroup,
       any_value(acute_chronic) AS acute_chronic
FROM pack GROUP BY subgroup;

CREATE OR REPLACE VIEW dim_form AS
SELECT nfc, any_value(nfc1) AS nfc1, any_value(nfc2) AS nfc2, any_value(nfc3) AS nfc3,
       any_value(form_short_desc) AS form_short_desc
FROM pack GROUP BY nfc;

-- ---------- convenience analytical view ----------
CREATE OR REPLACE VIEW v_pack_month AS
SELECT f.pfc, f.period, f.value_cr, f.units_k, f.qty_k,
       p.prod_code, p.brand, p.index_desc, p.molecule_desc, p.plain_combination, p.molecule_count,
       p.manufacturer_code, p.company, p.indian_mnc,
       p.subgroup, p.therapy_group, p.supergroup, p.acute_chronic,
       p.nfc, p.nfc1, p.form_short_desc, p.pack_launch_month, p.prod_launch_month
FROM fact_pack_month f JOIN pack p USING (pfc);
