"""Power BI layer (M12).

Generates a Power BI Project (PBIP: TMDL semantic model + PBIR report) over the SAME processed Parquet
used by the SQL/Python engines, exports canonical opportunity scores for import, and reconciles the
Power BI model against the validated engines through the local Analysis Services instance hosted by
Power BI Desktop. Power BI is a presentation layer; it is never a source of truth.
See docs/POWER_BI_ARCHITECTURE.md.
"""
