"""Synthetic dataset specification (M13, SYN-1.0.0).

Only structural properties are specified here: grain, calendar, hierarchy sizes, DQ quirks (as rates or
counts) and invented distribution parameters. No value is derived from the licensed data; structural rules
come from docs/DATA_MODEL.md and data/profile/DATA_QUALITY_BASELINE.md (committed, value-free).
Changing anything here changes the fingerprint (tests pin it): bump VERSION.
"""
from __future__ import annotations

import datetime as dt

VERSION = "SYN-1.0.0"
SEED = 20260927

# ---- calendar: identical to the processed layer (schema/grain parity) ----
FIRST_PERIOD = dt.date(2021, 6, 1)
N_PERIODS = 36                                   # Jun 2021 .. May 2024
SNAPSHOT_YEARS = (2022, 2023, 2024)              # May snapshots (MONTH / calendar YTD / MAT)
PRICE_PERIODS = 6                                # last 6 months (Dec 2023 .. May 2024)

# ---- scale (approved: ~4,000 packs x 36 months) ----
N_THERAPY_AREAS = 12
N_THERAPY_GROUPS = 40
N_SUBGROUPS = 180
N_MOLECULES = 600
N_COMPANIES = 120
N_MANUFACTURERS = 160
N_PRODUCTS = 2300
N_PACKS = 4000
N_FORMS = 30                                     # NFC-like form codes (fictional)

# ---- DQ quirks reproduced (structural; see DATA_QUALITY_BASELINE.md) ----
GROUPS_SPANNING_TWO_AREAS = 2                    # GROUP -> SUPERGROUP not nested for exactly 2 groups
SHARED_BRAND_NAME_RATE = 0.03                    # brand names reused by another company's product
BRAND_MULTI_PRODUCT_RATE = 0.04                  # brand names mapping to >1 product within a company
PRODUCT_MULTI_SUBGROUP_RATE = 0.06               # products whose packs sit in >1 subgroup
PACK_DESC_REUSE_RATE = 0.02                      # pack descriptions reused by another pack
NULL_MOLECULE_PACKS = 1                          # one pack with no molecule / plain-combination / count
PACK_LAUNCH_UNKNOWN_RATE = 0.0065                # sentinel 0 (unknown launch month)
PROD_LAUNCH_UNKNOWN_RATE = 0.0185
DORMANT_PACK_RATE = 0.085                        # all 36 months zero
LAUNCHED_IN_WINDOW_RATE = 0.14                   # products launched inside the data window
DISCONTINUED_PACK_RATE = 0.05                    # packs that stop selling inside the window
ZERO_MONTH_RATE = 0.03                           # sporadic zero months for active packs
INDIAN_COMPANY_RATE = 0.7
ACUTE_AREA_RATE = 0.6
TSA_EQUALS_MAT_RATE = 0.30                       # SSA/HSA/DSA split columns (meaning unknown, not analytical)

# ---- invented numeric distributions (NOT calibrated on real data) ----
PACK_BASE_VALUE_CR = (0.012, 1.3)                # lognormal (median, sigma) monthly value per active pack, Rs crore
PACK_PRICE_RS = (140.0, 1.0)                     # lognormal (median, sigma) price per pack, Rs
PACK_SIZES = (1, 6, 10, 10, 10, 14, 15, 20, 30, 60, 100)   # counting units per pack
MARKET_GROWTH = (0.07, 0.09)                     # normal (mean, sd) annual growth per subgroup
PRODUCT_GROWTH_SPREAD = 0.14                     # sd of product growth around its market
SEASONAL_AMPLITUDE = (0.0, 0.12)                 # uniform range of seasonal amplitude per therapy area
MONTHLY_NOISE = 0.10                             # sd of multiplicative monthly noise
ANNUAL_PRICE_INCREASE = (0.0, 0.08)              # uniform, applied each April
SUBGROUP_SIZE_SIGMA = 1.1                        # lognormal sigma of subgroup size weights
PRODUCT_WEIGHT_ALPHA = 1.2                       # Pareto alpha of product weights within a subgroup
FIRST_CODE = 1_000_000_000                       # fictional codes are 10 digits (source codes have <= 9): disjoint


def periods() -> list[dt.date]:
    out, d = [], FIRST_PERIOD
    for _ in range(N_PERIODS):
        out.append(d)
        d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def as_dict() -> dict:
    """Every parameter, for the fingerprint and the manifest."""
    return {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in globals().items()
            if k.isupper() and not k.startswith("_")}
