"""
fetch_bls_proj.py
Fetches BLS National Employment Projections and Kansas state-level
occupational employment projections for use as a demand-side reference
layer in the dashboard Industry Forecast tab.

This is a DISPLAY LAYER ONLY — projections are shown alongside the
dashboard's OLS employment trend but do not change the cohort model.

Data sources:
  National (BLS Employment Projections program, free):
    https://www.bls.gov/emp/ind-occ-matrix/occ_xls.zip
    or specific table: https://www.bls.gov/emp/ep_table_102.xlsx
    Published annually, on the last Thursday of August (2024–34 landed
    2025-08-28; 2025–35 landed 2026-08-27). Current adopted cycle: 2025–2035.
    bls.gov 403s scripted requests, so the workbook is placed by hand — see
    _find_manual_workbook() for the accepted filenames.

  Kansas state projections: NOT fetched by this module. They come from the
  adopted KDOL workbooks in data/kdol_proj/, parsed by
  scripts/parse_manual_ks_proj.py (industry) and
  scripts/parse_manual_ks_occproj.py (occupational). Current cycle 2024–2034.

SOC major group → dashboard sector mapping:
  15-xxxx Computer/mathematical      → IT/Computer Services
  29-xxxx Healthcare practitioners   → Healthcare
  31-xxxx Healthcare support         → Healthcare
  35-xxxx Food prep/serving          → Hospitality & Entertainment
  39-xxxx Personal care/service      → Hospitality & Entertainment
  47-xxxx Construction/extraction    → Skilled Trades
  49-xxxx Install/maintenance/repair → Skilled Trades
  51-xxxx Production occupations     → Manufacturing

Output DataFrame from fetch_national_projections():
  projection_source (str), base_year (int), proj_year (int),
  occ_code (str), occ_title (str), sector (str | None),
  base_emp (float), proj_emp (float), emp_change_pct (float),
  annual_openings (float | None), median_annual_wage (float | None)

Output from sector_demand_outlook():
  sector, base_year, proj_year, base_emp_total, proj_emp_total,
  emp_change_pct, projection_source
"""

import io
import re
import time
import zipfile
import warnings
import requests
import pandas as pd
from pathlib import Path

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}

# BLS Employment Projections — URL candidates (table numbering varies by cycle)
_BLS_PROJ_CANDIDATES: list[str] = [
    "https://www.bls.gov/emp/ind-occ-matrix/occ_xls.zip",          # full matrix zip
    "https://www.bls.gov/emp/ep_table_102.xlsx",                    # table 1.2 occupation projections
    "https://www.bls.gov/emp/ep_table_101.xlsx",                    # table 1.1
    "https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm",
]

# NOTE: _KS_PROJ_CANDIDATES and fetch_ks_state_projections() were removed
# 2026-08-27. Kansas projections come from the adopted KDOL 2024-2034 workbooks
# in data/kdol_proj/ via scripts/parse_manual_ks_proj.py (industry) and
# scripts/parse_manual_ks_occproj.py (occupational) — not from this module.

# SOC major group (first 2 digits) → dashboard sector
SOC2_TO_SECTOR: dict[str, str] = {
    "15": "IT/Computer Services",
    "11": "IT/Computer Services",       # management — loosely aligned
    "29": "Healthcare",
    "31": "Healthcare",
    "35": "Hospitality & Entertainment",
    "39": "Hospitality & Entertainment",
    "47": "Skilled Trades",
    "49": "Skilled Trades",
    "51": "Manufacturing",
    "17": "Manufacturing",              # engineering/architecture
}


# ── Download helpers ──────────────────────────────────────────────────────────

def _try_download(candidates: list[str]) -> tuple[bytes, str] | tuple[None, None]:
    """Try each URL; return (content, url) for the first 200 OK with substance."""
    for url in candidates:
        try:
            resp = requests.get(url, headers=_HTTP_HEADERS, timeout=300, stream=True)
            if resp.status_code != 200:
                continue
            chunks = []
            for chunk in resp.iter_content(1024 * 512):
                chunks.append(chunk)
            content = b"".join(chunks)
            if len(content) > 5000:   # skip empty/error pages
                print(f"    Downloaded from {url.rsplit('/', 1)[-1]}")
                return content, url
        except Exception:
            pass
    return None, None


# ── Excel / ZIP parsing ───────────────────────────────────────────────────────

def _read_excel_bytes(content: bytes) -> pd.DataFrame | None:
    """Parse an Excel binary blob; try all sheets.

    BLS Employment Projections workbooks (e.g. occupation.xlsx) have:
      - An "Index" sheet listing other sheets (skip)
      - Per-table sheets named "Table 1.1", "Table 1.2", etc., where
        row 0 is the title, row 1 is the column header, data starts row 2.
    Prefer Table 1.2 (occupational projections) when present; fall back
    to scanning all non-Index sheets for one with SOC/occupation columns.
    """
    try:
        xls = pd.ExcelFile(io.BytesIO(content))
    except Exception:
        return None

    preferred = [s for s in xls.sheet_names if s.lower().replace(" ", "") == "table1.2"]
    other     = [s for s in xls.sheet_names
                 if s.lower() != "index" and s not in preferred]
    candidates = preferred + other

    for sheet in candidates:
        for header_row in (1, 2, 0, 3):
            try:
                df = xls.parse(sheet, dtype=str, header=header_row)
            except Exception:
                continue
            cols_lower = [str(c).lower() for c in df.columns]
            if any(
                ("matrix code" in c) or ("soc" in c) or ("occ_code" in c)
                or ("occupation code" in c) or ("occupation title" in c)
                for c in cols_lower
            ):
                return df
    return None


def _read_from_zip(content: bytes) -> pd.DataFrame | None:
    """Extract and combine all relevant Excel sheets from a zip file."""
    frames = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith((".xlsx", ".xls")):
                    continue
                try:
                    with zf.open(name) as f:
                        df = _read_excel_bytes(f.read())
                    if df is not None and len(df) > 10:
                        df["_source_sheet"] = name
                        frames.append(df)
                except Exception:
                    pass
    except Exception:
        return None
    return pd.concat(frames, ignore_index=True) if frames else None


# ── Column detection ──────────────────────────────────────────────────────────

_OCC_CODE_HINTS  = ["matrix code", "occ_code", "soc_code", "soc",
                    "occupation code", "2024 soc code", "2022 soc code"]
_OCC_TITLE_HINTS = ["matrix title", "occ_title", "occupation title",
                    "occupation", "title"]

# The employment columns are detected by PATTERN, not by year literal.
#
# These hints used to name the years outright ("employment, 2024" /
# "employment, 2034"). That silently broke on every new cycle: a 2025-35
# workbook labels the columns "Employment, 2025" / "Employment, 2035", so
# neither hint matched, _parse_proj_df returned an empty frame, and the caller
# printed a warning rather than raising. The 2025-35 cycle published 2026-08-27
# and hit exactly that. Matching "Employment, <4 digits>" instead means the next
# cycle needs no edit here.
#
# Anchored on the full column name so it cannot match the neighbouring
# "Employment distribution, percent, 2025" or "Employment change, numeric,
# 2025-35" columns, which also begin with "Employment".
_EMP_YEAR_RE = re.compile(r"^employment,?\s+(\d{4})$")

# Kept only as a fallback for workbooks that do not use the National Employment
# Matrix column naming (e.g. an older zip layout). The year literals below are
# deliberately generic.
_BASE_EMP_HINTS  = ["base year employment", "base employment"]
_PROJ_EMP_HINTS  = ["projected employment", "proj employment"]
# Order matters: most specific first so "employment change, percent" wins
# over generic "percent" columns like "Employment distribution, percent, 2024".
_PCT_CHG_HINTS   = [
    "employment change, percent", "employment change, percent, 2024",
    "pct_change", "% change", "change (%)", "percent change",
]
_OPENINGS_HINTS  = ["occupational openings", "openings", "annual openings",
                    "total openings"]
_WAGE_HINTS      = ["median annual wage", "median wage", "annual median"]


def _find_col(cols: list[str], hints: list[str]) -> str | None:
    cols_lower = [c.lower().strip() for c in cols]
    for hint in hints:
        hl = hint.lower()
        for i, col_l in enumerate(cols_lower):
            if hl in col_l:
                return cols[i]
    return None


def detect_cycle(cols: list[str]) -> tuple[int | None, int | None, str | None, str | None]:
    """Detect the projection cycle from "Employment, <year>" column names.

    Returns (base_year, proj_year, base_col, proj_col). The earliest year is the
    base and the latest is the projection target, which is how every National
    Employment Matrix table is laid out. Any element is None when it cannot be
    determined; the caller decides whether that is fatal.
    """
    found: dict[int, str] = {}
    for c in cols:
        m = _EMP_YEAR_RE.match(str(c).lower().strip())
        if m:
            found[int(m.group(1))] = c

    if not found:
        return None, None, None, None

    years = sorted(found)
    base, proj = years[0], (years[-1] if len(years) > 1 else None)
    return base, proj, found[base], (found[proj] if proj else None)


def _parse_proj_df(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Parse a raw projection DataFrame into a normalised output table.
    Returns empty DataFrame if required columns are missing.
    """
    cols = list(raw.columns)
    occ_code_col  = _find_col(cols, _OCC_CODE_HINTS)
    occ_title_col = _find_col(cols, _OCC_TITLE_HINTS)

    # Pattern detection first; the generic hints are only a fallback for
    # workbooks that do not use National Employment Matrix column naming.
    _, _, base_emp_col, proj_emp_col = detect_cycle(cols)
    if base_emp_col is None:
        base_emp_col = _find_col(cols, _BASE_EMP_HINTS)
    if proj_emp_col is None:
        proj_emp_col = _find_col(cols, _PROJ_EMP_HINTS)
    pct_chg_col   = _find_col(cols, _PCT_CHG_HINTS)
    openings_col  = _find_col(cols, _OPENINGS_HINTS)
    wage_col      = _find_col(cols, _WAGE_HINTS)

    if occ_code_col is None or base_emp_col is None:
        return pd.DataFrame()

    def _num(series: pd.Series) -> pd.Series:
        return pd.to_numeric(
            series.astype(str).str.replace(",", "").str.replace("%", "").str.strip(),
            errors="coerce",
        )

    result = pd.DataFrame()
    result["occ_code"]  = raw[occ_code_col].astype(str).str.strip()
    result["occ_title"] = raw[occ_title_col].astype(str).str.strip() if occ_title_col else ""
    result["base_emp"]  = _num(raw[base_emp_col])
    result["proj_emp"]  = _num(raw[proj_emp_col]) if proj_emp_col else pd.NA
    result["emp_change_pct"] = _num(raw[pct_chg_col]) if pct_chg_col else pd.NA
    result["annual_openings"] = _num(raw[openings_col]) if openings_col else pd.NA
    result["median_annual_wage"] = _num(raw[wage_col]) if wage_col else pd.NA

    # Filter to valid SOC codes (format: DD-DDDD)
    valid = result["occ_code"].str.match(r"^\d{2}-\d{4}$")
    result = result[valid].copy()

    # Compute pct change if not present
    if result["emp_change_pct"].isna().all() and not result["proj_emp"].isna().all():
        result["emp_change_pct"] = (
            (result["proj_emp"] - result["base_emp"]) / result["base_emp"] * 100
        ).round(1)

    # Map SOC to sector
    result["soc2"]   = result["occ_code"].str[:2]
    result["sector"] = result["soc2"].map(SOC2_TO_SECTOR)

    return result.drop(columns=["soc2"]).dropna(subset=["occ_code"])


# ── Public entry points ───────────────────────────────────────────────────────

def _find_manual_workbook(cache_dir: Path, base_year: int, proj_year: int) -> Path | None:
    """Locate the manually-downloaded projections workbook.

    Priority, most specific first:
      1. bls_proj_national_{base}_{proj}.xlsx — names the cycle explicitly
      2. newest bls_proj_national_<digits>*.xlsx — any vintage-named workbook
      3. bls_proj_national_manual.xlsx — the legacy vintage-less name

    The glob in (2) requires a leading digit so it cannot match "manual" itself;
    without that, lexical sort would rank "manual" above "2025_2035" ("m" > "2")
    and the legacy file would win forever.
    """
    exact = cache_dir / f"bls_proj_national_{base_year}_{proj_year}.xlsx"
    if exact.exists():
        return exact

    vintaged = sorted(cache_dir.glob("bls_proj_national_[0-9]*.xlsx"))
    if vintaged:
        return vintaged[-1]

    legacy = cache_dir / "bls_proj_national_manual.xlsx"
    return legacy if legacy.exists() else None


def fetch_national_projections(
    cache_dir: Path | None = None,
    base_year: int = 2025,
    proj_year: int = 2035,
) -> pd.DataFrame:
    """
    Fetch BLS National Employment Projections (2025–2035 cycle by default).

    The defaults name the cycle and are part of the cache key
    (bls_proj_national_{base}_{proj}.parquet), so bumping them is what forces a
    newly-downloaded workbook to be reread. Leaving them stale means the old
    parquet is served on sight and the new workbook is never opened — that is
    how the 2025-35 cycle sat unadopted after publishing 2026-08-27.

    Parameters
    ----------
    cache_dir : parquet cache directory
    base_year : projection base year (default 2025)
    proj_year : projection target year (default 2035)

    Returns
    -------
    DataFrame: projection_source, base_year, proj_year, occ_code, occ_title,
    sector, base_emp, proj_emp, emp_change_pct, annual_openings, median_annual_wage
    """
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / f"bls_proj_national_{base_year}_{proj_year}.parquet"
    if cache_file.exists():
        print(f"  [cache] BLS national projections {base_year}–{proj_year}")
        return pd.read_parquet(cache_file)

    content, url = _try_download(_BLS_PROJ_CANDIDATES)

    if content is None:
        # bls.gov 403s scripted requests, so the manual workbook is the NORMAL
        # path here, not an error. Warn only when it is actually missing — the
        # warning used to fire on every refresh even with the file sitting in
        # place, which trains the operator to ignore it.
        manual = _find_manual_workbook(cache_dir, base_year, proj_year)
        if manual is not None:
            print(f"    BLS download unavailable (expected); loading manual "
                  f"workbook: {manual.name}")
            raw = _read_excel_bytes(manual.read_bytes())
            if raw is None or raw.empty:
                print("  Warning: manual BLS file could not be parsed")
                return pd.DataFrame()
        else:
            warnings.warn(
                "\n\nBLS National Employment Projections could not be downloaded "
                "and no manual workbook was found.\n"
                "Place the workbook manually at:\n"
                f"  {cache_dir / f'bls_proj_national_{base_year}_{proj_year}.xlsx'}\n"
                "(a vintage-named bls_proj_national_<base>_<proj>.xlsx is preferred; "
                "the legacy\n bls_proj_national_manual.xlsx is still accepted)\n"
                "Source: https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm\n"
                "Required columns: occ_code (SOC), occ_title, base employment,\n"
                "                  projected employment, % change\n",
                UserWarning,
                stacklevel=2,
            )
            return pd.DataFrame(columns=[
                "projection_source", "base_year", "proj_year", "occ_code",
                "occ_title", "sector", "base_emp", "proj_emp",
                "emp_change_pct", "annual_openings", "median_annual_wage",
            ])
    else:
        # Determine if content is zip or Excel
        if content[:4] == b"PK\x03\x04":   # ZIP magic bytes
            raw = _read_from_zip(content)
        else:
            raw = _read_excel_bytes(content)

        if raw is None or raw.empty:
            return pd.DataFrame()

    # Guard against labelling a workbook with a cycle it does not contain. The
    # years are a caller argument but the workbook is the authority, and a
    # mismatch means the cache key, the dashboard's cycle label, and the actual
    # numbers have diverged. Refuse rather than write a mislabelled parquet.
    found_base, found_proj, _, _ = detect_cycle(list(raw.columns))
    if found_base is not None:
        requested = (base_year, proj_year)
        found = (found_base, found_proj)
        if found_proj is not None and found != requested:
            raise ValueError(
                f"BLS projections cycle mismatch: the workbook holds "
                f"{found_base}–{found_proj} but this call requested "
                f"{base_year}–{proj_year}. Pass base_year/proj_year matching the "
                f"workbook, or update the defaults in fetch_national_projections(). "
                f"Refusing to write bls_proj_national_{base_year}_{proj_year}.parquet "
                f"from {found_base}–{found_proj} data."
            )

    df = _parse_proj_df(raw)
    if df.empty:
        print("  Warning: could not parse BLS projections file")
        return df

    df["projection_source"] = "BLS_National"
    df["base_year"]  = base_year
    df["proj_year"]  = proj_year

    df = df[["projection_source", "base_year", "proj_year", "occ_code",
             "occ_title", "sector", "base_emp", "proj_emp",
             "emp_change_pct", "annual_openings", "median_annual_wage"]]

    df.to_parquet(cache_file, index=False)
    print(f"  [saved] bls_proj_national_{base_year}_{proj_year}.parquet  ({len(df)} occupations)")
    return df


# fetch_ks_state_projections() lived here until 2026-08-27. It was a dead path:
# it downloaded from two KDOL URLs that no longer resolve, fell back to
# bls_proj_cache/ks_proj_manual.xlsx (a file that never existed — the real legacy
# export is .xls, an HTML <table> that pd.ExcelFile cannot open), and hardcoded
# base_year=2020 / proj_year=2030, which the adopted 2024-2034 cycle contradicts.
# Its only observable effect was a UserWarning on every refresh telling the
# operator to place a file that would not have worked anyway. It contributed zero
# rows: bls_proj_occupations.parquet has never contained a KS_State row.
#
# Kansas projections are parsed from the KDOL workbooks in data/kdol_proj/ by
# scripts/parse_manual_ks_proj.py and scripts/parse_manual_ks_occproj.py.


def sector_demand_outlook(proj_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate occupation-level projections to sector level.

    Returns DataFrame: sector, base_year, proj_year, projection_source,
    base_emp_total, proj_emp_total, emp_change_pct (weighted average).
    """
    df = proj_df[proj_df["sector"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    result = []
    for (sector, source, by, py), grp in df.groupby(
            ["sector", "projection_source", "base_year", "proj_year"]):
        sub = grp.dropna(subset=["base_emp"])
        base_total = float(sub["base_emp"].sum())
        proj_total = float(sub["proj_emp"].sum()) if not sub["proj_emp"].isna().all() else None
        chg_pct    = ((proj_total - base_total) / base_total * 100) \
                      if proj_total and base_total else None
        result.append({
            "sector":            sector,
            "projection_source": source,
            "base_year":         by,
            "proj_year":         py,
            "base_emp_total":    round(base_total, 0),
            "proj_emp_total":    round(proj_total, 0) if proj_total else None,
            "emp_change_pct":    round(chg_pct, 1) if chg_pct else None,
        })

    return pd.DataFrame(result).sort_values(["projection_source", "sector"]).reset_index(drop=True)
