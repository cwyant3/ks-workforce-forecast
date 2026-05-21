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
import sys
from pathlib import Path

import pandas as pd

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
