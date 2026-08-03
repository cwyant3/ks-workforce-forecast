"""
parse_manual_ks_proj.py
Parse the Kansas Department of Labor (KDOL LMIS) industry employment
projections file. The file is served as .xls but is actually an HTML
table (Akamai/Telerik report builder export).

This is INDUSTRY-level (NAICS) projection data, not occupational (SOC).
It complements — but does not replace — the BLS national occupational
projections parsed by fetch_bls_proj.fetch_national_projections().

Output file: data/outputs/ks_proj_industry.parquet
Schema:
    state_fips, naicscode, naicstitle, naicslvl (sector / 3-digit / 4-digit / 6-digit),
    estyear, projyear, estindprj, projindprj,
    nchg, pchg, grrate, openings, annualopenings,
    sector (mapped to dashboard sector when NAICS 2-digit is identifiable)

Maps each NAICS code to the 5 dashboard sectors using the 2-digit prefix.

Usage:
    python scripts/parse_manual_ks_proj.py
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# ── Published-workbook support ────────────────────────────────────────────────
# Since the 2024-2034 cycle KDOL also publishes a clean .xlsx ("2024-2034 KS
# Industry Projections.xlsx", LMIS, published July 2026) alongside the legacy
# Telerik HTML-as-.xls export. The published book is statewide-only but has
# real column headers and a stated vintage, so it is preferred when present.
# Its "Industry Level" is descriptive text where the Telerik export used a
# numeric naicslvl; map it back so sector_outlook()'s naicslvl == "2" filter
# keeps working.
_LEVEL_TO_NAICSLVL: dict[str, str] = {
    "total, all industries": "0",
    "supersector": "1",
    "industry sector (2-digit)": "2",
    "industry subsector (3-digit)": "3",
    "industry group (4-digit)": "4",
    "industry (5-digit)": "5",
    "industry (6-digit)": "6",
}


def _norm(col: object) -> str:
    """Collapse a multi-line Excel header into a lowercase single-space key."""
    return re.sub(r"\s+", " ", str(col)).strip().lower()


def _find(cols: list[str], *needles: str) -> str | None:
    """First column containing every needle (already-normalised keys)."""
    for c in cols:
        if all(n in c for n in needles):
            return c
    return None


def _years_from_columns(cols: list[str]) -> tuple[str | None, str | None]:
    """Read the projection vintage off the employment column headers.

    "2024 employment" -> base, "2034 projected employment" -> projected. Reading
    the years from the data instead of the filename means a new cycle needs no
    code change.
    """
    base = proj = None
    for c in cols:
        m = re.search(r"(?:19|20)\d{2}", c)
        if not m or "employment" not in c:
            continue
        if "projected" in c:
            proj = m.group(0)
        elif base is None:
            base = m.group(0)
    return base, proj


def _pct(series: pd.Series) -> pd.Series:
    """Published workbooks store rates as decimal fractions (0.0234); the
    Telerik export and every downstream consumer use percent (2.34)."""
    return (pd.to_numeric(
        series.astype(str).str.replace(",", "").str.replace("%", "").str.strip(),
        errors="coerce",
    ) * 100).round(4)


def parse_ks_proj_published(xlsx_path: Path, state_fips: str = "20") -> pd.DataFrame:
    """Parse KDOL's published industry-projections .xlsx into the Telerik schema."""
    xl = pd.ExcelFile(xlsx_path)
    sheet = next((s for s in xl.sheet_names if "industry projections" in s.lower()), None)
    if sheet is None:
        raise RuntimeError(
            f"No 'Industry Projections' sheet in {xlsx_path.name} "
            f"(sheets: {xl.sheet_names})"
        )
    raw = xl.parse(sheet, dtype=str, header=1)
    raw.columns = [_norm(c) for c in raw.columns]
    cols = list(raw.columns)

    lvl_col   = _find(cols, "industry level")
    code_col  = _find(cols, "industry code")
    title_col = _find(cols, "industry title")
    if not (lvl_col and code_col and title_col):
        raise RuntimeError(f"Unexpected industry columns in {xlsx_path.name}: {cols}")
    base_year, proj_year = _years_from_columns(cols)
    base_col = _find(cols, base_year or "", "employment") if base_year else None
    proj_col = _find(cols, "projected", "employment")
    chg_col  = _find(cols, "change in employment")
    pchg_col = _find(cols, "percent change")
    grr_col  = _find(cols, "growth rate")

    # Drop the trailing footnote/blank rows the workbook carries under the table.
    raw = raw[raw[code_col].notna() & raw[lvl_col].notna()].copy()

    result = pd.DataFrame()
    result["state_fips"] = [state_fips.zfill(2)] * len(raw)
    result["naicscode"]  = raw[code_col].astype(str).str.strip()
    result["naicstitle"] = raw[title_col].astype(str).str.strip()
    result["naicslvl"]   = raw[lvl_col].map(lambda v: _LEVEL_TO_NAICSLVL.get(_norm(v)))
    result["estyear"]    = base_year
    result["projyear"]   = proj_year
    result["estindprj"]  = pd.to_numeric(
        raw[base_col].astype(str).str.replace(",", ""), errors="coerce") if base_col else pd.NA
    result["projindprj"] = pd.to_numeric(
        raw[proj_col].astype(str).str.replace(",", ""), errors="coerce") if proj_col else pd.NA
    result["nchg"]       = pd.to_numeric(
        raw[chg_col].astype(str).str.replace(",", ""), errors="coerce") if chg_col else pd.NA
    result["pchg"]       = _pct(raw[pchg_col]) if pchg_col else pd.NA
    result["grrate"]     = _pct(raw[grr_col]) if grr_col else pd.NA
    # The published book carries no openings columns (the Telerik export's were
    # empty too), so these stay null rather than being invented.
    result["openings"] = pd.NA
    result["annualopenings"] = pd.NA
    result["sector"] = result["naicscode"].str[:2].map(NAICS2_TO_SECTOR)
    result["projection_source"] = "KS_State_Industry_Published"
    return result.reset_index(drop=True)


def is_published_workbook(path: Path) -> bool:
    """True for a real .xlsx; the legacy KDOL export is an HTML table named .xls."""
    return path.suffix.lower() == ".xlsx"

# NAICS 2-digit → dashboard sector (mirrors fetch_qcew.py SECTOR_NAICS)
NAICS2_TO_SECTOR: dict[str, str] = {
    "62": "Healthcare",
    "31": "Manufacturing", "32": "Manufacturing", "33": "Manufacturing",
    "71": "Hospitality & Entertainment", "72": "Hospitality & Entertainment",
    "51": "IT/Computer Services", "54": "IT/Computer Services",
    "22": "Skilled Trades", "23": "Skilled Trades", "81": "Skilled Trades",
}

_NUMERIC_COLS = {"estindprj", "projindprj", "nchg", "pchg", "grrate",
                 "openings", "annualopenings", "change", "annualchange"}


def parse_ks_proj(xls_path: Path, state_fips: str = "20") -> pd.DataFrame:
    tables = pd.read_html(xls_path)
    if not tables:
        raise RuntimeError(f"No tables found in {xls_path}")

    raw = tables[0]
    # First row is the column header (when the table is parsed without a real <thead>)
    raw.columns = [str(c).strip().lower() for c in raw.iloc[0]]
    raw = raw.iloc[1:].reset_index(drop=True)

    # Filter to the requested state
    sf = state_fips.zfill(2)
    raw = raw[raw["stfips"].astype(str).str.zfill(2) == sf].copy()

    # Numeric coercion
    for col in _NUMERIC_COLS:
        if col in raw.columns:
            raw[col] = pd.to_numeric(
                raw[col].astype(str).str.replace(",", "").str.replace("%", "").str.strip(),
                errors="coerce",
            )

    # Map sector from first 2 digits of NAICS
    raw["naicscode"] = raw["naicscode"].astype(str).str.strip()
    raw["sector"]    = raw["naicscode"].str[:2].map(NAICS2_TO_SECTOR)

    # Standard output columns (only keep what's relevant for the dashboard)
    keep = [c for c in [
        "stfips", "naicscode", "naicstitle", "naicslvl",
        "estyear", "projyear", "estindprj", "projindprj",
        "nchg", "pchg", "grrate", "openings", "annualopenings",
        "sector",
    ] if c in raw.columns]
    result = raw[keep].rename(columns={"stfips": "state_fips"})
    result["state_fips"] = result["state_fips"].astype(str).str.zfill(2)
    result["projection_source"] = "KS_State_Industry"
    return result.reset_index(drop=True)


def sector_outlook(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate industry projections to the 5 dashboard sectors."""
    sub = df[df["sector"].notna() & df["estindprj"].notna()].copy()
    if sub.empty:
        return pd.DataFrame()
    # Use only sector-level rows when available (naicslvl == '2' or similar);
    # otherwise sum all rows in the sector — risks double-counting if multiple
    # NAICS levels appear, so prefer the most aggregated rows.
    if "naicslvl" in sub.columns:
        # Lowest naicslvl number = most aggregated
        agg_levels = sub["naicslvl"].astype(str).str.strip()
        # Common KDOL convention: 2 = sector (2-digit), 3 = subsector, ...
        sub = sub[agg_levels.isin(["2", "02"])]
    grp = sub.groupby(["sector", "estyear", "projyear", "projection_source"],
                       as_index=False).agg(
        base_emp_total=("estindprj", "sum"),
        proj_emp_total=("projindprj", "sum"),
        annual_openings=("annualopenings", "sum"),
    )
    grp["emp_change_pct"] = (
        (grp["proj_emp_total"] - grp["base_emp_total"]) / grp["base_emp_total"] * 100
    ).round(1)
    return grp.sort_values("sector").reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="20")
    ap.add_argument("--input", default="data/bls_proj_cache/ks_proj_manual.xls")
    ap.add_argument("--output-dir", default="data/outputs")
    args = ap.parse_args()

    xls = Path(args.input)
    if not xls.exists():
        print(f"Input not found: {xls}", file=sys.stderr)
        sys.exit(1)

    if is_published_workbook(xls):
        df = parse_ks_proj_published(xls, args.state)
        print(f"Parsed {len(df)} industry-projection rows for state {args.state} "
              f"from published workbook {xls.name} "
              f"(vintage {df['estyear'].iloc[0]}-{df['projyear'].iloc[0]}, statewide only)")
    else:
        df = parse_ks_proj(xls, args.state)
        print(f"Parsed {len(df)} industry-projection rows for state {args.state}")
    print(df.head(5).to_string())

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw_out = out / "ks_proj_industry.parquet"
    df.to_parquet(raw_out, index=False)
    print(f"Saved: {raw_out}")

    outlook = sector_outlook(df)
    if not outlook.empty:
        outlook_out = out / "ks_proj_sector_outlook.parquet"
        outlook.to_parquet(outlook_out, index=False)
        print(f"Saved: {outlook_out}")
        print()
        print("Sector demand outlook (Kansas industry projections):")
        print(outlook.to_string(index=False))


if __name__ == "__main__":
    main()
