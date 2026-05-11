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
    Published every 2 years. Current cycle: 2024–2034.

  Kansas state projections (KDOL LMIS, free):
    https://www.dol.ks.gov/lmis/employment-projections
    Biennial. Last published: 2020–2030 (note: KDOL download URLs not stable).

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

# Kansas state projection candidates (KDOL LMIS — URLs not stable)
_KS_PROJ_CANDIDATES: list[str] = [
    "https://www.dol.ks.gov/docs/default-source/lmis-library/"
    "employment-projections/ks-long-term-projections.xlsx",
    "https://www.dol.ks.gov/docs/default-source/lmis-library/"
    "employment-projections/kansas-long-term-occupational-projections.xlsx",
]

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
    """Parse an Excel binary blob; try all sheets."""
    try:
        xls = pd.ExcelFile(io.BytesIO(content))
    except Exception:
        return None

    for sheet in xls.sheet_names:
        try:
            df = xls.parse(sheet, dtype=str)
            # Look for SOC code column
            cols_lower = [c.lower() for c in df.columns]
            if any("occ" in c or "soc" in c for c in cols_lower):
                return df
        except Exception:
            continue
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

_OCC_CODE_HINTS  = ["occ_code", "soc_code", "soc", "occupation code", "2024 soc code", "2022 soc code"]
_OCC_TITLE_HINTS = ["occ_title", "occupation title", "occupation", "title"]
_BASE_EMP_HINTS  = [
    "2024", "employment 2024", "employed 2024",
    "2022", "employment 2022", "base year employment", "employed 2022",
]
_PROJ_EMP_HINTS  = [
    "2034", "employment 2034", "employed 2034",
    "2032", "employment 2032", "projected employment", "employed 2032",
]
_PCT_CHG_HINTS   = ["percent", "% change", "pct_change", "change (%)"]
_OPENINGS_HINTS  = ["openings", "annual openings", "total openings"]
_WAGE_HINTS      = ["median annual wage", "median wage", "annual median"]


def _find_col(cols: list[str], hints: list[str]) -> str | None:
    cols_lower = [c.lower().strip() for c in cols]
    for hint in hints:
        hl = hint.lower()
        for i, col_l in enumerate(cols_lower):
            if hl in col_l:
                return cols[i]
    return None


def _parse_proj_df(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Parse a raw projection DataFrame into a normalised output table.
    Returns empty DataFrame if required columns are missing.
    """
    cols = list(raw.columns)
    occ_code_col  = _find_col(cols, _OCC_CODE_HINTS)
    occ_title_col = _find_col(cols, _OCC_TITLE_HINTS)
    base_emp_col  = _find_col(cols, _BASE_EMP_HINTS)
    proj_emp_col  = _find_col(cols, _PROJ_EMP_HINTS)
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

def fetch_national_projections(
    cache_dir: Path | None = None,
    base_year: int = 2024,
    proj_year: int = 2034,
) -> pd.DataFrame:
    """
    Fetch BLS National Employment Projections (2024–2034 cycle by default).

    Parameters
    ----------
    cache_dir : parquet cache directory
    base_year : projection base year (default 2024)
    proj_year : projection target year (default 2034)

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
        warnings.warn(
            "\n\nBLS National Employment Projections could not be downloaded.\n"
            "Place the file manually at:\n"
            f"  {cache_dir / 'bls_proj_national_manual.xlsx'}\n"
            "Source: https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm\n"
            "Required columns: occ_code (SOC), occ_title, base employment,\n"
            "                  projected employment, % change\n",
            UserWarning,
            stacklevel=2,
        )
        manual = cache_dir / "bls_proj_national_manual.xlsx"
        if manual.exists():
            raw = pd.read_excel(manual, dtype=str)
        else:
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


def fetch_ks_state_projections(
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch Kansas state-level occupational employment projections from KDOL LMIS.
    Falls back to manual file if KDOL download URL is unavailable.

    Returns same schema as fetch_national_projections() with
    projection_source = "KS_State".
    """
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / "bls_proj_ks_state.parquet"
    if cache_file.exists():
        print(f"  [cache] KS state projections")
        return pd.read_parquet(cache_file)

    content, _ = _try_download(_KS_PROJ_CANDIDATES)

    if content is None:
        manual = cache_dir / "ks_proj_manual.xlsx"
        if manual.exists():
            print(f"    Loading manual KS projections file")
            content = manual.read_bytes()
        else:
            warnings.warn(
                "\n\nKansas state projections not available.\n"
                "Place the file manually at:\n"
                f"  {cache_dir / 'ks_proj_manual.xlsx'}\n"
                "Source: https://www.dol.ks.gov/lmis/employment-projections\n",
                UserWarning,
                stacklevel=2,
            )
            return pd.DataFrame()

    raw = _read_excel_bytes(content)
    if raw is None:
        return pd.DataFrame()

    df = _parse_proj_df(raw)
    if df.empty:
        return df

    df["projection_source"] = "KS_State"
    # Infer base/proj year from column names if possible
    df["base_year"] = 2020
    df["proj_year"] = 2030

    df.to_parquet(cache_file, index=False)
    print(f"  [saved] bls_proj_ks_state.parquet  ({len(df)} occupations)")
    return df


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
