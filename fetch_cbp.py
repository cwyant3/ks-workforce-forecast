"""
fetch_cbp.py
Fetches Census County Business Patterns (CBP) establishment counts,
employment, and payroll by NAICS sector for all counties in a state.

CBP is an establishment-based dataset — new firm formation precedes hiring
in QCEW by 1–2 years, making it a leading indicator for sector labor demand.

Data source: Census Bureau API (same key as fetch_acs.py; optional but recommended)
  https://api.census.gov/data/{year}/cbp
  Sign up: https://api.census.gov/data/key_signup.html

Key API variables:
  ESTAB   — establishment count
  EMP     — mid-March employment
  PAYANN  — annual payroll ($1,000s)
  NAICS2017 (2017+) / NAICS2012 (2015–2016) — 2-digit NAICS code

Notes:
  • CBP uses a noise-infusion method for disclosure avoidance; suppressed cells
    have EMP/PAYANN replaced with ranges (stored as flags like 'a', 'b', 'c').
  • ESTAB is always published without suppression — it is the most reliable
    variable for trend analysis in small counties.
  • Annual lag: ~18 months. 2022 is the latest available as of mid-2024.

Output DataFrame columns:
  state_fips, county_fips (3-digit str), year (int),
  naics2 (str), sector (str | None),
  estab (int), emp (int | None — suppressed in rural counties),
  payann (int | None)
"""

import time
import requests
import pandas as pd
import numpy as np
from pathlib import Path

CBP_YEARS  = list(range(2015, 2023))   # 2015–2022
CBP_BASE   = "https://api.census.gov/data/{year}/cbp"

# 2-digit NAICS → dashboard sector (mirrors fetch_qcew.py SECTOR_NAICS)
NAICS2_TO_SECTOR: dict[str, str] = {
    "62": "Healthcare",
    "31": "Manufacturing",
    "32": "Manufacturing",
    "33": "Manufacturing",
    "71": "Hospitality & Entertainment",
    "72": "Hospitality & Entertainment",
    "51": "IT/Computer Services",
    "54": "IT/Computer Services",
    "22": "Skilled Trades",
    "23": "Skilled Trades",
    "81": "Skilled Trades",
}

# NAICS variable name changed in 2017
_NAICS_VAR = {year: ("NAICS2017" if year >= 2017 else "NAICS2012")
              for year in CBP_YEARS}

# Suppression flag characters CBP uses instead of numeric values
_SUPPRESSED = {"a", "b", "c", "d", "e", "f", "g", "h", "i", "j",
               "k", "l", "m", "n", "o", "p", "q", "r", "s"}


def _to_int(val: str | None) -> int | None:
    """Parse CBP numeric string; return None for suppression flags."""
    if val is None or str(val).strip().lower() in _SUPPRESSED or str(val).strip() in ("", "N"):
        return None
    try:
        return int(float(str(val).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _fetch_year(
    year: int,
    state_fips: str,
    api_key: str | None,
) -> pd.DataFrame:
    """Fetch one year of CBP county data for a state via Census API."""
    sf       = state_fips.zfill(2)
    naics_v  = _NAICS_VAR[year]
    url      = CBP_BASE.format(year=year)
    target_naics = list(NAICS2_TO_SECTOR.keys())

    params = {
        "get":  f"NAME,{naics_v},ESTAB,EMP,PAYANN",
        "for":  "county:*",
        "in":   f"state:{sf}",
        naics_v: ",".join(target_naics),
    }
    if api_key:
        params["key"] = api_key

    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    raw = resp.json()
    if not raw or len(raw) < 2:
        return pd.DataFrame()

    header = [h.lower() for h in raw[0]]
    naics_col = naics_v.lower()

    rows = []
    for record in raw[1:]:
        d = dict(zip(header, record))
        naics2 = str(d.get(naics_col, "")).zfill(2)
        rows.append({
            "state_fips":  sf,
            "county_fips": str(d.get("county", "")).zfill(3),
            "year":        year,
            "naics2":      naics2,
            "sector":      NAICS2_TO_SECTOR.get(naics2),
            "estab":       _to_int(d.get("estab")),
            "emp":         _to_int(d.get("emp")),
            "payann":      _to_int(d.get("payann")),
        })

    return pd.DataFrame(rows)


def fetch_cbp(
    state_fips: str = "20",
    years: list[int] | None = None,
    api_key: str | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch CBP establishment counts for all counties in a state.

    Parameters
    ----------
    state_fips : 2-digit state FIPS (default "20" = Kansas)
    years      : years to fetch (default 2015–2022)
    api_key    : Census API key (optional; same key as fetch_acs.py)
    cache_dir  : parquet cache directory

    Returns
    -------
    DataFrame: state_fips, county_fips, year, naics2, sector,
               estab, emp, payann
    """
    if years is None:
        years = CBP_YEARS
    sf = state_fips.zfill(2)

    cache_file = (cache_dir / f"cbp_s{sf}.parquet") if cache_dir else None
    if cache_file and cache_file.exists():
        print(f"  [cache] CBP {sf}")
        return pd.read_parquet(cache_file)

    frames = []
    for year in years:
        print(f"  CBP {year} (state {sf})…")
        try:
            df = _fetch_year(year, sf, api_key)
            if not df.empty:
                frames.append(df)
                print(f"    {len(df)} county-NAICS rows")
        except requests.HTTPError as exc:
            print(f"    Warning: CBP {year} failed ({exc.response.status_code}) — skipping")
        except Exception as exc:
            print(f"    Warning: CBP {year} error — {exc}")
        time.sleep(1.0)

    if not frames:
        return pd.DataFrame(columns=["state_fips", "county_fips", "year",
                                     "naics2", "sector", "estab", "emp", "payann"])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["county_fips", "naics2", "year"]).reset_index(drop=True)

    if cache_file:
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_file, index=False)
        print(f"  [saved] cbp_s{sf}.parquet  ({len(df)} rows)")

    return df


def compute_estab_trends(cbp_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute OLS establishment-count trend slope per (county, sector).

    Returns DataFrame with columns:
      state_fips, county_fips, sector, naics2,
      estab_slope   — annualised OLS slope (establishments per year)
      estab_pct_chg — total % change from first to last observed year
      estab_latest  — most recent year's establishment count
      year_range    — 'YYYY–YYYY'
      n_years       — number of observed years
    """
    results = []
    for (sf, county, naics2, sector), grp in cbp_df.groupby(
            ["state_fips", "county_fips", "naics2", "sector"]):
        sub = grp.dropna(subset=["estab"]).sort_values("year")
        if len(sub) < 2:
            continue

        x     = sub["year"].values.astype(float)
        y     = sub["estab"].values.astype(float)
        slope = float(np.polyfit(x - x.mean(), y, 1)[0])

        first, last = float(y[0]), float(y[-1])
        pct_chg = (last - first) / first * 100 if first else None

        results.append({
            "state_fips":  sf,
            "county_fips": county,
            "naics2":      naics2,
            "sector":      sector,
            "estab_slope": round(slope, 3),
            "estab_pct_chg": round(pct_chg, 2) if pct_chg is not None else None,
            "estab_latest": int(last),
            "year_range":  f"{int(x[0])}–{int(x[-1])}",
            "n_years":     len(sub),
        })

    return pd.DataFrame(results).sort_values(
        ["county_fips", "sector"]
    ).reset_index(drop=True)


def sector_estab_summary(cbp_df: pd.DataFrame, state_fips: str = "20") -> pd.DataFrame:
    """
    Aggregate CBP to (county_fips, year, sector) by summing NAICS 2-digit
    sub-codes within each sector, then compute trends.

    Returns trends DataFrame joined with latest establishment counts.
    """
    sf  = state_fips.zfill(2)
    sub = cbp_df[(cbp_df["state_fips"] == sf) & cbp_df["sector"].notna()].copy()

    # Sum NAICS sub-codes within sector per county-year
    agg = (
        sub.groupby(["state_fips", "county_fips", "year", "sector"], as_index=False)
        .agg(estab=("estab", "sum"), emp=("emp", "sum"))
    )
    # Treat 0 after aggregation as valid (vs. None = suppressed)
    agg["naics2"] = "sector_agg"
    return compute_estab_trends(agg)
