"""
parse_manual_ks_occproj.py
Parse the Kansas Department of Labor (KDOL LMIS) occupational employment
projections file (occproj__YYYYBBYYYY.xls) into parquet outputs the
dashboard can consume.

This is SOC-occupation-level KS-specific projection data with KDOL's
in-demand / DemandRank flags — strategically the highest-value layer for
WSU Tech program design (answers "what occupations should we train for
in Kansas?").

File comes from KDOL Telerik report builder served as HTML-disguised-as-.xls.

Outputs (data/outputs/):
  ks_occ_proj_state_s20.parquet    — state-level SOC occupations
  ks_occ_proj_region_s20.parquet   — sub-state region (LWDA) SOC occupations
  ks_occ_in_demand_top_s20.parquet — DemandRank-sorted top in-demand list
  ks_occ_by_sector_s20.parquet     — rolled up to 5 dashboard sectors

Schema (cleaned):
  state_fips, areatype, areaname (region name when areatype=15),
  soc_code (matoccode), occ_title (codeTitle), codelevel (1=group, 7=detail),
  base_year, proj_year, base_emp (estoccprj), proj_emp (projoccprj),
  pct_change (pchg), annual_openings, annual_exits, annual_transfers,
  annual_change, in_demand (0/1), demand_rank (Int), green_job (0/1),
  sector (mapped from SOC major group), projection_source
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# SOC major group → dashboard sector (mirrors fetch_bls_proj.SOC2_TO_SECTOR)
SOC2_TO_SECTOR: dict[str, str] = {
    "15": "IT/Computer Services",
    "11": "IT/Computer Services",   # management — loosely aligned
    "29": "Healthcare",
    "31": "Healthcare",
    "35": "Hospitality & Entertainment",
    "39": "Hospitality & Entertainment",
    "47": "Skilled Trades",
    "49": "Skilled Trades",
    "51": "Manufacturing",
    "17": "Manufacturing",          # engineering/architecture
}

_NUMERIC = ["estoccprj", "projoccprj", "grrate", "nchg", "pchg",
            "aopeng", "aopenr", "aopent", "exits", "annualexits",
            "transfers", "annualtransfers", "change", "annualchange",
            "openings", "annualopenings", "codelevel"]


def parse(xls_path: Path, state_fips: str = "20") -> pd.DataFrame:
    raw = pd.read_html(xls_path)[0]
    raw.columns = [str(c).strip() for c in raw.iloc[0]]
    raw = raw.iloc[1:].reset_index(drop=True)
    raw = raw[raw["stfips"].astype(str).str.zfill(2) == state_fips.zfill(2)].copy()

    for c in _NUMERIC:
        if c in raw.columns:
            raw[c] = pd.to_numeric(
                raw[c].astype(str).str.replace(",", "").str.replace("%", "").str.strip(),
                errors="coerce",
            )

    raw["matoccode"] = raw["matoccode"].astype(str).str.strip()
    # Format SOC code as DD-DDDD when possible
    raw["soc_code"]  = raw["matoccode"].apply(
        lambda x: f"{x[:2]}-{x[2:]}" if x.isdigit() and len(x) == 6 else x
    )
    raw["sector"]    = raw["matoccode"].str[:2].map(SOC2_TO_SECTOR)

    # in_demand / green: KDOL flags are "0"/"1" strings or NaN
    for flag in ("inDemand", "regionalInDemand", "green", "regionalGreen"):
        if flag in raw.columns:
            raw[flag] = pd.to_numeric(raw[flag], errors="coerce").fillna(0).astype("Int64")

    return raw.rename(columns={
        "stfips": "state_fips",
        "codeTitle": "occ_title",
        "estyear": "base_year",
        "projyear": "proj_year",
        "estoccprj": "base_emp",
        "projoccprj": "proj_emp",
        "pchg": "pct_change",
        "annualopenings": "annual_openings",
        "annualexits": "annual_exits",
        "annualtransfers": "annual_transfers",
        "annualchange": "annual_change",
        "inDemand": "in_demand",
        "regionalInDemand": "regional_in_demand",
        "green": "green_job",
        "regionalGreen": "regional_green",
        "DemandRank": "demand_rank",
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="20")
    ap.add_argument("--input", default="data/occproj__202201002032.xls")
    ap.add_argument("--output-dir", default="data/outputs")
    args = ap.parse_args()

    xls = Path(args.input)
    if not xls.exists():
        print(f"Input not found: {xls}", file=sys.stderr)
        sys.exit(1)
    sf = args.state.zfill(2)
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)

    df = parse(xls, args.state)
    df["projection_source"] = "KDOL_State_Occupational"
    df["state_fips"] = sf

    # State-level vs sub-state region
    keep_cols = [c for c in [
        "state_fips", "areatype", "areaname", "soc_code", "occ_title",
        "codelevel", "base_year", "proj_year", "base_emp", "proj_emp",
        "pct_change", "annual_openings", "annual_exits", "annual_transfers",
        "annual_change", "in_demand", "demand_rank", "green_job",
        "regional_in_demand", "regional_green", "sector", "projection_source",
    ] if c in df.columns]
    df = df[keep_cols]

    state  = df[df["areatype"] == "01"].copy()
    region = df[df["areatype"] == "15"].copy()

    state_out  = out / f"ks_occ_proj_state_s{sf}.parquet"
    region_out = out / f"ks_occ_proj_region_s{sf}.parquet"
    state.to_parquet(state_out, index=False)
    region.to_parquet(region_out, index=False)
    print(f"Saved: {state_out.name}  ({len(state)} state-SOC rows, "
          f"{state['soc_code'].nunique()} unique SOC)")
    print(f"Saved: {region_out.name} ({len(region)} region-SOC rows, "
          f"{region['areaname'].nunique()} regions)")

    # In-demand list (KDOL Workforce Innovation Board flag = 1)
    # demand_rank is empty in current KDOL exports; sort by annual_openings instead
    if "in_demand" in state.columns:
        in_demand = state[(state["in_demand"] == 1)
                          & (state["annual_openings"].notna())
                          & (state["codelevel"] == 6)] \
            .sort_values("annual_openings", ascending=False)
        if not in_demand.empty:
            top_out = out / f"ks_occ_in_demand_top_s{sf}.parquet"
            in_demand.to_parquet(top_out, index=False)
            print(f"Saved: {top_out.name} ({len(in_demand)} in-demand detail occupations)")
            print("\nTop 15 in-demand KS occupations (by annual openings):")
            print(in_demand.head(15)[
                ["occ_title", "sector", "annual_openings", "pct_change", "base_emp"]
            ].to_string(index=False))

    # Sector rollup — only sum 6-digit detail rows to avoid double-counting
    detail = state[state["codelevel"] == 6].copy() if "codelevel" in state.columns else state.copy()
    sector_roll = (
        detail[detail["sector"].notna()]
        .groupby(["sector", "base_year", "proj_year", "projection_source"],
                  as_index=False)
        .agg(base_emp_total=("base_emp", "sum"),
             proj_emp_total=("proj_emp", "sum"),
             annual_openings=("annual_openings", "sum"),
             n_occupations=("soc_code", "nunique"))
    )
    sector_roll["emp_change_pct"] = (
        (sector_roll["proj_emp_total"] - sector_roll["base_emp_total"])
        / sector_roll["base_emp_total"] * 100
    ).round(1)
    sector_out = out / f"ks_occ_by_sector_s{sf}.parquet"
    sector_roll.to_parquet(sector_out, index=False)
    print(f"\nSaved: {sector_out.name}")
    print(sector_roll.to_string(index=False))


if __name__ == "__main__":
    main()
