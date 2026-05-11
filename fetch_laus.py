"""
fetch_laus.py
Fetches BLS Local Area Unemployment Statistics (LAUS) for county-level
labor force, employment, unemployment, and unemployment rate.
Designed to be state-agnostic; defaults to Kansas (FIPS 20).

Data source: BLS public API v2
  https://api.bls.gov/publicAPI/v2/timeseries/data/

Series ID format: LAUCN{SSFFF}00000000{MM}
  SS  = state FIPS (2 digits)
  FFF = county FIPS (3 digits)
  00000000 = 8 zero padding digits
  MM  = measure code: 03=unemployment rate, 04=unemployment count,
                       05=employment count, 06=labor force count
  Total length: 20 characters (LAUCN=5 + SS=2 + FFF=3 + zeros=8 + MM=2)

BLS API keys are free: https://data.bls.gov/registrationEngine/
Without a key: 25 series/request, 10-year history.
With a key:    50 series/request, 20-year history.

Output DataFrame columns:
  state_fips, county_fips (3-digit str), year (int),
  labor_force, employed, unemployed (int counts),
  unemployment_rate (float, percent)
"""

import time
import requests
import pandas as pd
from pathlib import Path

LAUS_YEARS   = list(range(2015, 2024))       # 2015–2023 annual averages
BLS_API_URL  = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Measure suffix → output column name
MEASURES: dict[str, str] = {
    "06": "labor_force",
    "05": "employed",
    "04": "unemployed",
    "03": "unemployment_rate",
}

_HTTP_HEADERS = {"Content-Type": "application/json"}


def _build_series_ids(state_fips: str, county_fips3_list: list[str]) -> list[str]:
    """Return LAUS series IDs for all counties × all four measures."""
    sf = state_fips.zfill(2)
    ids = []
    for cf in county_fips3_list:
        fips5 = sf + cf.zfill(3)
        for code in MEASURES:
            ids.append(f"LAUCN{fips5}00000000{code}")
    return ids


def _post_bls(
    series_ids: list[str],
    start_year: int,
    end_year: int,
    api_key: str | None,
) -> list[dict]:
    """POST one batch of series IDs to BLS API v2; return raw series list."""
    payload: dict = {
        "seriesid":    series_ids,
        "startyear":   str(start_year),
        "endyear":     str(end_year),
        "annualaverage": True,
    }
    if api_key:
        payload["registrationkey"] = api_key

    resp = requests.post(BLS_API_URL, json=payload, headers=_HTTP_HEADERS, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status", "")
    if status != "REQUEST_SUCCEEDED":
        msgs = data.get("message", [])
        raise RuntimeError(f"BLS API error ({status}): {msgs}")

    return data.get("Results", {}).get("series", [])


def _parse_series(series_list: list[dict], state_fips: str) -> list[dict]:
    """
    Parse BLS series list into flat row dicts keyed by (county_fips, year).
    Annual averages have period 'M13'.
    """
    sf = state_fips.zfill(2)
    # Accumulate: {(county_fips3, year): {col: value}}
    rows: dict[tuple, dict] = {}

    for series in series_list:
        sid = series.get("seriesID", "")
        # Series ID: LAUCN{SF2}{CF3}00000000{MM2} — total 20 chars
        # Positions: 0-4=LAUCN, 5-6=state, 7-9=county, 10-17=zeros, 18-19=measure
        if len(sid) != 20 or not sid.startswith("LAUCN"):
            continue
        county_fips3 = sid[7:10]
        measure_code = sid[18:20]
        col_name = MEASURES.get(measure_code)
        if col_name is None:
            continue

        for obs in series.get("data", []):
            if obs.get("period") != "M13":   # annual average only
                continue
            year = int(obs["year"])
            key  = (county_fips3, year)
            if key not in rows:
                rows[key] = {
                    "state_fips":  sf,
                    "county_fips": county_fips3,
                    "year":        year,
                }
            val_str = obs.get("value", "").replace(",", "").strip()
            try:
                rows[key][col_name] = float(val_str)
            except ValueError:
                rows[key][col_name] = None

    return list(rows.values())


def fetch_laus(
    state_fips: str = "20",
    county_fips3_list: list[str] | None = None,
    years: list[int] | None = None,
    api_key: str | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch LAUS annual-average labor force statistics for all counties.

    Parameters
    ----------
    state_fips        : 2-digit state FIPS (default "20" = Kansas)
    county_fips3_list : 3-digit county FIPS strings; if None, fetched for
                        all counties the API returns for the state
    years             : list of years to fetch (default 2015–2023)
    api_key           : BLS API key (optional; raises rate limit without one)
    cache_dir         : parquet cache directory

    Returns
    -------
    DataFrame with columns: state_fips, county_fips, year, labor_force,
    employed, unemployed, unemployment_rate
    """
    if years is None:
        years = LAUS_YEARS
    sf = state_fips.zfill(2)

    cache_file = (cache_dir / f"laus_s{sf}.parquet") if cache_dir else None
    if cache_file and cache_file.exists():
        print(f"  [cache] LAUS {sf}")
        return pd.read_parquet(cache_file)

    if county_fips3_list is None:
        raise ValueError("county_fips3_list required when no cache exists")

    all_ids = _build_series_ids(sf, county_fips3_list)

    # BLS API limit: 50 series/request with key, 25 without
    batch_size = 50 if api_key else 25
    start_year = min(years)
    end_year   = max(years)

    # BLS API only supports 20-year windows (with key) or 10-year (without)
    max_window = 20 if api_key else 10
    year_batches: list[tuple[int, int]] = []
    y = start_year
    while y <= end_year:
        y_end = min(y + max_window - 1, end_year)
        year_batches.append((y, y_end))
        y = y_end + 1

    all_rows: list[dict] = []
    _rate_limited = False

    for y_start, y_end in year_batches:
        for i in range(0, len(all_ids), batch_size):
            batch = all_ids[i : i + batch_size]
            print(f"  [LAUS] fetching series {i+1}–{i+len(batch)} "
                  f"of {len(all_ids)} ({y_start}–{y_end})…")
            try:
                series_list = _post_bls(batch, y_start, y_end, api_key)
                all_rows.extend(_parse_series(series_list, sf))
            except RuntimeError as exc:
                msg = str(exc)
                if "daily threshold" in msg or "daily limit" in msg.lower() or "threshold" in msg:
                    _rate_limited = True
                    print(
                        "\n  *** BLS daily request limit reached ***\n"
                        "  LAUS fetch aborted. Options:\n"
                        "  1. Register for a free BLS API key at https://data.bls.gov/registrationEngine/\n"
                        "     then re-run with:  BLS_API_KEY=<key> python run_forecast.py --state 20 --laus\n"
                        "  2. Wait until tomorrow (anonymous limit resets daily).\n"
                    )
                    break
                print(f"    Warning: batch failed — {exc}")
            except Exception as exc:
                print(f"    Warning: batch failed — {exc}")
            time.sleep(1.5)   # BLS rate limit courtesy
        if _rate_limited:
            break

    if not all_rows:
        if not _rate_limited:
            print("  Warning: no LAUS data returned")
        return pd.DataFrame(columns=["state_fips", "county_fips", "year",
                                     "labor_force", "employed", "unemployed",
                                     "unemployment_rate"])

    df = pd.DataFrame(all_rows)

    # Coerce counts to integer where possible
    for col in ("labor_force", "employed", "unemployed"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(0).astype("Int64")
    if "unemployment_rate" in df.columns:
        df["unemployment_rate"] = pd.to_numeric(df["unemployment_rate"], errors="coerce")

    # Filter to requested years
    df = df[df["year"].isin(years)].copy()
    df = df.sort_values(["county_fips", "year"]).reset_index(drop=True)

    if cache_file:
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_file, index=False)
        print(f"  [saved] {cache_file.name}  ({len(df)} rows)")

    return df


def compute_lfpr(
    laus_df: pd.DataFrame,
    acs_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join LAUS labor force counts with ACS labor-force-status data.

    Preferred LFPR = ACS civilian labor force 18-64 / ACS civilian labor-force
    status population 18-64. This keeps numerator and denominator in the same
    ACS universe. If legacy ACS files do not have B23001 fields, the function
    falls back to the older LAUS labor_force / ACS working-age population proxy.

    ACS years are mapped to the nearest LAUS year using forward-fill so the
    5-year estimate midpoint is used as the population denominator.

    Returns laus_df with added ACS denominator columns, lfpr_pct, lfpr_source,
    and effective_workforce_lfpr.
    """
    # Use ACS midpoint year as the denominator anchor
    acs_cols = ["county_fips", "acs_period_midpoint_year", "pop_working_age"]
    for col in [
        "acs_lf_status_pop_18_64",
        "acs_civilian_labor_force_18_64",
        "acs_armed_forces_18_64",
        "acs_lfpr_pct",
    ]:
        if col in acs_df.columns:
            acs_cols.append(col)
    acs_pop = acs_df[acs_cols].copy()
    acs_pop = acs_pop.rename(columns={"acs_period_midpoint_year": "year"})

    # For each LAUS year, find the nearest ACS vintage (forward/backward fill)
    acs_years = sorted(acs_pop["year"].unique())

    def _nearest_acs_year(y: int) -> int:
        return min(acs_years, key=lambda a: abs(a - y))

    laus = laus_df.copy()
    laus["_acs_year"] = laus["year"].apply(_nearest_acs_year)

    merged = laus.merge(
        acs_pop.rename(columns={"year": "_acs_year"}),
        on=["county_fips", "_acs_year"],
        how="left",
    ).drop(columns=["_acs_year"])

    if "acs_lfpr_pct" in merged.columns and merged["acs_lfpr_pct"].notna().any():
        merged["lfpr_pct"] = merged["acs_lfpr_pct"]
        merged["lfpr_source"] = "ACS_B23001_civilian_18_64"
    else:
        merged["lfpr_pct"] = (
            merged["labor_force"] / merged["pop_working_age"] * 100
        ).round(2)
        merged["lfpr_source"] = "LAUS_labor_force_over_ACS_18_64_proxy"

    # Clamp LFPR to [0, 100] — rounding or suppression artifacts can produce outliers
    merged["lfpr_pct"] = merged["lfpr_pct"].clip(0, 100)

    merged["effective_workforce_lfpr"] = (
        merged["pop_working_age"] * merged["lfpr_pct"] / 100
    ).round(0).astype("Int64")

    return merged
