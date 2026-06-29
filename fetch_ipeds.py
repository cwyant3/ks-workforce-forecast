"""
fetch_ipeds.py
Fetches NCES IPEDS completions data for postsecondary institutions within
a given state, maps CIP codes to workforce sectors, and caches as parquet.
Designed to be state-agnostic; defaults to Kansas (FIPS 20).

Data source: NCES IPEDS bulk CSV downloads (no API key required)
  Completions: https://nces.ed.gov/ipeds/datacenter/data/C{YEAR}_A.zip
  Institution characteristics (HD): https://nces.ed.gov/ipeds/datacenter/data/HD{YEAR}.zip

Key survey variables:
  UNITID     — institution ID (join key to HD file)
  CIPCODE    — 6-digit CIP code (e.g., "51.1601")
  MAJORNUM   — 1=first major, 2=second major (filter to 1 to avoid double-counting)
  AWLEVELC   — award level (1=<1yr cert, 2=1-<2yr cert, 3=assoc, 5=bach, 7=master's)
  CTOTALT    — total completions

HD survey variables:
  UNITID     — institution ID
  INSTNM     — institution name
  STABBR     — state abbreviation
  COUNTYCD   — 5-digit county FIPS code

Output DataFrame columns:
  state_fips, county_fips (3-digit str), unitid (int), institution_name (str),
  year (int), cip2 (str, 2-digit CIP prefix), sector (str | None),
  credential_level (str), completions (int)
"""

import io
import time
import zipfile
import requests
import pandas as pd
from pathlib import Path

IPEDS_YEARS = list(range(2015, 2024))   # 2014–15 through 2022–23 academic years

IPEDS_BASE         = "https://nces.ed.gov/ipeds/datacenter/data"
COMPLETIONS_URL    = IPEDS_BASE + "/C{year}_A.zip"
HD_URL             = IPEDS_BASE + "/HD{year}.zip"

# Credential level codes → readable labels
CREDENTIAL_LEVELS: dict[int, str] = {
    1: "less_than_1yr_cert",
    2: "1_to_2yr_cert",
    3: "associates",
    4: "postsec_award_1_4yr",
    5: "bachelors",
    6: "post_bacc_cert",
    7: "masters",
    8: "post_masters_cert",
    9: "doctoral",
    10: "doctoral_professional",
}

# CIP 2-digit prefix → dashboard sector.
# Source: O*NET CIP→SOC crosswalk + BLS sector definitions.
# Only includes CIPs commonly produced at sub-baccalaureate level in KS.
CIP2_TO_SECTOR: dict[str, str] = {
    # Healthcare & Nursing
    "51": "Healthcare",
    # Computer & Information Sciences
    "11": "IT/Computer Services",
    # Engineering Technologies
    "15": "Manufacturing",
    # Precision Production (welding, machining, fabrication)
    "48": "Manufacturing",
    # Agriculture — maps loosely to Manufacturing (food processing) in KS context
    "01": "Manufacturing",
    # Mechanic/Repair Technologies (auto, diesel, HVAC, equipment)
    "47": "Skilled Trades",
    # Construction Trades
    "46": "Skilled Trades",
    # Electrical/Electronic Engineering Tech
    "14": "Skilled Trades",
    # Personal & Culinary Services
    "12": "Hospitality & Entertainment",
    # Parks, Recreation, Leisure
    "31": "Hospitality & Entertainment",
    # Hospitality Administration
    "52": None,   # Business — too broad; omit from sector mapping
    # Liberal Arts / General Studies — omit
    "24": None,
    # Homeland Security / Law Enforcement
    "43": None,
    # Education
    "13": None,
}

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}


# ── Download helpers ──────────────────────────────────────────────────────────

def _download_zip(url: str, label: str) -> bytes:
    print(f"    Downloading {label}…")
    resp = requests.get(url, headers=_HTTP_HEADERS, timeout=300, stream=True)
    resp.raise_for_status()
    chunks = []
    for chunk in resp.iter_content(1024 * 256):
        chunks.append(chunk)
    return b"".join(chunks)


def _read_csv_from_zip(zip_bytes: bytes, target_suffix: str) -> pd.DataFrame:
    """Extract and read the first CSV file whose name ends with target_suffix."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.lower().endswith(target_suffix.lower()):
                with zf.open(name) as f:
                    csv_bytes = f.read()
                # IPEDS CSV bodies are Latin-1/CP1252 (institution names), but
                # some files (e.g. HD2023) carry a leading UTF-8 BOM. Decoding
                # those BOM bytes as Latin-1 yields the literal chars 'ï»¿' on the
                # first column header (so 'unitid' → 'ï»¿unitid'). Strip the BOM at
                # the byte level before Latin-1 decoding so headers stay clean.
                if csv_bytes.startswith(b"\xef\xbb\xbf"):
                    csv_bytes = csv_bytes[3:]
                return pd.read_csv(io.BytesIO(csv_bytes), dtype=str,
                                   low_memory=False, encoding="latin-1")
    raise FileNotFoundError(
        f"No file ending with '{target_suffix}' found in zip"
    )


# ── HD (institutional characteristics) loader ────────────────────────────────

def _load_hd(
    year: int,
    state_fips: str,
    cache_dir: Path,
) -> pd.DataFrame:
    """
    Load institutional characteristics for a given year, filtered to state.
    Returns DataFrame: unitid (int), institution_name, county_fips (3-digit str).
    """
    cache_file = cache_dir / f"hd_{year}.parquet"
    if cache_file.exists():
        return pd.read_parquet(cache_file)

    zip_bytes = _download_zip(HD_URL.format(year=year), f"HD{year}")
    time.sleep(1)
    raw = _read_csv_from_zip(zip_bytes, f"hd{year}.csv")

    raw.columns = raw.columns.str.lower().str.strip().str.replace('﻿', '', regex=False)

    needed = ["unitid", "instnm", "stabbr", "countycd"]
    missing = [c for c in needed if c not in raw.columns]
    if missing:
        # Some years use slightly different column names; attempt fallbacks
        if "countycd" in missing and "countynm" in raw.columns:
            raw["countycd"] = raw.get("fips", "")
        remaining = [c for c in needed if c not in raw.columns]
        if remaining:
            raise KeyError(f"HD{year}: missing columns {remaining}. "
                           f"Available: {list(raw.columns)}")

    hd = raw[needed].copy()
    hd["unitid"] = pd.to_numeric(hd["unitid"], errors="coerce").astype("Int64")
    # COUNTYCD is a 5-digit FIPS string; strip to 3-digit county suffix
    hd["countycd"] = hd["countycd"].str.strip().str.zfill(5)
    hd["county_fips3"] = hd["countycd"].str[-3:]
    hd["state_fips_from_county"] = hd["countycd"].str[:2]
    hd = hd.rename(columns={"instnm": "institution_name"})

    hd.to_parquet(cache_file, index=False)
    print(f"    [saved] hd_{year}.parquet  ({len(hd)} institutions)")
    return hd


# ── Completions loader ────────────────────────────────────────────────────────

def _load_completions(
    year: int,
    state_fips: str,
    hd_df: pd.DataFrame,
    cache_dir: Path,
) -> pd.DataFrame:
    """
    Load completions for one academic year, filter to state institutions,
    and join with HD for county FIPS.

    Returns DataFrame: unitid, institution_name, county_fips, year,
    cip2, sector, credential_level, completions.
    """
    cache_file = cache_dir / f"completions_{year}_s{state_fips.zfill(2)}.parquet"
    if cache_file.exists():
        return pd.read_parquet(cache_file)

    zip_bytes = _download_zip(
        COMPLETIONS_URL.format(year=year), f"C{year}_A completions"
    )
    time.sleep(1)
    raw = _read_csv_from_zip(zip_bytes, f"c{year}_a.csv")
    raw.columns = raw.columns.str.lower().str.strip().str.replace('﻿', '', regex=False)

    # IPEDS renamed awlevelc → awlevel in some release years; normalise to awlevelc
    if "awlevelc" not in raw.columns and "awlevel" in raw.columns:
        raw = raw.rename(columns={"awlevel": "awlevelc"})

    needed = ["unitid", "cipcode", "majornum", "awlevelc", "ctotalt"]
    missing = [c for c in needed if c not in raw.columns]
    if missing:
        raise KeyError(f"C{year}_A: missing columns {missing}. "
                       f"Available: {list(raw.columns)}")

    # First majors only to prevent double-counting joint programs
    comp = raw[raw["majornum"] == "1"].copy()

    comp["unitid"]   = pd.to_numeric(comp["unitid"], errors="coerce").astype("Int64")
    comp["awlevelc"] = pd.to_numeric(comp["awlevelc"], errors="coerce").astype("Int64")
    comp["ctotalt"]  = pd.to_numeric(comp["ctotalt"], errors="coerce").fillna(0).astype(int)

    # Filter to state institutions via HD join
    state_units = hd_df[
        hd_df["state_fips_from_county"] == state_fips.zfill(2)
    ][["unitid", "institution_name", "county_fips3"]].copy()

    comp = comp.merge(state_units, on="unitid", how="inner")

    # 2-digit CIP prefix
    comp["cip2"] = comp["cipcode"].str.split(".").str[0].str.zfill(2)

    # Map to sector
    comp["sector"] = comp["cip2"].map(CIP2_TO_SECTOR)

    # Credential level label
    comp["credential_level"] = comp["awlevelc"].map(
        lambda x: CREDENTIAL_LEVELS.get(x, f"level_{x}") if pd.notna(x) else "unknown"
    )

    comp["year"] = year

    result = (
        comp.groupby(
            ["unitid", "institution_name", "county_fips3",
             "year", "cip2", "sector", "credential_level"],
            as_index=False,
            dropna=False,
        )["ctotalt"]
        .sum()
        .rename(columns={"county_fips3": "county_fips", "ctotalt": "completions"})
    )

    # Drop rows with zero completions
    result = result[result["completions"] > 0].reset_index(drop=True)

    result.to_parquet(cache_file, index=False)
    print(f"    [saved] completions_{year}_s{state_fips.zfill(2)}.parquet  "
          f"({len(result)} rows)")
    return result


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_ipeds(
    state_fips: str = "20",
    years: list[int] | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch IPEDS postsecondary completions for all institutions in a state.

    Completions are grouped by institution, CIP 2-digit prefix, award level,
    and year, then mapped to the five dashboard workforce sectors.

    Parameters
    ----------
    state_fips : 2-digit state FIPS (default "20" = Kansas)
    years      : academic years to fetch (default 2015–2023)
    cache_dir  : parquet cache directory

    Returns
    -------
    DataFrame with columns: state_fips, county_fips, unitid, institution_name,
    year, cip2, sector, credential_level, completions
    """
    if years is None:
        years = IPEDS_YEARS
    sf = state_fips.zfill(2)

    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    combined_cache = cache_dir / f"ipeds_s{sf}_all.parquet"
    if combined_cache.exists():
        print(f"  [cache] IPEDS {sf} (combined)")
        return pd.read_parquet(combined_cache)

    frames = []
    for year in years:
        print(f"  IPEDS {year} (state {sf})…")
        try:
            hd_df = _load_hd(year, sf, cache_dir)
            year_df = _load_completions(year, sf, hd_df, cache_dir)
            year_df["state_fips"] = sf
            frames.append(year_df)
        except Exception as exc:
            print(f"    Warning: could not load IPEDS {year} — {exc}")
        time.sleep(1.5)

    if not frames:
        print("  Warning: no IPEDS data loaded")
        return pd.DataFrame(columns=[
            "state_fips", "county_fips", "unitid", "institution_name",
            "year", "cip2", "sector", "credential_level", "completions",
        ])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["county_fips", "year", "sector"]).reset_index(drop=True)

    df.to_parquet(combined_cache, index=False)
    print(f"  [saved] ipeds_s{sf}_all.parquet  ({len(df)} rows, "
          f"{df['unitid'].nunique()} institutions, "
          f"{df['year'].nunique()} years)")

    return df


def summarize_by_sector(ipeds_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate IPEDS completions to (county_fips, year, sector) level.
    Returns DataFrame suitable for joining with sector employment projections.
    """
    return (
        ipeds_df[ipeds_df["sector"].notna()]
        .groupby(["state_fips", "county_fips", "year", "sector"], as_index=False)["completions"]
        .sum()
        .sort_values(["county_fips", "year", "sector"])
        .reset_index(drop=True)
    )
