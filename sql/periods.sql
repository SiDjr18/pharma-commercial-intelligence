-- Time logic (M4). Depends on views.sql (dim_period).
-- One row per (anchor month, basis). Bases reproduce the source's own definitions:
--   MONTH : the anchor month                                   (source "MONTH MAY'YY")
--   YTD   : Jan .. anchor month of the anchor's calendar year  (source "CUMM MAY'YY" = calendar YTD, NOT Indian FY)
--   MAT   : 12 months ending at the anchor month               (source "MAT MAY'YY")
-- Comparison window = the same window shifted back 12 months (source pivot growth fields compare
-- MAY'24 vs MAY'23 for MONTH, CUMM and MAT).
-- A window is "complete" only if every month in it exists in the data (data are gap-free from
-- 2021-06 to 2024-05, verified by M3 tests), i.e. window start >= first available month.

CREATE OR REPLACE VIEW period_basis AS
WITH bounds AS (SELECT min(period) AS first_period, max(period) AS last_period FROM dim_period),
anchors AS (
    SELECT d.period AS anchor, b.basis, b.basis_label
    FROM dim_period d
    CROSS JOIN (VALUES ('MONTH', 'Month'), ('YTD', 'Calendar YTD'), ('MAT', 'MAT (moving annual total)'))
               AS b(basis, basis_label)
),
w AS (
    SELECT anchor, basis, basis_label,
           CAST(CASE basis WHEN 'MONTH' THEN anchor
                           WHEN 'YTD'   THEN make_date(year(anchor), 1, 1)
                           ELSE anchor - INTERVAL 11 MONTH END AS DATE) AS cur_start,
           anchor AS cur_end
    FROM anchors
)
SELECT w.anchor, w.basis, w.basis_label,
       w.cur_start, w.cur_end,
       CAST(w.cur_start - INTERVAL 12 MONTH AS DATE) AS prior_start,
       CAST(w.cur_end   - INTERVAL 12 MONTH AS DATE) AS prior_end,
       CAST(datediff('month', w.cur_start, w.cur_end) + 1 AS INTEGER) AS n_months,
       w.cur_start >= bnd.first_period                                AS current_complete,
       CAST(w.cur_start - INTERVAL 12 MONTH AS DATE) >= bnd.first_period AS prior_complete
FROM w CROSS JOIN bounds bnd;
