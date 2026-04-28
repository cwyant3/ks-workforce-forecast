"""
fetch_jolts.py
Fetches BLS Job Openings and Labor Turnover Survey (JOLTS) data at the
state level, mapped to the five dashboard workforce sectors.

JOLTS is the only source of vacancy (job openings) rates, which convert the
supply-vs-employment comparison into supply-vs-demand-pressure. A sector with
8% vacancy and a declining working-age population is in acute shortage; a
sector with 1% vacancy is absorbing available labor.

IMPORTANT — geographic limitation:
  JOLTS is published at the NATIONAL level only (no state or county breakdown).
  "State-level" usage means the national supersector vacancy rate is used as
  a uniform proxy scaled by county sector share. This is documented in the
  dashboard output with a clear caveat.

Data source: BLS public API v2 (same key as fetch_laus.py)
  https://api.bls.gov/publicAPI/v2/timeseries/data/
  BLS API keys: https://data.bls.gov/registrationEngine/

JOLTS Series ID format: JT{U|S}{8-digit-industry}{data-element}{L|R}
  U/S = seasonally unadjusted / seasonally adjusted
  data-element: JO=openings, HI=hires, QU=quits, LD=layoffs, TS=total separations
  L/R = level (thousands) / rate (percent)

JOLTS supersector → 8-digit industry codes used in series IDs:
  00000000 = Total nonfarm
  20000000 = Construction               → Skilled Trades
  30000000 = Manufacturing              → Manufacturing
  55000000 = Financial activities       (not used)
  60000000 = Professional/business svc  → IT/Computer Services
  65000000 = Education & health svc     → Healthcare
  70000000 = Leisure & hospitality      → Hospitality & Entertainment

NOTE: Information (NAICS 51) is not published as a separate JOLTS supersector;
it rolls into 60000000 (Professional/business services). This is a known
JOLTS limitation — documented in output.

Output DataFrame columns from fetch_jolts():
  year (int), month (int), sector (str),
  industry_code (str), data_element (str),
  level (float | None), rate (float | None)

Output from compute_vacancy_rates():
  year, sector, openings_level, employment_proxy,
  vacancy_rate_pct, vacancy_rate_trend_slope
"""

import time
import requests
import pandas as pd
import numpy as np
from pathlib import Path

JOLTS_YEARS  = list(range(2015, 2024))   # 2015–2023
BLS_API_URL  = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# JOLTS supersector 8-digit codes → dashboard sector
# Source: BLS JOLTS technical notes
JOLTS_INDUSTRY: dict[str, str] = {
    "20000000": "Skilled Trades",              # Construction
    "30000000": "Manufacturing",               # Manufacturing (31-33)
    "60000000": "IT/Computer Services",        # Professional & business services (54+55+56)
    "65000000": "Healthcare",                  # Education & health services (61+62)
    "70000000": "Hospitality & Entertainment", # Leisure & hospitality (71+72)
}

# Data elements to fetch
DATA_ELEMENTS: dict[str, str] = {
    "JO": "openings",
    "HI": "hires",
    "TS": "total_separations",
}

_HTTP_HEADERS = {"Content-Type": "application/json"}


def _build_series_ids(seasonal: str = "U") -> list[str]:
    """
    Build JOLTS series IDs for all sector×data-element combinations.
    seasonal="U" for not seasonally adjusted (consistent with other modules).
    """
    ids = []
    for ind_code in JOLTS_INDUSTRY:
        for elem_code in DATA_ELEMENTS:
            # Level series (thousands)
            ids.append(f"JT{seasonal}{ind_code}{elem_code}L")
            # Rate series (percent)
            ids.append(f"JT{seasonal}{ind_code}{elem_code}R")
    # Also fetch total nonfarm openings level for scale reference
    ids.append(f"JT{seasonal}00000000JOL")
    return ids


def _post_bls(
    series_ids: list[str],
    start_year: int,
    end_year: int,
    api_key: str | None,
) -> list[dict]:
    """POST batch to BLS API v2; return raw series list."""
    payload: dict = {
        "seriesid":  series_ids,
        "startyear": str(start_year),
        "endyear":   str(end_year),
    }
    if api_key:
        payload["registrationkey"] = api_key

    resp = requests.post(BLS_API_URL, json=payload, headers=_HTTP_HEADERS, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "REQUEST_SUCCEEDED":
        msgs = data.get("message", [])
        raise RuntimeError(f"BLS API error: {msgs}")
    return data.get("Results", {}).get("series", [])


def _parse_jolts(series_list: list[dict]) -> list[dict]:
    """Parse raw BLS JOLTS series into flat row dicts."""
    rows = []
    for series in series_list:
        sid = series.get("seriesID", "")
        # JT{S1}{industry8}{elem2}{rate1} → total len = 2+1+8+2+1 = 14
        if len(sid) != 14 or not sid.startswith("JT"):
            continue
        seasonal    = sid[2]
        ind_code    = sid[3:11]
        elem_code   = sid[11:13]
        rate_level  = sid[13]
        sector      = JOLTS_INDUSTRY.get(ind_code, "Total" if ind_code == "00000000" else None)
        col_suffix  = DATA_ELEMENTS.get(elem_code, elem_code.lower())
        col_type    = "rate" if rate_level == "R" else "level"

        for obs in series.get("data", []):
            period = obs.get("period", "")
            if not period.startswith("M") or period == "M13":
                continue   # JOLTS is monthly; skip annual averages if any
            try:
                month = int(period[1:])
            except ValueError:
                continue
            val_str = obs.get("value", "").replace(",", "").strip()
            try:
                value = float(val_str)
            except ValueError:
                value = None

            rows.append({
                "year":         int(obs["year"]),
                "month":        month,
                "sector":       sector,
                "industry_code": ind_code,
                "data_element": col_suffix,
                "measure":      col_type,
                "value":        value,
                "seasonal":     seasonal,
            })
    return rows


def fetch_jolts(
    years: list[int] | None = None,
    api_key: str | None = None,
    cache_dir: Path | None = None,
    seasonal: str = "U",
) -> pd.DataFrame:
    """
    Fetch JOLTS monthly openings, hires, and separations by supersector.

    Parameters
    ----------
    years     : list of years to fetch (default 2015–2023)
    api_key   : BLS API key (optional; raises rate limit without one)
    cache_dir : parquet cache directory
    seasonal  : "U" = not seasonally adjusted, "S" = seasonally adjusted

    Returns
    -------
    Long-format DataFrame: year, month, sector, industry_code,
    data_element, measure ("level"|"rate"), value, seasonal
    """
    if years is None:
        years = JOLTS_YEARS

    cache_file = (cache_dir / f"jolts_{seasonal}.parquet") if cache_dir else None
    if cache_file and cache_file.exists():
        print(f"  [cache] JOLTS ({seasonal})")
        return pd.read_parquet(cache_file)

    all_ids     = _build_series_ids(seasonal)
    batch_size  = 50 if api_key else 25
    max_window  = 20 if api_key else 10
    start_year  = min(years)
    end_year    = max(years)

    year_batches: list[tuple[int, int]] = []
    y = start_year
    while y <= end_year:
        y_end = min(y + max_window - 1, end_year)
        year_batches.append((y, y_end))
        y = y_end + 1

    all_rows: list[dict] = []
    for y_start, y_end in year_batches:
        for i in range(0, len(all_ids), batch_size):
            batch = all_ids[i : i + batch_size]
            print(f"  [JOLTS] series {i+1}–{i+len(batch)} of {len(all_ids)} "
                  f"({y_start}–{y_end})…")
            try:
                series_list = _post_bls(batch, y_start, y_end, api_key)
                all_rows.extend(_parse_jolts(series_list))
            except Exception as exc:
                print(f"    Warning: batch failed — {exc}")
            time.sleep(1.5)

    if not all_rows:
        return pd.DataFrame(columns=["year", "month", "sector", "industry_code",
                                     "data_element", "measure", "value", "seasonal"])

    df = pd.DataFrame(all_rows)
    df = df[df["year"].isin(years)].sort_values(
        ["year", "month", "sector", "data_element"]
    ).reset_index(drop=True)

    if cache_file:
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_file, index=False)
        print(f"  [saved] jolts_{seasonal}.parquet  ({len(df)} rows)")

    return df


def compute_annual_averages(jolts_df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse monthly JOLTS to annual averages by (year, sector, data_element, measure).
    """
    return (
        jolts_df[jolts_df["sector"].notna()]
        .groupby(["year", "sector", "data_element", "measure"], as_index=False)["value"]
        .mean()
        .round(3)
        .sort_values(["year", "sector", "data_element"])
        .reset_index(drop=True)
    )


def compute_vacancy_rates(jolts_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute annual-average vacancy rate (job openings rate) per sector.

    Vacancy rate = openings_rate from JOLTS (direct; BLS publishes it as JO rate).
    Also computes OLS trend slope over the available year range.

    Returns DataFrame: sector, year, openings_level, openings_rate,
    hires_rate, separations_rate, vacancy_rate_trend_slope
    """
    annual = compute_annual_averages(jolts_df)

    def _pivot(elem: str, measure: str) -> pd.DataFrame:
        sub = annual[(annual["data_element"] == elem) & (annual["measure"] == measure)]
        return sub[["year", "sector", "value"]].rename(columns={"value": f"{elem}_{measure}"})

    openings_l = _pivot("openings",           "level")
    openings_r = _pivot("openings",           "rate")
    hires_r    = _pivot("hires",              "rate")
    seps_r     = _pivot("total_separations",  "rate")

    df = openings_l
    for other in [openings_r, hires_r, seps_r]:
        df = df.merge(other, on=["year", "sector"], how="outer")

    df = df.rename(columns={
        "openings_level": "openings_thousands",
        "openings_rate":  "vacancy_rate_pct",
        "hires_rate":     "hires_rate_pct",
        "total_separations_rate": "separations_rate_pct",
    })

    # OLS slope of vacancy rate over time, per sector
    slopes = []
    for sector, grp in df.groupby("sector"):
        sub = grp.dropna(subset=["vacancy_rate_pct"]).sort_values("year")
        if len(sub) >= 3:
            x = sub["year"].values.astype(float)
            y = sub["vacancy_rate_pct"].values
            slope = float(np.polyfit(x - x.mean(), y, 1)[0])
        else:
            slope = None
        slopes.append({"sector": sector, "vacancy_rate_trend_slope": slope})

    slopes_df = pd.DataFrame(slopes)
    result = df.merge(slopes_df, on="sector", how="left")
    return result.sort_values(["sector", "year"]).reset_index(drop=True)
