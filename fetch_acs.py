"""
fetch_acs.py
Fetches ACS 5-year age-by-sex (B01001) data for all counties in a given state.
Designed to be state-agnostic; defaults to Kansas (FIPS 20).

Census API key is optional but recommended for production use.
Sign up free at: https://api.census.gov/data/key_signup.html
"""

import requests
import time
import pandas as pd
from pathlib import Path

ACS_YEARS = [2015, 2019, 2021, 2023]
ACS_BASE = "https://api.census.gov/data/{year}/acs/acs5"

# B01001 — Sex by Age variable codes
MALE_VARS = [f"B01001_{str(i).zfill(3)}E" for i in range(3, 26)]   # _003–_025
FEMALE_VARS = [f"B01001_{str(i).zfill(3)}E" for i in range(27, 50)] # _027–_049
TOTAL_POP = "B01001_001E"

# Maps logical age-group names → raw ACS variable lists (male + female combined)
AGE_GROUPS: dict[str, list[str]] = {
    "under_5":  ["B01001_003E", "B01001_027E"],
    "5_9":      ["B01001_004E", "B01001_028E"],
    "10_14":    ["B01001_005E", "B01001_029E"],
    "15_17":    ["B01001_006E", "B01001_030E"],
    # Working-age cohorts
    "18_24":    ["B01001_007E", "B01001_008E", "B01001_009E", "B01001_010E",
                 "B01001_031E", "B01001_032E", "B01001_033E", "B01001_034E"],
    "25_29":    ["B01001_011E", "B01001_035E"],
    "30_34":    ["B01001_012E", "B01001_036E"],
    "35_39":    ["B01001_013E", "B01001_037E"],
    "40_44":    ["B01001_014E", "B01001_038E"],
    "45_49":    ["B01001_015E", "B01001_039E"],
    "50_54":    ["B01001_016E", "B01001_040E"],
    "55_59":    ["B01001_017E", "B01001_041E"],
    "60_64":    ["B01001_018E", "B01001_019E", "B01001_042E", "B01001_043E"],
    # Retirement-age cohorts
    "65_69":    ["B01001_020E", "B01001_021E", "B01001_044E", "B01001_045E"],
    "70_74":    ["B01001_022E", "B01001_046E"],
    "75_79":    ["B01001_023E", "B01001_047E"],
    "80_84":    ["B01001_024E", "B01001_048E"],
    "85_plus":  ["B01001_025E", "B01001_049E"],
}

WORKFORCE_GROUPS = ["18_24", "25_29", "30_34", "35_39", "40_44",
                    "45_49", "50_54", "55_59", "60_64"]
YOUTH_GROUPS     = ["under_5", "5_9", "10_14", "15_17"]
RETIREMENT_GROUPS = ["65_69", "70_74", "75_79", "80_84", "85_plus"]


def _get(url: str, params: dict, retries: int = 3) -> list:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            print(f"  [retry {attempt+1}] {exc}")
            time.sleep(3 * (attempt + 1))


def fetch_year(year: int, state_fips: str = "20",
               api_key: str | None = None,
               cache_dir: Path | None = None) -> pd.DataFrame:
    """Fetch full age distribution for all counties in state for one ACS year."""

    cache_file = (cache_dir / f"acs5_{year}_s{state_fips}.parquet") if cache_dir else None
    if cache_file and cache_file.exists():
        print(f"  [cache] {year}")
        cached = pd.read_parquet(cache_file)
        return _add_acs_metadata(cached, year, state_fips)

    base = ACS_BASE.format(year=year)
    shared = {"for": f"county:*", "in": f"state:{state_fips}"}
    if api_key:
        shared["key"] = api_key

    # Batch 1: total pop + male vars (≤50 variable limit)
    batch1 = [TOTAL_POP] + MALE_VARS
    raw1 = _get(base, {"get": "NAME," + ",".join(batch1), **shared})
    hdr1 = raw1[0]

    rows: dict[str, dict] = {}
    for row in raw1[1:]:
        fips = row[hdr1.index("county")]
        rows[fips] = {"state_fips": state_fips.zfill(2),
                      "county_fips": fips,
                      "county_name": row[hdr1.index("NAME")].split(",")[0].strip()}
        for v in batch1:
            rows[fips][v] = int(row[hdr1.index(v)] or 0)

    time.sleep(1.5)

    # Batch 2: female vars
    raw2 = _get(base, {"get": ",".join(FEMALE_VARS), **shared})
    hdr2 = raw2[0]
    for row in raw2[1:]:
        fips = row[hdr2.index("county")]
        for v in FEMALE_VARS:
            rows[fips][v] = int(row[hdr2.index(v)] or 0)

    df = pd.DataFrame(list(rows.values()))

    # Aggregate to logical age groups
    for group, vars_ in AGE_GROUPS.items():
        present = [v for v in vars_ if v in df.columns]
        df[f"pop_{group}"] = df[present].sum(axis=1)

    df["pop_working_age"]  = df[[f"pop_{g}" for g in WORKFORCE_GROUPS]].sum(axis=1)
    df["pop_youth"]        = df[[f"pop_{g}" for g in YOUTH_GROUPS]].sum(axis=1)
    df["pop_retirement"]   = df[[f"pop_{g}" for g in RETIREMENT_GROUPS]].sum(axis=1)
    df["pop_total"]        = df[TOTAL_POP]
    df["year"] = year
    df = _add_acs_metadata(df, year, state_fips)

    # Drop raw census variable columns to keep output clean
    raw_cols = [TOTAL_POP] + MALE_VARS + FEMALE_VARS
    df = df.drop(columns=[c for c in raw_cols if c in df.columns])

    if cache_file:
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_file, index=False)
        print(f"  [saved] {cache_file.name}")

    return df


def _add_acs_metadata(df: pd.DataFrame, year: int, state_fips: str) -> pd.DataFrame:
    """Attach period-estimate metadata, including for legacy cached files."""
    df = df.copy()
    df["state_fips"] = state_fips.zfill(2)
    df["acs_vintage_year"] = year
    df["acs_period_start_year"] = year - 4
    df["acs_period_end_year"] = year
    df["acs_period_midpoint_year"] = year - 2
    df["estimate_type"] = "ACS 5-year"
    df["overlapping_period_estimate"] = True
    return df


def fetch_all(state_fips: str = "20",
              api_key: str | None = None,
              cache_dir: Path | None = None) -> pd.DataFrame:
    """Fetch all ACS years and return combined DataFrame."""
    frames = []
    for year in ACS_YEARS:
        print(f"Fetching ACS 5-yr {year} (state {state_fips})…")
        df = fetch_year(year, state_fips, api_key, cache_dir)
        frames.append(df)
        time.sleep(2)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["county_fips", "year"]).reset_index(drop=True)
    return combined
