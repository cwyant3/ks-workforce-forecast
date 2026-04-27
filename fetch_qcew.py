"""
fetch_qcew.py
Fetches BLS Quarterly Census of Employment and Wages (QCEW) annual data
for county-level industry sector employment and wages.

Data source: BLS QCEW annual by-area ZIP files
  https://data.bls.gov/cew/data/files/{year}/csv/{year}_annual_by_area.zip

Key design notes:
  • Sector rows in county files use own_code=5 (Private sector).
    own_code=0 only exists as a single total-all-industries row (agglvl=70).
    Private sector dominates all five target sectors; government workers are
    a small fraction and are typically suppressed in rural counties anyway.
  • Files inside the ZIP are named:
    {year}.annual.by_area/{year}.annual {fips5} {county name}.csv
  • One ZIP is downloaded per year (~140 MB); only state + county files for the
    requested state are extracted and cached as parquet.
"""

import io
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT          = Path(__file__).parent
QCEW_CACHE    = ROOT / "data" / "qcew_cache"
QCEW_BASE_URL = "https://data.bls.gov/cew/data/files"
QCEW_YEARS    = list(range(2015, 2024))   # 2015–2023 annual averages

# ── Sector → QCEW 2-digit NAICS codes ────────────────────────────────────────
# Notes:
#   • Manufacturing "31-33" is the single combined QCEW supersector code
#   • Skilled Trades: Utilities (22) + Construction/HVAC/HeavyEquip (23)
#                     + Auto/Diesel/Equipment Repair (81)
#   • Hospitality: Arts/Recreation (71) + Accommodation/Food Services (72)
#   • IT: Information (51) + Professional/Scientific/Technical Services (54)
SECTOR_NAICS: dict[str, list[str]] = {
    "Healthcare":                   ["62"],
    "Manufacturing":                ["31-33"],
    "Hospitality & Entertainment":  ["71", "72"],
    "IT/Computer Services":         ["51", "54"],
    "Skilled Trades":               ["22", "23", "81"],
}

SECTOR_DISPLAY_NAMES: dict[str, str] = {
    "Healthcare": "Healthcare (NAICS 62)",
    "Manufacturing": "Manufacturing (NAICS 31-33)",
    "Hospitality & Entertainment": "Hospitality, Entertainment & Food Service (NAICS 71+72)",
    "IT/Computer Services": "Information & Professional Services (NAICS 51+54)",
    "Skilled Trades": "Utilities, Construction & Repair Services (NAICS 22+23+81)",
}

SECTOR_COLORS: dict[str, str] = {
    "Healthcare":                   "#E74C3C",
    "Manufacturing":                "#2980B9",
    "Hospitality & Entertainment":  "#F39C12",
    "IT/Computer Services":         "#8E44AD",
    "Skilled Trades":               "#27AE60",
}

SECTORS = list(SECTOR_NAICS.keys())
_SECTOR_NAICS = {code for codes in SECTOR_NAICS.values() for code in codes}
_ALL_FETCH    = _SECTOR_NAICS | {"10"}   # "10" = total all industries for denominator


def _naics_to_sector(naics: str) -> str | None:
    for sector, codes in SECTOR_NAICS.items():
        if naics in codes:
            return sector
    return None


# ── ZIP download and extraction ───────────────────────────────────────────────

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}


def _download_zip(year: int) -> bytes:
    url = f"{QCEW_BASE_URL}/{year}/csv/{year}_annual_by_area.zip"
    print(f"    Downloading {year} QCEW ZIP (~140 MB)…")
    resp = requests.get(url, headers=_HTTP_HEADERS, timeout=600, stream=True)
    resp.raise_for_status()
    chunks = []
    total  = 0
    for chunk in resp.iter_content(1024 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total % (30 * 1024 * 1024) < 1024 * 1024:
            print(f"      {total // 1024 // 1024} MB…")
    return b"".join(chunks)


def _extract_state_from_zip(
    zip_bytes: bytes,
    state_fips: str,
    year: int,
) -> dict[str, pd.DataFrame]:
    """
    Extract CSV DataFrames for one state from a QCEW by_area ZIP.

    Returns dict mapping area_fips → DataFrame.
    """
    sfips5    = state_fips.zfill(2)
    fips_re   = re.compile(rf"\b{sfips5}\d{{3}}\b")   # matches 20001 … 20999
    state_re  = re.compile(rf"\b{sfips5}000\b")        # statewide file

    results: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            basename = name.rsplit("/", 1)[-1]
            # Match county files (e.g. "2023.annual 20001 Allen County, Kansas.csv")
            # and the statewide file (e.g. "2023.annual 20000 Kansas -- Statewide.csv")
            m = fips_re.search(basename) or state_re.search(basename)
            if not m:
                continue
            fips5 = m.group()
            try:
                with zf.open(name) as f:
                    df = pd.read_csv(f, dtype=str, low_memory=False)
                results[fips5] = df
            except Exception:
                pass
    return results


# ── Row parsing ───────────────────────────────────────────────────────────────

def _parse_df(df: pd.DataFrame, area_fips: str, year: int) -> list[dict]:
    """Extract sector employment / wage rows from one county DataFrame."""
    if df is None or df.empty:
        return []

    df = df.copy()
    for col in ("own_code", "industry_code", "size_code", "disclosure_code"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # Sector rows: own_code=5 (Private), size_code=0 (all sizes), target NAICS
    # also pull own_code=0 for industry_code=10 (total all industries denominator)
    mask = (
        (
            ((df["own_code"] == "5") & df["industry_code"].isin(_SECTOR_NAICS)) |
            ((df["own_code"] == "0") & (df["industry_code"] == "10"))
        ) &
        (df["size_code"] == "0")
    )
    sub = df[mask].copy()
    if sub.empty:
        return []

    # Deduplicate: same industry_code at multiple agglvl levels → keep lowest agglvl
    if "agglvl_code" in sub.columns:
        sub["_ag"] = pd.to_numeric(sub["agglvl_code"], errors="coerce").fillna(99)
        sub = (sub.sort_values("_ag")
                  .groupby(["own_code", "industry_code"], as_index=False)
                  .first()
                  .drop(columns=["_ag"]))

    rows = []
    for _, row in sub.iterrows():
        naics     = str(row["industry_code"])
        disc      = str(row.get("disclosure_code", "")).strip()
        disclosed = disc not in ("N", "S")
        emp  = float(row.get("annual_avg_emplvl",  "") or 0) if disclosed else None
        pay  = float(row.get("avg_annual_pay",     "") or 0) if disclosed else None
        wkly = float(row.get("annual_avg_wkly_wage", "") or 0) if disclosed else None
        rows.append({
            "area_fips":       area_fips,
            "year":            year,
            "naics":           naics,
            "sector":          _naics_to_sector(naics),
            "employment":      emp,
            "avg_annual_pay":  pay,
            "avg_weekly_wage": wkly,
            "suppressed":      not disclosed,
        })
    return rows


# ── Aggregation ───────────────────────────────────────────────────────────────

def _aggregate(rows: list[dict], is_county: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Collapse raw rows into sector-level and total-employment DataFrames.

    Returns
    -------
    sector_df : county_fips, year, sector, employment, avg_annual_pay,
                avg_weekly_wage, suppressed
    totals_df : county_fips, year, total_employment
    """
    _empty_sec = pd.DataFrame(columns=["county_fips", "year", "sector",
                                        "employment", "avg_annual_pay",
                                        "avg_weekly_wage", "suppressed"])
    _empty_tot = pd.DataFrame(columns=["county_fips", "year", "total_employment"])

    if not rows:
        return _empty_sec, _empty_tot

    df = pd.DataFrame(rows)
    if is_county:
        # County FIPS codes are exactly 5 digits (SS+CCC). Guard against any
        # statewide (SS000) or non-standard rows that may have leaked through.
        df = df[df["area_fips"].str.len() == 5]
        df["county_fips"] = df["area_fips"].str[-3:]
    else:
        df["county_fips"] = df["area_fips"]

    # Total employment rows (naics="10")
    tot_rows = df[df["naics"] == "10"].copy()
    if tot_rows.empty:
        totals_df = _empty_tot.copy()
    else:
        totals_df = (tot_rows.groupby(["county_fips", "year"])["employment"]
                     .sum().reset_index()
                     .rename(columns={"employment": "total_employment"}))

    # Sector rows (exclude naics="10")
    sec_df = df[df["sector"].notna()].copy()
    result = []
    for (fips, year, sector), grp in sec_df.groupby(
            ["county_fips", "year", "sector"]):
        has = grp["employment"].notna()
        if not has.any():
            result.append({"county_fips": fips, "year": year, "sector": sector,
                           "employment": None, "avg_annual_pay": None,
                           "avg_weekly_wage": None, "suppressed": True})
            continue
        sub  = grp[has]
        emp  = float(sub["employment"].sum())
        if emp > 0:
            w_pay  = float((sub["avg_annual_pay"].fillna(0)  * sub["employment"]).sum() / emp)
            w_wkly = float((sub["avg_weekly_wage"].fillna(0) * sub["employment"]).sum() / emp)
        else:
            w_pay  = float(sub["avg_annual_pay"].mean() or 0)
            w_wkly = float(sub["avg_weekly_wage"].mean() or 0)
        result.append({"county_fips": fips, "year": year, "sector": sector,
                       "employment": emp, "avg_annual_pay": w_pay,
                       "avg_weekly_wage": w_wkly,
                       "suppressed": bool(grp["suppressed"].any())})

    sector_df = pd.DataFrame(result) if result else _empty_sec.copy()
    return sector_df, totals_df


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_state_qcew(
    state_fips: str,
    county_fips3_list: list[str],
    years: list[int] | None = None,
    cache_dir: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Fetch QCEW sector employment and wages for all counties + state level.

    Downloads one ~140 MB ZIP per year; Kansas-relevant CSVs are extracted,
    parsed, and cached as parquet (subsequent runs load from cache).

    Parameters
    ----------
    state_fips        : 2-digit state FIPS (e.g. "20" for Kansas)
    county_fips3_list : list of 3-digit county FIPS strings
    years             : years to fetch (default 2015–2023)
    cache_dir         : parquet cache directory (default data/qcew_cache/)

    Returns
    -------
    county_sector_df : county_fips, year, sector, employment, avg_annual_pay,
                       avg_weekly_wage, suppressed
    state_sector_df  : same columns, state-level (county_fips = "{sfips}000")
    state_totals_df  : year, total_employment  (all industries, state level)
    """
    if years is None:
        years = QCEW_YEARS
    if cache_dir is None:
        cache_dir = QCEW_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)

    sfips2 = state_fips.zfill(2)
    county_fips_set = {f.zfill(3) for f in county_fips3_list}

    all_county_rows: list[dict] = []
    all_state_rows:  list[dict] = []

    for year in years:
        year_cache = cache_dir / f"s{sfips2}_{year}.parquet"

        if year_cache.exists():
            year_df = pd.read_parquet(year_cache)
        else:
            # Download full ZIP for this year and extract state files
            zip_bytes  = _download_zip(year)
            area_dfs   = _extract_state_from_zip(zip_bytes, sfips2, year)
            del zip_bytes   # free memory

            raw_rows: list[dict] = []
            for fips5, df in area_dfs.items():
                raw_rows.extend(_parse_df(df, fips5, year))
            time.sleep(0.5)

            if raw_rows:
                year_df = pd.DataFrame(raw_rows)
                year_df.to_parquet(year_cache, index=False)
                print(f"    Cached {year}: {len(year_df)} rows saved to {year_cache.name}")
            else:
                year_df = pd.DataFrame()
                print(f"    Warning: no data extracted for {year}")

        if year_df.empty:
            continue

        # Split state vs county
        state_area = f"{sfips2}000"
        state_mask  = year_df["area_fips"] == state_area
        county_mask = year_df["area_fips"].str[-3:].isin(county_fips_set)

        all_state_rows.extend(year_df[state_mask].to_dict("records"))
        all_county_rows.extend(year_df[county_mask].to_dict("records"))

    county_sector_df, county_totals = _aggregate(all_county_rows, is_county=True)
    state_sector_df,  state_totals  = _aggregate(all_state_rows,  is_county=False)

    # State totals: aggregate across years
    state_totals_df = (state_totals.groupby("year")["total_employment"]
                       .sum().reset_index()) if not state_totals.empty else pd.DataFrame(
        columns=["year", "total_employment"])

    print(f"  QCEW loaded: {len(county_sector_df)} county-sector-year rows, "
          f"{len(state_sector_df)} state-sector-year rows")
    return county_sector_df, state_sector_df, state_totals_df
