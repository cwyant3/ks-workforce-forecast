"""
fetch_oes.py
Fetches BLS Occupational Employment & Wage Statistics (OES) data.
Produces two datasets:
  1. State-level OES — all-industry occupation wage benchmarks for a state
  2. National industry OES — SOC occupation trees by NAICS sector (no county OES exists)

Data sources (no API key required):
  State (AREA_TYPE 2):     oesm{YY}st.zip  →  oesm{YY}all.zip  →  manual
  Industry (AREA_TYPE 1):  oesm{YY}in4.zip →  oesm{YY}all.zip  →  manual
  Manual tier:             data/oes_manual/all_data_M_{YYYY}.xlsx
  All under https://www.bls.gov/oes/special.requests/

OES reference period is May of each year. Years in use: 2015–2025 for the state
layer, 2021–2025 for the industry layer (no 2020 — BLS suspended May 2020 OES
due to COVID).

ACQUISITION IS THREE-TIERED because BLS's per-file availability is inconsistent
(see the notes on OES_STATE_URL below). Each layer falls through its own tier
list, cheapest first, ending at a workbook placed on disk by hand. Every source
carries the SAME 32-column schema, so one parser serves them all; they differ
only in which AREA_TYPE rows they contain, which is why _load_area_slice
filters on that column rather than trusting the filename.

AGGREGATION LEVEL IS A DECISION, NOT A DEFAULT. OEWS publishes the same
employment at several levels on three independent axes — industry (I_GROUP),
occupation (O_GROUP) and ownership (OWN_CODE) — and the ownership codes are
overlapping aggregates rather than a partition. Anything that sums tot_emp must
first collapse all three. See _parse_industry_oes, which does, and explains
what happened before it did.

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

import bulk_cache
from cache_freshness import load_fresh_cache, save_if_complete

# May 2020 OES was suspended, so 2019 is the prior comparable data point and
# the gap in this list is intentional.
OES_YEARS = [2015, 2016, 2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025]

# The industry layer covers fewer years than the state layer on purpose: its
# source archives are large and the dashboard only uses recent sector
# composition. It is NOT capped by availability any more — the three-tier
# acquisition below reaches 2024 and 2025.
OES_SECTOR_YEARS = [y for y in OES_YEARS if y >= 2021]

# BLS's per-file availability in this directory is inconsistent, and the
# pattern makes no sense from the outside. Probed 2026-09-02, re-probed
# 2026-09-03 with identical results:
#
#   oesm23st.zip  -> 200 (7,445,440 B)    oesm23all.zip -> 200 (80,129,309 B)
#   oesm24st.zip  -> 403                  oesm24all.zip -> 200 (79,846,301 B)
#   oesm25st.zip  -> 403                  oesm25all.zip -> 403
#   oesm24in4.zip -> 403                  oesm24nat.zip -> 403
#   oesm25in4.zip -> 403                  oesm25nat.zip -> 403
#
# Every 2023-and-older file returns 200, so this is neither a bot block nor an
# age gate — it is per-file. A *browser* User-Agent gets 403 for every year
# including 2023, so the pipeline's own UA is the one that works; do NOT try to
# "fix" a 403 here by spoofing a browser, which makes things strictly worse.
#
# Hence: 2024 is served by the all-areas ZIP, and 2025 — which has no reachable
# URL at all under any probed pattern — is served by a hand-placed workbook.
OES_STATE_URL  = "https://www.bls.gov/oes/special.requests/oesm{yy}st.zip"
OES_ALL_URL    = "https://www.bls.gov/oes/special.requests/oesm{yy}all.zip"
OES_INDUS_URL  = "https://www.bls.gov/oes/special.requests/oesm{yy}in4.zip"

# Manual fallback for a year BLS will not serve. Placed by hand; the year in
# the filename is what the vintage is read from, so name it for its year and
# leave older editions in place (same convention as the other manual sources —
# see docs/data-source-release-calendar.md "Manual sources").
#
# This lives OUTSIDE data/oes_cache/ on purpose. oes_cache is in
# refresh_dashboard.ANNUAL_API_CACHES, which clear_caches() removes with
# shutil.rmtree — so a workbook stored there would be destroyed by the first
# `--sources oes` refresh, taking 2025 with it. Every other manual source in
# this repo sits in a directory that is never cleared; this matches.
ROOT           = Path(__file__).parent
OES_MANUAL_DIR = ROOT / "data" / "oes_manual"
OES_MANUAL_GLOB = "all_data_M_{year}.xls*"

# HTTP statuses that mean "try the next tier", as opposed to a real failure.
_UNAVAILABLE_STATUSES = {403, 404}

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


def _read_oes_excel_from_zip(
    zip_bytes: bytes,
    year: int,
    candidates: list[str] | None = None,
) -> pd.DataFrame:
    """
    Extract the main OES data sheet from a BLS OES zip.
    BLS changed file naming across years; try common patterns.

    `candidates` overrides the default list. The industry archive needs it:
    oesm{yy}in4.zip ships EIGHT workbooks pre-split by aggregation level
    (natsector, nat3d, nat4d, nat5d_6d, plus *_owner_* variants and
    file_descriptions), so the member you pick IS the granularity decision. The
    largest-xlsx fallback below would pick nat4d and silently give you 4-digit
    rows with no sector totals at all.
    """
    yy = _year_to_yy(year)
    if candidates is None:
        candidates = [
            f"state_M{year}_dl.xlsx",
            f"state_M{year}_dl.xls",
            f"MSA_M{year}_dl.xlsx",    # fallback
            f"oesm{yy}st.xlsx",
            f"all_data_M_{year}.xlsx",
        ]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # Match on BASENAME, not the full member path. Every one of these
        # archives nests its payload in a directory ("oesm24all/all_data_M_
        # 2024.xlsx", "oesm23st/state_M2023_dl.xlsx"), so matching the full
        # name never hit and every year fell through to the largest-xlsx
        # fallback below. That happened to pick the right file, which is why
        # it went unnoticed — but it would have silently picked the wrong one
        # in any archive carrying more than one sheet-bearing workbook.
        by_base = {n.rsplit("/", 1)[-1].lower(): n for n in zf.namelist()}
        for cand in candidates:
            if cand.lower() in by_base:
                with zf.open(by_base[cand.lower()]) as f:
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


def _manual_state_workbook(year: int) -> Path | None:
    """Newest hand-placed all-data workbook for `year`, or None."""
    if not OES_MANUAL_DIR.is_dir():
        return None
    matches = sorted(OES_MANUAL_DIR.glob(OES_MANUAL_GLOB.format(year=year)),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


# AREA_TYPE codes, per the "Field Descriptions" sheet these workbooks ship:
#   1 = U.S. (national)   2 = State   3 = Territory   4 = MSA   6 = Nonmetro
_AREA_NATIONAL = "1"
_AREA_STATE    = "2"

# Disk cache per (area_type, year). Vintage-keyed by construction (the year is
# in the filename), so a new vintage is a cache miss and these cannot hide
# staleness — VINTAGE-KEYED in scripts/audit_cache_freshness.py's taxonomy.
_SLICE_CACHE = {
    _AREA_STATE:    "oes_allstates_{year}.parquet",
    _AREA_NATIONAL: "oes_national_{year}.parquet",
}

# Parsed slices keyed by (area_type, year), for the life of one process. See
# _load_area_slice for why the disk cache beside it is the one that matters.
_SLICE_MEMO: dict[tuple[str, int], pd.DataFrame] = {}


def _state_tiers(yy: str, year: int) -> list[tuple[str, str, str, list[str] | None]]:
    """Acquisition tiers for STATE rows, cheapest first (7 MB before 80 MB)."""
    return [
        (OES_STATE_URL.format(yy=yy), f"oesm{yy}st.zip",  "state-only ZIP", None),
        (OES_ALL_URL.format(yy=yy),   f"oesm{yy}all.zip", "all-areas ZIP",  None),
    ]


def _national_tiers(yy: str, year: int) -> list[tuple[str, str, str, list[str] | None]]:
    """Acquisition tiers for NATIONAL industry rows, cheapest first.

    The state-only ZIP is deliberately absent — it contains no AREA_TYPE 1
    rows at all, so it can never satisfy a national request.

    in4 is tried before all-areas because it is smaller (~31 MB vs ~79 MB) AND
    because BLS has already done the granularity work inside it: the explicit
    natsector member is one row per (sector NAICS, occupation), with a single
    appropriate ownership aggregate per sector. Verified on 2023: 16,376 rows,
    AREA_TYPE all 1, I_GROUP all 'sector', own_code one value per NAICS
    (62 -> 58, 71/72 -> 57, the rest -> 5).
    """
    return [
        (OES_INDUS_URL.format(yy=yy), f"oesm{yy}in4.zip", "industry ZIP (natsector)",
         [f"natsector_M{year}_dl.xlsx", f"natsector_M{year}_dl.xls"]),
        (OES_ALL_URL.format(yy=yy),   f"oesm{yy}all.zip", "all-areas ZIP", None),
    ]


def _load_area_slice(
    year: int,
    cache_dir: Path,
    area_type: str,
    tiers: list[tuple[str, str, str, list[str] | None]],
) -> pd.DataFrame:
    """OES rows for one AREA_TYPE and year, from whichever tier can supply them.

    Falls through the given tiers, then to a hand-placed workbook. Raises
    requests.HTTPError if every network tier reports the file unavailable and
    no manual workbook exists — the caller logs and skips the year.

    Why this caches the parsed slice to DISK and not just to memory: the
    all-areas workbook is ~80 MB / ~414k rows and costs a minute-plus to parse,
    fetch_oes_state is called once per state, and refresh_dashboard.py runs
    run_forecast.py as a SEPARATE SUBPROCESS per state. An in-process memo
    therefore buys nothing across a five-state refresh — it would parse the same
    workbook five times for 2024 and five more for 2025. bulk_cache already
    shares the download; this shares the parse. The slices are small (36,600
    state rows, ~178k national) so the caches are a few MB at most.
    """
    memo_key = (area_type, year)
    if memo_key in _SLICE_MEMO:
        return _SLICE_MEMO[memo_key]

    slice_cache = cache_dir / _SLICE_CACHE[area_type].format(year=year)
    if slice_cache.is_file():
        try:
            df = pd.read_parquet(slice_cache)
        except Exception as exc:
            print(f"    [cache] {slice_cache.name} unreadable ({exc}); re-parsing")
        else:
            print(f"    [cache] OES {year} area_type={area_type} ({len(df):,} rows)")
            _SLICE_MEMO[memo_key] = df
            return df

    yy = _year_to_yy(year)
    raw: pd.DataFrame | None = None
    last_exc: requests.HTTPError | None = None

    for url, key, label, candidates in tiers:
        try:
            zip_bytes = bulk_cache.cached_download(
                key, lambda u=url, l=label: _download_zip(u, f"OES {year} {l}"))
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status in _UNAVAILABLE_STATUSES:
                print(f"    OES {year}: {label} unavailable ({status}) — trying next tier")
                last_exc = exc
                continue
            raise
        raw = _read_oes_excel_from_zip(zip_bytes, year, candidates)
        print(f"    OES {year}: served by {label}")
        break

    if raw is None:
        manual = _manual_state_workbook(year)
        if manual is None:
            print(f"    OES {year}: no reachable URL and no manual workbook at "
                  f"{OES_MANUAL_DIR / OES_MANUAL_GLOB.format(year=year)}")
            raise last_exc if last_exc else FileNotFoundError(
                f"OES {year}: no acquisition tier available")
        print(f"    OES {year}: served by manual workbook {manual.name}")
        raw = pd.read_excel(manual, dtype=str)

    full = _normalise_cols(raw)

    # AREA_TYPE is load-bearing for any file that carries more than one area
    # level. Without it the AREA match in _parse_state_oes would admit MSA rows,
    # since CBSA codes such as 20020 (Dothan, AL) and 20260 (Duluth, MN-WI)
    # start with "20" and would be counted as Kansas — 2,922 such rows in the
    # 2024 file. Files that carry only one level (the state-only ZIP, in4) may
    # omit the column, in which case every row is the level we asked for.
    if "area_type" not in full.columns:
        df = full
    else:
        at = full["area_type"].astype(str).str.strip()
        df = full[at == area_type].copy()
        print(f"      AREA_TYPE=={area_type} filter: {len(full):,} -> {len(df):,} rows")

        # Opportunistically cache the OTHER slices we already hold, so the
        # sibling layer does not re-parse the same 80 MB workbook. Guarded on
        # non-empty: caching an empty slice would later read back as "this year
        # has no national data" and silently drop the year.
        for other, name in _SLICE_CACHE.items():
            if other == area_type:
                continue
            spare = full[at == other]
            spare_path = cache_dir / name.format(year=year)
            if spare.empty or spare_path.is_file():
                continue
            try:
                spare.to_parquet(spare_path, index=False)
                print(f"      [saved] {spare_path.name} ({len(spare):,} rows, "
                      f"area_type={other}, from the same parse)")
            except Exception as exc:
                print(f"      could not cache {spare_path.name} ({exc}); continuing")

    if df.empty:
        raise ValueError(
            f"OES {year}: no AREA_TYPE=={area_type} rows in the file that served "
            f"it. Fail rather than cache an empty slice, which would read back "
            f"as 'this year has no data'.")

    try:
        df.to_parquet(slice_cache, index=False)
        print(f"      [saved] {slice_cache.name} ({len(df):,} rows)")
    except Exception as exc:
        # A cache that cannot be written must never fail the fetch.
        print(f"      could not cache {slice_cache.name} ({exc}); continuing")

    _SLICE_MEMO[memo_key] = df
    return df


def _load_state_rows(year: int, cache_dir: Path) -> pd.DataFrame:
    """All-state (AREA_TYPE 2) OES rows for `year`."""
    return _load_area_slice(year, cache_dir, _AREA_STATE,
                            _state_tiers(_year_to_yy(year), year))


def _load_national_rows(year: int, cache_dir: Path) -> pd.DataFrame:
    """National (AREA_TYPE 1) OES industry rows for `year`."""
    return _load_area_slice(year, cache_dir, _AREA_NATIONAL,
                            _national_tiers(_year_to_yy(year), year))


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

    # AREA for a state row is the zero-padded 2-digit state FIPS ("01", "20"),
    # verified on both the state-only and all-areas files. The old comment here
    # claimed a 7-character "2000000" form and matched a 2-char PREFIX of the
    # zero-stripped value; that happened to work on the state-only file (where
    # AREA is exactly 2 chars) but is what would have admitted MSA rows from the
    # all-areas file. Exact equality is correct for every tier and is belt-and-
    # braces with the AREA_TYPE==2 filter in _load_state_rows.
    if "area" not in df.columns:
        raise KeyError(f"No AREA column found in OES {year}. Columns: {list(df.columns)}")

    df = df[df["area"].astype(str).str.strip().str.zfill(2) == sf].copy()
    if df.empty:
        raise ValueError(
            f"OES {year}: no rows for state FIPS {sf}. This means the AREA "
            f"convention changed — fail rather than publish an empty state.")

    # Cross-industry rows: OES uses 'NAICS' = '000000' or blank for all-industry
    if "naics" in df.columns:
        df = df[df["naics"].astype(str).str.strip().isin(["000000", "0", "", "nan"])]

    # Filter to detailed occupations (exclude major group summaries).
    # O_GROUP is the authoritative field and is present in every tier; the
    # regex/_GROUP_CODES pair below is kept as a fallback and as a second
    # opinion. On KS 2024 the two agree exactly (687 rows either way), which is
    # also confirmation that OEWS state output carries no "minor"/"broad"
    # rollup rows that the regex alone would have let through.
    if "o_group" in df.columns:
        df = df[df["o_group"].astype(str).str.strip().str.lower() == "detailed"]
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

    # Per-year sub-caches are vintage-keyed; this combined shortcut is not,
    # and it is checked first — so it was the thing serving 2023 forever.
    combined_cache = cache_dir / f"oes_state_s{sf}.parquet"
    cached = load_fresh_cache(combined_cache, years, f"OES state {sf}")
    if cached is not None:
        return cached

    frames = []
    for year in years:
        year_cache = cache_dir / f"oes_state_s{sf}_{year}.parquet"
        if year_cache.exists():
            print(f"  [cache] OES state {sf} {year}")
            frames.append(pd.read_parquet(year_cache))
            continue

        try:
            raw     = _load_state_rows(year, cache_dir)
            year_df = _parse_state_oes(raw, sf, year)
            year_df.to_parquet(year_cache, index=False)
            print(f"    [saved] oes_state_s{sf}_{year}.parquet  ({len(year_df)} occupations)")
            frames.append(year_df)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", "?")
            print(f"    Warning: OES state {year} unavailable ({status}) — skipping")
        except Exception as exc:
            print(f"    Warning: OES state {year} failed — {exc}")
        time.sleep(2)

    if not frames:
        return pd.DataFrame(columns=["state_fips", "year", "occ_code", "occ_title",
                                     "tot_emp", "h_median", "a_median"])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["year", "occ_code"]).reset_index(drop=True)
    save_if_complete(df, combined_cache, years,
                     sorted(df["year"].unique()), f"OES state {sf}")
    return df


# ── Industry OES (national, NAICS 4-digit) ────────────────────────────────────

def _parse_industry_oes(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """
    Parse national industry OES file: keep sector-relevant NAICS rows,
    detailed occupations only.
    """
    df = _normalise_cols(df)

    # ── Collapse BOTH nesting hierarchies before any aggregation ─────────────
    # OEWS publishes the same employment at several levels on two independent
    # axes, and this parser used to keep every level of both. Consumers such as
    # top_occupations_by_sector() sum tot_emp across whatever rows they are
    # handed, so nested rows stacked. Measured on 2023 before this fix:
    #
    #   industry axis   sector 23 ⊃ 238000 ⊃ 238200 ⊃ 238210 …   → 3.0-4.4x
    #   occupation axis minor 47-2000 ⊃ broad 47-2060 ⊃ detailed 47-2061
    #                   → 51.9% of all rows were GROUP rows, and the "top
    #                     occupations" tables ranked aggregates against their
    #                     own children (Registered Nurses appeared twice, as
    #                     broad 29-1140 and detailed 29-1141, same employment)
    #
    # Electricians (47-2111) in Skilled Trades summed to 2,245,100 against a
    # true sector total of 569,740. Both filters use the file's own
    # authoritative level columns rather than inferring level from code shape.

    # Industry axis: sector-level rows only. This is what makes the in4 and
    # all-areas files interchangeable here — both carry exactly one sector-level
    # code set per dashboard sector (62 / 71,72 / 51,54 / 22,23,81 / 31-33).
    # Using I_GROUP rather than a code-length rule also excludes the
    # "sector, ownership" variants, which the all-areas file adds.
    if "i_group" in df.columns:
        before = len(df)
        df = df[df["i_group"].astype(str).str.strip().str.lower() == "sector"]
        print(f"      I_GROUP=='sector' filter: {before:,} -> {len(df):,} rows")

    # Occupation axis: detailed occupations only — the same filter
    # _parse_state_oes uses. Note the regex/_GROUP_CODES pair below removes only
    # the 24 MAJOR groups; minor and broad groups pass it. That was harmless in
    # the state file (which publishes no minor/broad rows) and badly wrong here.
    if "o_group" in df.columns:
        before = len(df)
        df = df[df["o_group"].astype(str).str.strip().str.lower() == "detailed"]
        print(f"      O_GROUP=='detailed' filter: {before:,} -> {len(df):,} rows")

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

    # ── Ownership axis: assert it away rather than sum over it ───────────────
    # OES OWN_CODE values are OVERLAPPING aggregates, not a partition — 57
    # (Private + Postal) contains 5 (Private), and 123 (Fed + State + Local)
    # contains 235 (State + Local). Summing across them would be a third
    # double-count on top of the industry and occupation axes.
    #
    # It does not arise today: the natsector member publishes exactly ONE
    # ownership aggregate per sector, chosen per sector (verified 2023 —
    # 62 -> 58, 71/72 -> 57, 22/23/31-33/51/54/81 -> 5), so every
    # (naics, occ_code) is unique. This guard exists because the all-areas
    # fallback is a different file and could differ. Fail loudly instead of
    # silently inflating: a human should decide which aggregate to keep.
    dupes = df.duplicated(subset=["naics", "occ_code"], keep=False)
    if dupes.any():
        sample = (df[dupes][["naics", "occ_code", "own_code", "tot_emp"]]
                  .head(8).to_string(index=False)
                  if "own_code" in df.columns else
                  df[dupes][["naics", "occ_code"]].head(8).to_string(index=False))
        raise ValueError(
            f"Industry OES {year}: {int(dupes.sum())} rows share a "
            f"(naics, occ_code) key after the sector/detailed filters, so "
            f"something still nests — most likely an OWN_CODE split, whose "
            f"codes are overlapping aggregates and must not be summed. "
            f"Sample:\n{sample}")

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
        years = OES_SECTOR_YEARS
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    combined_cache = cache_dir / "oes_by_sector.parquet"
    cached = load_fresh_cache(combined_cache, years, "OES by sector (combined)")
    if cached is not None:
        return cached

    frames = []
    for year in years:
        year_cache = cache_dir / f"oes_sector_{year}.parquet"
        if year_cache.exists():
            print(f"  [cache] OES sector {year}")
            frames.append(pd.read_parquet(year_cache))
            continue

        try:
            raw     = _load_national_rows(year, cache_dir)
            year_df = _parse_industry_oes(raw, year)
            year_df.to_parquet(year_cache, index=False)
            print(f"    [saved] oes_sector_{year}.parquet  "
                  f"({len(year_df)} occupation-sector rows)")
            frames.append(year_df)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", "?")
            print(f"    Warning: OES industry {year} unavailable "
                  f"({status}) — skipping")
        except Exception as exc:
            print(f"    Warning: OES industry {year} failed — {exc}")
        time.sleep(2)

    if not frames:
        return pd.DataFrame(columns=["year", "sector", "naics", "occ_code",
                                     "occ_title", "tot_emp", "a_median"])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["year", "sector", "occ_code"]).reset_index(drop=True)
    save_if_complete(df, combined_cache, years,
                     sorted(df["year"].unique()), "OES by sector (combined)")
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
