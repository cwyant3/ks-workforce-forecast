"""
fetch_ipeds.py
Fetches NCES IPEDS completions data for postsecondary institutions within
a given state, maps CIP codes to workforce sectors, and caches as parquet.
Designed to be state-agnostic; defaults to Kansas (FIPS 20).

Data source: NCES IPEDS bulk CSV downloads (no API key required)
  Completions: https://nces.ed.gov/ipeds/complete-data-files/C{YEAR}_A.zip
  Institution characteristics (HD): https://nces.ed.gov/ipeds/complete-data-files/HD{YEAR}.zip

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

import bulk_cache
from cache_freshness import load_fresh_cache, save_if_complete

# Collection N covers academic year N-1/N. C2025_A and HD2025 were
# downloaded and row-validated 2026-10-20 from complete-data-files.
IPEDS_YEARS = list(range(2015, 2026))

IPEDS_BASE         = "https://nces.ed.gov/ipeds/complete-data-files"
# NCES serves the bulk files from two paths. Checked 2026-11-08 with HEAD
# requests: complete-data-files has collections 2023-2025 and returns 404 for
# 2015-2022; datacenter/data has 2015-2024 and returns 404 for 2025. Each
# download tries the primary path, then falls back on a 404.
IPEDS_FALLBACK_BASE = "https://nces.ed.gov/ipeds/datacenter/data"
IPEDS_BASES        = (IPEDS_BASE, IPEDS_FALLBACK_BASE)
COMPLETIONS_URL    = IPEDS_BASE + "/C{year}_A.zip"
HD_URL             = IPEDS_BASE + "/HD{year}.zip"
FLAGS_URL          = IPEDS_BASE + "/FLAGS{year}.zip"
IMPUTATION_LABELS = {
    'A': 'Not applicable', 'B': 'Institution left item blank',
    'C': 'Analyst corrected reported value', 'D': 'Do not know',
    'G': 'Data generated from other data values', 'H': 'Data not usable',
    'J': 'Logical imputation', 'K': 'Ratio adjustment',
    'L': 'Group median imputation', 'N': 'Nearest neighbor imputation',
    'P': 'Carry forward imputation', 'R': 'Reported', 'Z': 'Implied zero',
}


def attach_response_flags(detail, flags):
    """Preserve public institution response/parent flags without allocating campuses.

    FLAGS and C2025_A dictionaries checked 2026-10-20. A parent total remains
    at the reporting institution; never copy it to each child campus.
    """
    flags = flags.copy()
    flags.columns = flags.columns.str.lower().str.strip()
    fields = ['unitid', 'stat_c', 'lock_c', 'prch_c', 'idx_c', 'pcc_f', 'imp_c']
    if not set(fields) <= set(flags):
        raise ValueError('IPEDS FLAGS missing completions response fields')
    flags = flags[fields].copy()
    flags['unitid'] = pd.to_numeric(flags.unitid, errors='raise').astype('Int64')
    if flags.unitid.duplicated().any():
        raise ValueError('Duplicate reporting UNITID in FLAGS')
    result = detail.merge(flags, on='unitid', how='left', validate='many_to_one')
    result['response_flag_status'] = result.stat_c.map({'1':'Respondent','4':'Nonrespondent, imputed','5':'Nonrespondent, not imputed','-2':'Not applicable','-9':'Not active'}).fillna('Unknown or unavailable')
    result['parent_reporting_status'] = result.prch_c.map({'1':'Includes child campuses','2':'All data reported with parent','-2':'No parent/child relationship reported'}).fillna('Unknown or unavailable')
    result['imputation_status'] = result.imputation_flag.map(IMPUTATION_LABELS).fillna('Unknown or unavailable')
    result['flags_availability_status'] = result.stat_c.notna().map({True:'published',False:'not_reported'})
    if result.prch_c.eq('2').any():
        raise ValueError('Child campus unexpectedly has detail; review before aggregation')
    return result

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
    20: "less_than_12_weeks_cert",
    21: "12_weeks_to_less_than_1yr_cert",
    17: "doctoral_research",
    18: "doctoral_professional",
    19: "doctoral_other",
}

# CIP 2-digit prefix → dashboard sector.
# Legacy dashboard category approximation, not an official CIP-to-SOC allocation.
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
    """Fetch an IPEDS archive, shared across states within a refresh session.

    The completions archive (C{year}_A.zip) is national but cached per state, so
    without the bulk cache every state in a multi-state refresh re-downloads it.
    (HD{year}.zip is already cached nationally, so it only benefits from this
    within a single cold run.) Outside a session this is a plain download.
    """
    return bulk_cache.cached_download(
        f"ipeds_{url.rsplit('/', 1)[-1]}",
        lambda: _http_get_zip_any(candidate_urls(url), label),
    )


def candidate_urls(url: str) -> list[str]:
    """The given URL first, then the same file name on each other NCES base."""
    name = url.rsplit("/", 1)[-1]
    return [url] + [f"{base}/{name}" for base in IPEDS_BASES if f"{base}/{name}" != url]


def _http_get_zip_any(urls: list[str], label: str) -> bytes:
    """Try each URL in order; only a 404 moves on to the next path."""
    last_404 = None
    for url in urls:
        try:
            return _http_get_zip(url, label)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                last_404 = exc
                continue
            raise
    raise last_404 if last_404 is not None else FileNotFoundError(f"No URL for {label}")


def _http_get_zip(url: str, label: str) -> bytes:
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
    cache_file = cache_dir / f"hd_v2_{year}.parquet"
    if cache_file.exists():
        return pd.read_parquet(cache_file)

    zip_bytes = _download_zip(HD_URL.format(year=year), f"HD{year}")
    time.sleep(1)
    raw = _read_csv_from_zip(zip_bytes, f"hd{year}.csv")

    raw.columns = raw.columns.str.lower().str.strip().str.replace('﻿', '', regex=False)

    hd = prepare_hd(raw)

    hd.to_parquet(cache_file, index=False)
    print(f"    [saved] hd_v2_{year}.parquet  ({len(hd)} institutions)")
    return hd


# ── Completions loader ────────────────────────────────────────────────────────

def prepare_hd(raw):
    """Keep reporting-unit identity; never substitute state FIPS for missing county."""
    raw=raw.copy(); raw.columns=raw.columns.str.lower().str.strip().str.replace("\ufeff","",regex=False)
    needed=["unitid","instnm","stabbr","countycd"]
    if not set(needed)<=set(raw): raise ValueError("IPEDS HD missing required geography fields")
    hd=raw[needed+[c for c in ("fips","countynm","cyactive","newid","closedat") if c in raw]].copy()
    hd["unitid"]=pd.to_numeric(hd.unitid,errors="raise").astype("Int64")
    if hd.unitid.duplicated().any(): raise ValueError("Duplicate reporting UNITID in HD")
    valid=hd.countycd.astype(str).str.strip().str.fullmatch(r"\d{5}")
    hd["countycd"]=hd.countycd.where(valid)
    hd["county_fips3"]=hd.countycd.str[-3:]
    hd["state_fips_from_county"]=hd.countycd.str[:2]
    hd=hd.rename(columns={"instnm":"institution_name"})
    return hd


def parse_completions(raw, hd_df, year, state_fips="20"):
    """One row per reporting unit/CIP6/award level, first majors; retain zero/null/flags.

    Aggregate CIP99 rows are retained only for reconciliation via attrs, never
    added to detail. Two/four-digit hierarchy subtotals are excluded as well.
    """
    raw=raw.copy(); raw.columns=raw.columns.str.lower().str.strip().str.replace("\ufeff","",regex=False)
    if "awlevel" not in raw and "awlevelc" in raw: raw=raw.rename(columns={"awlevelc":"awlevel"})
    required=["unitid","cipcode","majornum","awlevel","ctotalt"]
    if not set(required)<=set(raw): raise ValueError("IPEDS completions missing required fields")
    raw["unitid"]=pd.to_numeric(raw.unitid,errors="raise").astype("Int64")
    raw["cipcode"]=raw.cipcode.astype(str).str.strip()
    raw["majornum"]=pd.to_numeric(raw.majornum,errors="raise").astype(int)
    raw["award_level"]=pd.to_numeric(raw.awlevel,errors="raise").astype(int)
    raw["completions"]=pd.to_numeric(raw.ctotalt,errors="coerce")
    if (raw.completions.dropna()<0).any(): raise ValueError("Negative completion counts")
    raw=raw.loc[raw.majornum==1].copy()
    key=["unitid","cipcode","award_level","majornum"]
    if raw.duplicated(key).any(): raise ValueError("Duplicate institutional CIP/award rows")
    hd=hd_df.copy()
    # Keep institutions with missing county but valid state FIPS for coverage QA.
    state=hd["fips"].astype(str).str.zfill(2) if "fips" in hd else hd.state_fips_from_county
    hd=hd.loc[state==str(state_fips).zfill(2)]
    joined=raw.merge(hd,on="unitid",how="inner",validate="many_to_one")
    totals=joined.loc[joined.cipcode.isin(["99","99.0000"]),["unitid","award_level","completions"]].rename(columns={"completions":"published_total"})
    detail=joined.loc[joined.cipcode.str.fullmatch(r"\d{2}\.\d{4}") & ~joined.cipcode.str.startswith("99")].copy()
    detail["cip6"]=detail.cipcode
    detail["cip2"]=detail.cipcode.str[:2]
    detail["sector"]=detail.cip2.map(CIP2_TO_SECTOR)
    detail["credential_level"]=detail.award_level.map(CREDENTIAL_LEVELS).fillna("unknown")
    detail["county_fips"]=detail.county_fips3
    detail["county_fips5"]=detail.countycd
    detail["state_fips"]=str(state_fips).zfill(2); detail["year"]=int(year)
    detail["imputation_flag"]=detail["xctotalt"] if "xctotalt" in detail else "unknown"
    detail["availability_status"]=detail.completions.map(lambda v:"published" if pd.notna(v) else "not_reported")
    # Numeric total reconciliation is separate from distinct-person counting.
    sums=detail.groupby(["unitid","award_level"],as_index=False).completions.agg(lambda x:x.sum() if x.notna().all() else float("nan"))
    checks=totals.merge(sums,on=["unitid","award_level"],how="outer")
    checks["reconciled"]=(checks.published_total==checks.completions)&checks.published_total.notna()
    detail.attrs["reconciliation"]=checks
    return detail.reset_index(drop=True)


def _load_completions(year,state_fips,hd_df,cache_dir):
    cache_file=cache_dir/f"completions_detail_v3_{year}_s{state_fips.zfill(2)}.parquet"
    if cache_file.exists(): return pd.read_parquet(cache_file)
    raw=_read_csv_from_zip(_download_zip(COMPLETIONS_URL.format(year=year),f"C{year}_A"),f"c{year}_a.csv")
    result=parse_completions(raw,hd_df,year,state_fips)
    # attrs DataFrame is deliberately not serialized into parquet metadata.
    result.attrs={}
    try:
        flags=_read_csv_from_zip(_download_zip(FLAGS_URL.format(year=year),f"FLAGS{year}"),f"flags{year}.csv")
        result=attach_response_flags(result,flags)
        result.to_parquet(cache_file,index=False)
    except (requests.RequestException, OSError) as exc:
        result['flags_availability_status']='access_failed'
        result['flags_quality_note']=f'{type(exc).__name__}: {exc}'
    return result


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_ipeds(
    state_fips: str = "20",
    years: list[int] | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch IPEDS postsecondary completions for all institutions in a state.

    Completions retain institution, six-digit CIP, award level and first-major
    detail. Legacy sector categories are descriptive approximations.

    Parameters
    ----------
    state_fips : 2-digit state FIPS (default "20" = Kansas)
    years      : academic years to fetch (default 2015–2025)
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

    # As with LODES, the per-year HD/completions sub-caches are vintage-keyed
    # but this combined shortcut short-circuits ahead of them.
    combined_cache = cache_dir / f"ipeds_detail_v3_s{sf}_all.parquet"
    cached = load_fresh_cache(combined_cache, years, f"IPEDS {sf} (combined)")
    if cached is not None:
        return cached

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

    if 'flags_availability_status' in df and df.flags_availability_status.eq('published').all():
        save_if_complete(df, combined_cache, years,
                         sorted(df["year"].unique()), f"IPEDS {sf} (combined)")
    print(f"  IPEDS {sf}: {len(df)} rows, {df['unitid'].nunique()} institutions, "
          f"{df['year'].nunique()} years")

    return df


def summarize_by_sector(ipeds_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate IPEDS completions to (county_fips, year, sector) level.
    Returns DataFrame suitable for joining with sector employment projections.
    """
    return (
        ipeds_df[ipeds_df["sector"].notna()]
        .groupby(["state_fips", "county_fips", "year", "sector"], as_index=False)["completions"]
        .agg(lambda x: x.sum() if x.notna().all() else float("nan"))
        .sort_values(["county_fips", "year", "sector"])
        .reset_index(drop=True)
    )
