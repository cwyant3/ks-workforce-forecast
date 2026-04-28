"""
fetch_oes.py
Fetches BLS Occupational Employment & Wage Statistics (OES) data.
Produces two datasets:
  1. State-level OES — all-industry occupation wage benchmarks for a state
  2. National industry OES — SOC occupation trees by NAICS sector (no county OES exists)

Data sources (no API key required):
  State:    https://www.bls.gov/oes/special.requests/oesm{YY}st.zip
  Industry: https://www.bls.gov/oes/special.requests/oesm{YY}in4.zip

OES reference period is May of each year.
Years available: 2015–2023 (no 2020 — BLS suspended May 2020 OES due to COVID).

Output from fetch_oes_state():
  state_fips, year, occ_code, occ_title, tot_emp,
  h_median (hourly median wage), a_median (annual median wage)

Output from fetch_oes_by_sector():
  year, sector, naics_prefix, occ_code, occ_title,
  tot_emp, a_median

Output from top_occupations_by_sector():
  sector, occ_code, occ_title, tot_emp, a_median, rank
"""

import io
import time
import zipfile
import requests
import pandas as pd
from pathlib import Path

# May 2020 OES was suspended; 2019 is the prior comparable data point
OES_YEARS      = [2015, 2016, 2017, 2018, 2019, 2021, 2022, 2023]
OES_STATE_URL  = "https://www.bls.gov/oes/special.requests/oesm{yy}st.zip"
OES_INDUS_URL  = "https://www.bls.gov/oes/special.requests/oesm{yy}in4.zip"

# Sector → NAICS 2-digit prefixes (mirrors fetch_qcew.py SECTOR_NAICS)
SECTOR_NAICS_PREFIX: dict[str, list[str]] = {
    "Healthcare":                  ["62"],
    "Manufacturing":               ["31", "32", "33"],
    "Hospitality & Entertainment": ["71", "72"],
    "IT/Computer Services":        ["51", "54"],
    "Skilled Trades":              ["22", "23", "81"],
}

# Columns that identify occupations vs. occupation groups (groups have no detailed SOC)
_GROUP_CODES = {"00-0000", "11-0000", "13-0000", "15-0000", "17-0000",
                "19-0000", "21-0000", "23-0000", "25-0000", "27-0000",
                "29-0000", "31-0000", "33-0000", "35-0000", "37-0000",
                "39-0000", "41-0000", "43-0000", "45-0000", "47-0000",
                "49-0000", "51-0000", "53-0000", "55-0000"}

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}


# ── Download helpers ──────────────────────────────────────────────────────────

def _year_to_yy(year: int) -> str:
    return str(year)[-2:]


def _download_zip(url: str, label: str) -> bytes:
    print(f"    Downloading {label}…")
    resp = requests.get(url, headers=_HTTP_HEADERS, timeout=600, stream=True)
    resp.raise_for_status()
    chunks = []
    total = 0
    for chunk in resp.iter_content(1024 * 512):
        chunks.append(chunk)
        total += len(chunk)
        if total % (30 * 1024 * 1024) < 1024 * 512:
            print(f"      {total // 1024 // 1024} MB…")
    return b"".join(chunks)


def _read_oes_excel_from_zip(zip_bytes: bytes, year: int) -> pd.DataFrame:
    """
    Extract the main OES data sheet from a BLS OES zip.
    BLS changed file naming across years; try common patterns.
    """
    yy = _year_to_yy(year)
    candidates = [
        f"state_M{year}_dl.xlsx",
        f"state_M{year}_dl.xls",
        f"MSA_M{year}_dl.xlsx",    # fallback
        f"oesm{yy}st.xlsx",
        f"all_data_M_{year}.xlsx",
    ]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names_lower = {n.lower(): n for n in zf.namelist()}
        for cand in candidates:
            if cand.lower() in names_lower:
                with zf.open(names_lower[cand.lower()]) as f:
                    return pd.read_excel(f, dtype=str)
        # Last resort: pick the largest xlsx in the archive
        xlsx_files = [(n, zf.getinfo(n).file_size) for n in zf.namelist()
                      if n.lower().endswith((".xlsx", ".xls"))]
        if xlsx_files:
            target = max(xlsx_files, key=lambda x: x[1])[0]
            print(f"      Using fallback file: {target}")
            with zf.open(target) as f:
                return pd.read_excel(f, dtype=str)

    raise FileNotFoundError(f"No OES Excel file found in OES {year} zip")


def _read_oes_industry_excel_from_zip(zip_bytes: bytes, year: int) -> pd.DataFrame:
    """Extract all sheets from the industry OES zip and concatenate."""
    frames = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith((".xlsx", ".xls")):
                continue
            try:
                with zf.open(name) as f:
                    df = pd.read_excel(f, dtype=str)
                df["_source_file"] = name
                frames.append(df)
            except Exception as exc:
                print(f"      Warning: could not read {name} — {exc}")
    if not frames:
        raise FileNotFoundError(f"No Excel files found in industry OES {year} zip")
    return pd.concat(frames, ignore_index=True)


# ── Column normalisation ──────────────────────────────────────────────────────

_COL_ALIASES: dict[str, list[str]] = {
    "area":       ["area", "area_fips"],
    "area_title": ["area_title", "area_name"],
    "occ_code":   ["occ_code"],
    "occ_title":  ["occ_title"],
    "tot_emp":    ["tot_emp"],
    "h_median":   ["h_median"],
    "a_median":   ["a_median"],
    "naics":      ["naics", "naics_code"],
    "naics_title":["naics_title"],
    "i_group":    ["i_group"],
}


def _normalise_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase all column names and apply alias mapping."""
    df = df.copy()
    df.columns = df.columns.str.strip().str.lower()
    rename = {}
    for canonical, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if alias in df.columns and alias != canonical:
                rename[alias] = canonical
    return df.rename(columns=rename)


def _to_numeric_wage(series: pd.Series) -> pd.Series:
    """Convert BLS wage strings to float; '#' means data not available."""
    return pd.to_numeric(series.replace({"#": None, "*": None, "**": None}),
                         errors="coerce")


# ── State OES ─────────────────────────────────────────────────────────────────

def _parse_state_oes(df: pd.DataFrame, state_fips: str, year: int) -> pd.DataFrame:
    """
    Filter state OES Excel to one state, cross-industry rows, detailed occupations.
    """
    df = _normalise_cols(df)
    sf = state_fips.zfill(2)

    # AREA field is typically a 7-character code; state-level rows end in '000'
    # e.g., "0200000" for Alaska, "2000000" for Kansas — but format varies by year.
    # Strategy: keep rows where AREA starts with the 2-digit state FIPS.
    if "area" not in df.columns:
        raise KeyError(f"No AREA column found in OES {year}. Columns: {list(df.columns)}")

    area_mask = df["area"].astype(str).str.strip().str.lstrip("0").str[:2] == str(int(sf))
    df = df[area_mask].copy()

    # Cross-industry rows: OES uses 'NAICS' = '000000' or blank for all-industry
    if "naics" in df.columns:
        df = df[df["naics"].astype(str).str.strip().isin(["000000", "0", "", "nan"])]

    # Filter to detailed occupations (exclude major group summaries)
    if "occ_code" in df.columns:
        df = df[~df["occ_code"].isin(_GROUP_CODES)]
        df = df[df["occ_code"].astype(str).str.match(r"^\d{2}-\d{4}$")]

    needed = ["occ_code", "occ_title", "tot_emp", "a_median"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise KeyError(f"OES {year}: missing columns {missing}. Available: {list(df.columns)}")

    result = df[needed].copy()
    result["tot_emp"]  = _to_numeric_wage(result["tot_emp"])
    result["a_median"] = _to_numeric_wage(result["a_median"])
    if "h_median" in df.columns:
        result["h_median"] = _to_numeric_wage(df["h_median"])
    else:
        result["h_median"] = None
    result["year"]       = year
    result["state_fips"] = sf
    return result[["state_fips", "year", "occ_code", "occ_title",
                   "tot_emp", "h_median", "a_median"]].dropna(subset=["occ_code"])


def fetch_oes_state(
    state_fips: str = "20",
    years: list[int] | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch state-level OES cross-industry occupation wage benchmarks.

    Parameters
    ----------
    state_fips : 2-digit state FIPS (default "20" = Kansas)
    years      : May OES reference years (default 2015–2023 excl. 2020)
    cache_dir  : parquet cache directory

    Returns
    -------
    DataFrame: state_fips, year, occ_code, occ_title, tot_emp, h_median, a_median
    """
    if years is None:
        years = OES_YEARS
    sf = state_fips.zfill(2)
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    combined_cache = cache_dir / f"oes_state_s{sf}.parquet"
    if combined_cache.exists():
        print(f"  [cache] OES state {sf}")
        return pd.read_parquet(combined_cache)

    frames = []
    for year in years:
        year_cache = cache_dir / f"oes_state_s{sf}_{year}.parquet"
        if year_cache.exists():
            print(f"  [cache] OES state {sf} {year}")
            frames.append(pd.read_parquet(year_cache))
            continue

        url = OES_STATE_URL.format(yy=_year_to_yy(year))
        try:
            zip_bytes = _download_zip(url, f"OES state {year}")
            raw       = _read_oes_excel_from_zip(zip_bytes, year)
            year_df   = _parse_state_oes(raw, sf, year)
            year_df.to_parquet(year_cache, index=False)
            print(f"    [saved] oes_state_s{sf}_{year}.parquet  ({len(year_df)} occupations)")
            frames.append(year_df)
        except requests.HTTPError as exc:
            print(f"    Warning: OES state {year} unavailable ({exc.response.status_code}) — skipping")
        except Exception as exc:
            print(f"    Warning: OES state {year} failed — {exc}")
        time.sleep(2)

    if not frames:
        return pd.DataFrame(columns=["state_fips", "year", "occ_code", "occ_title",
                                     "tot_emp", "h_median", "a_median"])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["year", "occ_code"]).reset_index(drop=True)
    df.to_parquet(combined_cache, index=False)
    print(f"  [saved] oes_state_s{sf}.parquet  ({len(df)} rows)")
    return df


# ── Industry OES (national, NAICS 4-digit) ────────────────────────────────────

def _parse_industry_oes(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """
    Parse national industry OES file: keep sector-relevant NAICS rows,
    detailed occupations only.
    """
    df = _normalise_cols(df)

    # Filter to detailed occupations
    if "occ_code" in df.columns:
        df = df[~df["occ_code"].isin(_GROUP_CODES)]
        df = df[df["occ_code"].astype(str).str.match(r"^\d{2}-\d{4}$")]

    # Determine 2-digit NAICS prefix for sector mapping
    if "naics" not in df.columns:
        return pd.DataFrame()

    df["naics_str"] = df["naics"].astype(str).str.strip()
    # Industry OES NAICS codes may be 2–6 digits; extract 2-digit prefix
    df["naics2"] = df["naics_str"].str.replace("-", "").str[:2]

    # Build reverse lookup: naics2 prefix → sector name
    naics2_to_sector: dict[str, str] = {}
    for sector, prefixes in SECTOR_NAICS_PREFIX.items():
        for prefix in prefixes:
            naics2_to_sector[prefix] = sector

    df["sector"] = df["naics2"].map(naics2_to_sector)
    df = df[df["sector"].notna()].copy()

    needed = ["naics", "sector", "occ_code", "occ_title", "tot_emp", "a_median"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise KeyError(f"Industry OES {year}: missing {missing}")

    result = df[needed].copy()
    result["tot_emp"]  = _to_numeric_wage(result["tot_emp"])
    result["a_median"] = _to_numeric_wage(result["a_median"])
    result["year"]     = year
    return result[["year", "sector", "naics", "occ_code", "occ_title",
                   "tot_emp", "a_median"]].dropna(subset=["occ_code", "sector"])


def fetch_oes_by_sector(
    years: list[int] | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch national industry OES to build SOC occupation trees by workforce sector.

    Because county-level OES doesn't exist, this provides national industry
    occupation distributions as a proxy for local sector composition.

    Returns
    -------
    DataFrame: year, sector, naics, occ_code, occ_title, tot_emp, a_median
    """
    if years is None:
        # Industry OES is large (~300 MB/zip); fetch most recent 3 years only by default
        years = [y for y in OES_YEARS if y >= 2021]
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    combined_cache = cache_dir / "oes_by_sector.parquet"
    if combined_cache.exists():
        print(f"  [cache] OES by sector (combined)")
        return pd.read_parquet(combined_cache)

    frames = []
    for year in years:
        year_cache = cache_dir / f"oes_sector_{year}.parquet"
        if year_cache.exists():
            print(f"  [cache] OES sector {year}")
            frames.append(pd.read_parquet(year_cache))
            continue

        url = OES_INDUS_URL.format(yy=_year_to_yy(year))
        try:
            zip_bytes = _download_zip(url, f"OES industry {year} (~300 MB)")
            raw       = _read_oes_industry_excel_from_zip(zip_bytes, year)
            year_df   = _parse_industry_oes(raw, year)
            year_df.to_parquet(year_cache, index=False)
            print(f"    [saved] oes_sector_{year}.parquet  "
                  f"({len(year_df)} occupation-sector rows)")
            frames.append(year_df)
        except requests.HTTPError as exc:
            print(f"    Warning: OES industry {year} unavailable "
                  f"({exc.response.status_code}) — skipping")
        except Exception as exc:
            print(f"    Warning: OES industry {year} failed — {exc}")
        time.sleep(2)

    if not frames:
        return pd.DataFrame(columns=["year", "sector", "naics", "occ_code",
                                     "occ_title", "tot_emp", "a_median"])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["year", "sector", "occ_code"]).reset_index(drop=True)
    df.to_parquet(combined_cache, index=False)
    print(f"  [saved] oes_by_sector.parquet  ({len(df)} rows)")
    return df


# ── Analysis helpers ──────────────────────────────────────────────────────────

def top_occupations_by_sector(
    oes_sector_df: pd.DataFrame,
    sector: str,
    n: int = 10,
    year: int | None = None,
) -> pd.DataFrame:
    """
    Return the top N occupations (by total employment) within a sector.

    If year is None, uses the most recent year in the dataset.
    Returns DataFrame: rank, occ_code, occ_title, tot_emp, a_median.
    """
    df = oes_sector_df[oes_sector_df["sector"] == sector].copy()
    if df.empty:
        return pd.DataFrame(columns=["rank", "occ_code", "occ_title", "tot_emp", "a_median"])

    if year is None:
        year = df["year"].max()
    df = df[df["year"] == year]

    # Aggregate across NAICS sub-codes within the sector (sum employment, median wage)
    agg = (
        df.groupby(["occ_code", "occ_title"], as_index=False)
        .agg(tot_emp=("tot_emp", "sum"), a_median=("a_median", "median"))
    )
    agg = agg.dropna(subset=["tot_emp"]).nlargest(n, "tot_emp").reset_index(drop=True)
    agg.insert(0, "rank", agg.index + 1)
    return agg


def wage_benchmark(
    oes_state_df: pd.DataFrame,
    occ_codes: list[str],
    year: int | None = None,
) -> pd.DataFrame:
    """
    Pull OES median wages for a list of SOC occupation codes.
    If year is None, uses the most recent year available.
    Returns DataFrame: occ_code, occ_title, h_median, a_median, year.
    """
    df = oes_state_df.copy()
    if year is None:
        year = int(df["year"].max())
    df = df[(df["year"] == year) & (df["occ_code"].isin(occ_codes))]
    return df[["occ_code", "occ_title", "h_median", "a_median", "year"]].reset_index(drop=True)
