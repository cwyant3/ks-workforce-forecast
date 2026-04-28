"""
fetch_lodes.py
Fetches Census LEHD LODES origin-destination data to produce county-level
commute-flow metrics: where workers live vs. where they work.

Data source: Census LEHD portal (no API key required)
  https://lehd.ces.census.gov/data/lodes/LODES8/{state}/od/

Files used:
  {state}_od_main_JT00_{year}.csv.gz — all jobs, all job types
  JT01 = primary jobs only (alternative; main is broader)

Key columns in OD file:
  w_geocode : 15-char census block where worker is EMPLOYED
  h_geocode : 15-char census block where worker LIVES
  S000      : total job count for this home→work block pair

County FIPS is the first 5 characters of the geocode (SS + FFF).

Output DataFrame columns from fetch_lodes():
  state_fips (str), year (int),
  w_county_fips (str, 5-digit), h_county_fips (str, 5-digit),
  jobs (int)

Output from compute_commute_metrics():
  Adds per-county summary rows:
  county_fips (3-digit), year, total_jobs_at_worksite,
  jobs_workers_live_instate, jobs_workers_live_in_county,
  pct_workers_live_in_county (float), pct_workers_imported (float),
  top_feeder_counties (str, comma-sep 5-digit FIPS)
"""

import io
import time
import gzip
import requests
import pandas as pd
from pathlib import Path

# LODES8 years currently published (lag ~2 years from reference year)
LODES_YEARS = list(range(2015, 2022))   # 2015–2021

LODES_BASE  = "https://lehd.ces.census.gov/data/lodes/LODES8"

# State FIPS → lowercase abbreviation for constructing LODES filenames
FIPS_TO_ABBR: dict[str, str] = {
    "01": "al", "02": "ak", "04": "az", "05": "ar", "06": "ca",
    "08": "co", "09": "ct", "10": "de", "11": "dc", "12": "fl",
    "13": "ga", "15": "hi", "16": "id", "17": "il", "18": "in",
    "19": "ia", "20": "ks", "21": "ky", "22": "la", "23": "me",
    "24": "md", "25": "ma", "26": "mi", "27": "mn", "28": "ms",
    "29": "mo", "30": "mt", "31": "ne", "32": "nv", "33": "nh",
    "34": "nj", "35": "nm", "36": "ny", "37": "nc", "38": "nd",
    "39": "oh", "40": "ok", "41": "or", "42": "pa", "44": "ri",
    "45": "sc", "46": "sd", "47": "tn", "48": "tx", "49": "ut",
    "50": "vt", "51": "va", "53": "wa", "54": "wv", "55": "wi",
    "56": "wy",
}

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}


def _state_abbr(state_fips: str) -> str:
    abbr = FIPS_TO_ABBR.get(state_fips.zfill(2))
    if not abbr:
        raise ValueError(f"No LODES abbreviation for state FIPS '{state_fips}'")
    return abbr


def _od_url(state_abbr: str, year: int, job_type: str = "JT00") -> str:
    return (f"{LODES_BASE}/{state_abbr}/od/"
            f"{state_abbr}_od_main_{job_type}_{year}.csv.gz")


def _download_od(state_abbr: str, year: int) -> pd.DataFrame:
    """Download and parse one LODES OD CSV.gz file."""
    url = _od_url(state_abbr, year)
    print(f"    Downloading LODES {state_abbr} {year}…")
    resp = requests.get(url, headers=_HTTP_HEADERS, timeout=300, stream=True)
    resp.raise_for_status()
    chunks = []
    for chunk in resp.iter_content(1024 * 256):
        chunks.append(chunk)
    raw_bytes = b"".join(chunks)

    with gzip.open(io.BytesIO(raw_bytes)) as f:
        df = pd.read_csv(f, dtype=str, usecols=["w_geocode", "h_geocode", "S000"])

    df["S000"] = pd.to_numeric(df["S000"], errors="coerce").fillna(0).astype(int)
    return df


def _aggregate_to_county(od_df: pd.DataFrame, state_fips: str, year: int) -> pd.DataFrame:
    """
    Collapse block-level OD pairs to county-county flows.
    Only keeps flows where the work county is in the target state.
    """
    sf = state_fips.zfill(2)
    od = od_df.copy()
    od["w_county_fips"] = od["w_geocode"].str[:5]
    od["h_county_fips"] = od["h_geocode"].str[:5]

    # Keep only rows where workers work in our target state
    od = od[od["w_county_fips"].str[:2] == sf]

    county_flows = (
        od.groupby(["w_county_fips", "h_county_fips"], as_index=False)["S000"]
        .sum()
        .rename(columns={"S000": "jobs"})
    )
    county_flows["state_fips"] = sf
    county_flows["year"]       = year
    return county_flows


def fetch_lodes(
    state_fips: str = "20",
    years: list[int] | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch LODES OD data for a state and return county-to-county flow table.

    Parameters
    ----------
    state_fips : 2-digit state FIPS (default "20" = Kansas)
    years      : list of years to fetch (default 2015–2021)
    cache_dir  : parquet cache directory

    Returns
    -------
    DataFrame: state_fips, year, w_county_fips, h_county_fips, jobs
    """
    if years is None:
        years = LODES_YEARS
    sf   = state_fips.zfill(2)
    abbr = _state_abbr(sf)

    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    combined_cache = cache_dir / f"lodes_s{sf}_all.parquet"
    if combined_cache.exists():
        print(f"  [cache] LODES {sf} (combined)")
        return pd.read_parquet(combined_cache)

    frames = []
    for year in years:
        year_cache = cache_dir / f"lodes_s{sf}_{year}.parquet"
        if year_cache.exists():
            print(f"  [cache] LODES {sf} {year}")
            frames.append(pd.read_parquet(year_cache))
            continue

        try:
            od_raw = _download_od(abbr, year)
            year_df = _aggregate_to_county(od_raw, sf, year)
            year_df.to_parquet(year_cache, index=False)
            print(f"    [saved] lodes_s{sf}_{year}.parquet  ({len(year_df)} county-pair rows)")
            frames.append(year_df)
        except requests.HTTPError as exc:
            if exc.response.status_code == 404:
                print(f"    Warning: LODES {sf} {year} not available (404) — skipping")
            else:
                print(f"    Warning: LODES {sf} {year} failed — {exc}")
        time.sleep(1.5)

    if not frames:
        print("  Warning: no LODES data loaded")
        return pd.DataFrame(columns=[
            "state_fips", "year", "w_county_fips", "h_county_fips", "jobs"
        ])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["year", "w_county_fips"]).reset_index(drop=True)
    df.to_parquet(combined_cache, index=False)
    print(f"  [saved] lodes_s{sf}_all.parquet  ({len(df)} rows, {df['year'].nunique()} years)")
    return df


def compute_commute_metrics(
    lodes_df: pd.DataFrame,
    state_fips: str = "20",
    top_n_feeders: int = 3,
) -> pd.DataFrame:
    """
    Aggregate county-to-county flows into per-county commute metrics.

    For each work county:
      - total_jobs_at_worksite          : all workers regardless of home location
      - jobs_workers_live_in_county     : workers who both live and work in county
      - jobs_workers_imported           : workers commuting in from outside county
      - pct_workers_live_in_county      : share of local workers (0–100)
      - pct_workers_imported            : share commuting in (0–100)
      - top_feeder_counties             : top_n home counties (by job count), comma-separated

    Returns DataFrame indexed by (county_fips [3-digit], year).
    """
    sf = state_fips.zfill(2)
    df = lodes_df[lodes_df["state_fips"] == sf].copy()

    if df.empty:
        return pd.DataFrame(columns=[
            "state_fips", "county_fips", "year",
            "total_jobs_at_worksite", "jobs_workers_live_in_county",
            "jobs_workers_imported", "pct_workers_live_in_county",
            "pct_workers_imported", "top_feeder_counties",
        ])

    results = []
    for (w_county, year), grp in df.groupby(["w_county_fips", "year"]):
        total = int(grp["jobs"].sum())
        live_in = int(grp.loc[grp["h_county_fips"] == w_county, "jobs"].sum())
        imported = total - live_in

        # Top feeder counties (home counties outside the work county, by job count)
        feeders = (
            grp[grp["h_county_fips"] != w_county]
            .sort_values("jobs", ascending=False)
            .head(top_n_feeders)["h_county_fips"]
            .tolist()
        )

        results.append({
            "state_fips":               sf,
            "county_fips":              w_county[-3:],   # 3-digit suffix
            "w_county_fips":            w_county,
            "year":                     year,
            "total_jobs_at_worksite":   total,
            "jobs_workers_live_in_county": live_in,
            "jobs_workers_imported":    imported,
            "pct_workers_live_in_county": round(live_in / total * 100, 2) if total else 0.0,
            "pct_workers_imported":     round(imported / total * 100, 2) if total else 0.0,
            "top_feeder_counties":      ",".join(feeders),
        })

    out = pd.DataFrame(results).sort_values(["county_fips", "year"]).reset_index(drop=True)
    return out


def latest_commute_snapshot(
    lodes_df: pd.DataFrame,
    state_fips: str = "20",
) -> pd.DataFrame:
    """
    Return commute metrics for only the most recent available year.
    Useful for joining into the county summary table.
    """
    sf  = state_fips.zfill(2)
    sub = lodes_df[lodes_df["state_fips"] == sf]
    if sub.empty:
        return pd.DataFrame()
    latest_year = sub["year"].max()
    metrics = compute_commute_metrics(lodes_df, sf)
    return metrics[metrics["year"] == latest_year].copy()
