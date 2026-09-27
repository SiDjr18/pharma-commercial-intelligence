-- Generic metric engine (M4). Depends on views.sql, periods.sql, entities.sql.
-- All domain macros (domains.sql) are thin wrappers around these two macros.
--
-- entity_period(p_type, p_anchor, p_basis, p_scope_type, p_scope_key)
--   One row per entity of type p_type, restricted to packs in the scope entity
--   (default scope = total market). Current and comparison windows from period_basis.
--   Returns nothing if the current window is incomplete (e.g. MAT before 2022-05).
--
-- Growth (reproduces the source pivot calculated fields, e.g. VAL GRTH MAT 24 =
--   'MAT MAY''24'/'MAT MAY''23'*100-100, evaluated on aggregated sums):
--   growth_pct = cur / prior * 100 - 100, only when prior > 0; otherwise NULL (never 0).
--   growth_status: ok | prior_zero (cur > 0, prior = 0) | no_sales (both 0) | prior_unavailable
-- Share: entity cur / scope total cur * 100 (partition => shares sum to 100).
-- Contribution to growth (pp): (cur - prior) / scope total prior * 100; sums to scope growth.
-- Evolution index: share_cur / share_prior * 100 = 100 * (1 + g_entity) / (1 + g_scope).
-- Ranks: row_number over value rounded to 1e-9 crore (growth ratio to 1e-9), deterministic;
--   ties broken by entity_key ascending (byte order).

CREATE OR REPLACE MACRO entity_period(p_type, p_anchor, p_basis,
                                      p_scope_type := 'total', p_scope_key := 'TOTAL') AS TABLE
WITH pb AS (
    SELECT * FROM period_basis
    WHERE anchor = CAST(p_anchor AS DATE) AND basis = upper(p_basis) AND current_complete
),
scope AS (
    SELECT pfc FROM pack_entity
    WHERE entity_type = p_scope_type AND entity_key = CAST(p_scope_key AS VARCHAR)
),
pf AS (  -- aggregate to pack first (2.5M -> 105k rows), then attach entities
    SELECT f.pfc,
           sum(f.value_cr) FILTER (WHERE f.period BETWEEN pb.cur_start AND pb.cur_end)     AS value_cur,
           sum(f.value_cr) FILTER (WHERE f.period BETWEEN pb.prior_start AND pb.prior_end) AS value_prior_raw,
           sum(f.units_k)  FILTER (WHERE f.period BETWEEN pb.cur_start AND pb.cur_end)     AS units_cur,
           sum(f.units_k)  FILTER (WHERE f.period BETWEEN pb.prior_start AND pb.prior_end) AS units_prior_raw,
           sum(f.qty_k)    FILTER (WHERE f.period BETWEEN pb.cur_start AND pb.cur_end)     AS qty_cur,
           sum(f.qty_k)    FILTER (WHERE f.period BETWEEN pb.prior_start AND pb.prior_end) AS qty_prior_raw
    FROM fact_pack_month f CROSS JOIN pb
    WHERE f.period BETWEEN pb.prior_start AND pb.cur_end
    GROUP BY f.pfc
),
agg AS (
    SELECT e.entity_key,
           any_value(e.entity_label) AS entity_label,
           sum(pf.value_cur) AS value_cur, sum(pf.value_prior_raw) AS value_prior_raw,
           sum(pf.units_cur) AS units_cur, sum(pf.units_prior_raw) AS units_prior_raw,
           sum(pf.qty_cur)   AS qty_cur,   sum(pf.qty_prior_raw)   AS qty_prior_raw,
           count(*) AS n_packs
    FROM pf
    JOIN scope s ON s.pfc = pf.pfc
    JOIN pack_entity e ON e.pfc = pf.pfc AND e.entity_type = p_type
    GROUP BY e.entity_key
),
a AS (
    SELECT agg.* EXCLUDE (value_prior_raw, units_prior_raw, qty_prior_raw),
           CASE WHEN pb.prior_complete THEN coalesce(value_prior_raw, 0) END AS value_prior,
           CASE WHEN pb.prior_complete THEN coalesce(units_prior_raw, 0) END AS units_prior,
           CASE WHEN pb.prior_complete THEN coalesce(qty_prior_raw, 0)   END AS qty_prior
    FROM agg CROSS JOIN pb
),
tot AS (
    SELECT sum(value_cur) AS t_value_cur, sum(value_prior) AS t_value_prior,
           sum(units_cur) AS t_units_cur, sum(units_prior) AS t_units_prior
    FROM a
)
SELECT
    p_type AS entity_type, a.entity_key, a.entity_label,
    p_scope_type AS scope_type, CAST(p_scope_key AS VARCHAR) AS scope_key,
    pb.anchor, pb.basis, pb.basis_label, pb.cur_start, pb.cur_end,
    CASE WHEN pb.prior_complete THEN pb.prior_start END AS prior_start,
    CASE WHEN pb.prior_complete THEN pb.prior_end END AS prior_end,
    a.n_packs,
    -- value (Rs crore)
    a.value_cur, a.value_prior,
    a.value_cur - a.value_prior AS value_abs_chg,
    CASE WHEN a.value_prior > 0 THEN a.value_cur / a.value_prior * 100 - 100 END AS value_growth_pct,
    CASE WHEN a.value_prior IS NULL THEN 'prior_unavailable'
         WHEN a.value_prior = 0 AND a.value_cur = 0 THEN 'no_sales'
         WHEN a.value_prior = 0 THEN 'prior_zero'
         ELSE 'ok' END AS value_growth_status,
    -- units ('000)
    a.units_cur, a.units_prior,
    a.units_cur - a.units_prior AS units_abs_chg,
    CASE WHEN a.units_prior > 0 THEN a.units_cur / a.units_prior * 100 - 100 END AS units_growth_pct,
    -- qty ('000 counting units)
    a.qty_cur, a.qty_prior,
    CASE WHEN a.qty_prior > 0 THEN a.qty_cur / a.qty_prior * 100 - 100 END AS qty_growth_pct,
    -- share / contribution within scope
    tot.t_value_cur AS scope_value_cur, tot.t_value_prior AS scope_value_prior,
    CASE WHEN tot.t_value_cur > 0 THEN a.value_cur / tot.t_value_cur * 100 END AS value_share_pct,
    CASE WHEN tot.t_value_prior > 0 THEN a.value_prior / tot.t_value_prior * 100 END AS value_share_prior_pct,
    CASE WHEN tot.t_value_cur > 0 AND tot.t_value_prior > 0
         THEN a.value_cur / tot.t_value_cur * 100 - a.value_prior / tot.t_value_prior * 100 END AS value_share_chg_pp,
    CASE WHEN tot.t_units_cur > 0 THEN a.units_cur / tot.t_units_cur * 100 END AS units_share_pct,
    CASE WHEN tot.t_value_prior > 0 THEN (a.value_cur - a.value_prior) / tot.t_value_prior * 100 END
        AS contribution_to_growth_pp,
    CASE WHEN a.value_prior > 0 AND tot.t_value_prior > 0 AND tot.t_value_cur > 0
         THEN (a.value_cur / tot.t_value_cur) / (a.value_prior / tot.t_value_prior) * 100 END AS evolution_index,
    -- deterministic ranks
    -- ranking keys are rounded (value to 1e-9 crore = Rs 0.01; growth ratio to 1e-9) so that
    -- floating-point summation order (parallel aggregation) can never flip a tie.
    CAST(row_number() OVER (ORDER BY round(a.value_cur, 9) DESC, a.entity_key) AS INTEGER) AS rank_value,
    CAST(row_number() OVER (ORDER BY round(CASE WHEN a.value_prior > 0 THEN a.value_cur / a.value_prior END, 9) DESC NULLS LAST,
                                     round(a.value_cur, 9) DESC, a.entity_key) AS INTEGER) AS rank_growth,
    CAST(count(*) OVER () AS INTEGER) AS n_entities
FROM a CROSS JOIN tot CROSS JOIN pb;


-- entity_trend(p_type, p_key, p_scope_type, p_scope_key)
--   Dense monthly series (all 36 months, zero-filled) for ONE entity, with the three bases
--   evaluated at every month: month YoY, calendar YTD and MAT, each with prior-year value and
--   growth. Values that need unavailable months are NULL. Empty if the key does not exist.
CREATE OR REPLACE MACRO entity_trend(p_type, p_key, p_scope_type := 'total', p_scope_key := 'TOTAL') AS TABLE
WITH members AS (
    SELECT e.pfc FROM pack_entity e
    WHERE e.entity_type = p_type AND e.entity_key = CAST(p_key AS VARCHAR)
      AND e.pfc IN (SELECT pfc FROM pack_entity
                    WHERE entity_type = p_scope_type AND entity_key = CAST(p_scope_key AS VARCHAR))
),
m AS (
    SELECT f.period, sum(f.value_cr) AS v, sum(f.units_k) AS u
    FROM fact_pack_month f JOIN members USING (pfc) GROUP BY f.period
),
d AS (
    SELECT p.period, p.period_index, p.year,
           coalesce(m.v, 0) AS value_cr, coalesce(m.u, 0) AS units_k
    FROM dim_period p LEFT JOIN m USING (period)
    WHERE EXISTS (SELECT 1 FROM members)
),
r AS (
    SELECT d.*,
           CASE WHEN period_index >= 12 THEN sum(value_cr) OVER w12 END AS value_mat,
           CASE WHEN period_index >= 12 THEN sum(units_k) OVER w12 END AS units_mat,
           CASE WHEN year > (SELECT min(year) FROM dim_period)
                  OR (SELECT month(min(period)) FROM dim_period) = 1
                THEN sum(value_cr) OVER (PARTITION BY year ORDER BY period) END AS value_ytd
    FROM d
    WINDOW w12 AS (ORDER BY period ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
),
y AS (
    SELECT r.*,
           lag(value_cr, 12)  OVER (ORDER BY period) AS value_cr_prior,
           lag(units_k, 12)   OVER (ORDER BY period) AS units_k_prior,
           lag(value_ytd, 12) OVER (ORDER BY period) AS value_ytd_prior,
           lag(value_mat, 12) OVER (ORDER BY period) AS value_mat_prior,
           lag(units_mat, 12) OVER (ORDER BY period) AS units_mat_prior
    FROM r
)
SELECT p_type AS entity_type, CAST(p_key AS VARCHAR) AS entity_key,
       p_scope_type AS scope_type, CAST(p_scope_key AS VARCHAR) AS scope_key,
       period, period_index,
       value_cr, value_cr_prior,
       CASE WHEN value_cr_prior > 0 THEN value_cr / value_cr_prior * 100 - 100 END AS value_growth_pct,
       units_k, units_k_prior,
       CASE WHEN units_k_prior > 0 THEN units_k / units_k_prior * 100 - 100 END AS units_growth_pct,
       value_ytd, value_ytd_prior,
       CASE WHEN value_ytd_prior > 0 THEN value_ytd / value_ytd_prior * 100 - 100 END AS value_ytd_growth_pct,
       value_mat, value_mat_prior,
       CASE WHEN value_mat_prior > 0 THEN value_mat / value_mat_prior * 100 - 100 END AS value_mat_growth_pct,
       units_mat, units_mat_prior,
       CASE WHEN units_mat_prior > 0 THEN units_mat / units_mat_prior * 100 - 100 END AS units_mat_growth_pct
FROM y ORDER BY period;
