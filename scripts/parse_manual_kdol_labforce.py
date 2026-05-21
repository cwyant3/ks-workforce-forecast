"""
parse_manual_kdol_labforce.py
Parse the KDOL LMIS labor force file (HTML-as-.xls export from the KDOL
Telerik report builder). This is NOT the UI claims dataset originally
scoped for fetch_kdol_ui.py — KDOL does not expose UI claims by NAICS
publicly. Instead this is monthly labor force statistics (LF, employed,
unemployed, unemployment rate, LFPR, emp/pop ratio) at county and state
level, going back to 1976 and forward to the most recent month.

Why we use it: KDOL labor force data is materially more recent than BLS
LAUS (KDOL: monthly through ~last month; BLS LAUS: annual through 2023).
This file thus serves as the dashboard's "current labor market pulse"
layer — what the KDOL UI claims layer was originally meant to be.

Schema columns (verbatim from KDOL):
  Areaname, Areatype, Stfips, Periodyear, Periodtype, Period, Timeperiod,
  Adjusted, Laborforce, Emplab, Unemp, Unemprate, Clfprate, Emppopratio

Areatype values observed: 01=state, 04=county, 11=MSA, 15=WIB, 17=micro,
                          24=county subset, 81=BOS, 82=region

Output files (under data/outputs/):
  kdol_labforce_county_s20.parquet  — county × month, Areatype=04
  kdol_labforce_state_s20.parquet   — state × month, Areatype=01
  kdol_labforce_county_recent_s20.parquet — most recent 24 months × county
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

_AREATYPE_STATE  = "01"
_AREATYPE_COUNTY = "04"
_NUMERIC_COLS    = ["Laborforce", "Emplab", "Unemp", "Unemprate",
                    "Clfprate", "Emppopratio"]


def parse_kdol_labforce(xls_path: Path, state_fips: str = "20") -> pd.DataFrame:
    tables = pd.read_html(xls_path)
    raw    = tables[0]
    raw.columns = [str(c).strip() for c in raw.iloc[0]]
    raw    = raw.iloc[1:].reset_index(drop=True)

    sf = state_fips.zfill(2)
    raw = raw[raw["Stfips"].astype(str).str.zfill(2) == sf].copy()

    for col in _NUMERIC_COLS:
        if col in raw.columns:
            raw[col] = pd.to_numeric(
                raw[col].astype(str).str.replace(",", "").str.replace("%", ""),
                errors="coerce",
            )

    # 5-digit FIPS for counties; Area column is 6-digit "020001" → county_fips=001
    raw["county_fips"] = raw["Area"].astype(str).str.zfill(6).str[-3:]

    # Construct sortable period_date (Period 13 = annual average; otherwise month)
    raw["Periodyear"] = pd.to_numeric(raw["Periodyear"], errors="coerce").astype("Int64")
    raw["Period"]     = pd.to_numeric(raw["Period"],     errors="coerce").astype("Int64")
    raw["month"]      = raw["Period"].where(raw["Period"].between(1, 12))
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="20")
    ap.add_argument("--input", default="data/kdol_cache/labforce__99999999.xls")
    ap.add_argument("--output-dir", default="data/outputs")
    ap.add_argument("--recent-months", type=int, default=24)
    args = ap.parse_args()

    xls = Path(args.input)
    if not xls.exists():
        print(f"Input not found: {xls}", file=sys.stderr)
        sys.exit(1)

    sf = args.state.zfill(2)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Parsing {xls.name} ...")
    df = parse_kdol_labforce(xls, args.state)
    print(f"Total rows for state {sf}: {len(df)}")

    counties = df[df["Areatype"] == _AREATYPE_COUNTY].copy()
    state    = df[df["Areatype"] == _AREATYPE_STATE].copy()

    cty_out = out / f"kdol_labforce_county_s{sf}.parquet"
    st_out  = out / f"kdol_labforce_state_s{sf}.parquet"
    counties.to_parquet(cty_out, index=False)
    state.to_parquet(st_out, index=False)
    print(f"Saved: {cty_out.name}  ({len(counties)} rows, "
          f"{counties['county_fips'].nunique()} counties)")
    print(f"Saved: {st_out.name}   ({len(state)} rows)")

    # Recent window: monthly data only (drop annual averages), most recent N months
    recent = counties[counties["month"].notna()].copy()
    recent["_period"] = recent["Periodyear"].astype("Int64") * 100 + recent["month"].astype("Int64")
    cutoff = recent["_period"].nlargest(
        args.recent_months * recent["county_fips"].nunique()
    ).min()
    recent = recent[recent["_period"] >= cutoff].drop(columns=["_period"])
    rec_out = out / f"kdol_labforce_county_recent_s{sf}.parquet"
    recent.to_parquet(rec_out, index=False)
    print(f"Saved: {rec_out.name}  ({len(recent)} rows, "
          f"{recent['Periodyear'].astype('Int64').max()}/"
          f"{int(recent['month'].max())} most-recent month)")

    # Print latest statewide snapshot
    latest_state = state[state["month"].notna()].sort_values(
        ["Periodyear", "month"]
    ).tail(1)
    if not latest_state.empty:
        r = latest_state.iloc[0]
        print()
        print(f"Latest KDOL statewide snapshot ({int(r['Periodyear'])}-{int(r['month']):02d}):")
        print(f"  Labor force: {int(r['Laborforce']):,}")
        print(f"  Employed:    {int(r['Emplab']):,}")
        print(f"  Unemployed:  {int(r['Unemp']):,}")
        print(f"  Unemp rate:  {r['Unemprate']:.2f}%")
        print(f"  LFPR:        {r['Clfprate']:.1f}%")
        print(f"  E/P ratio:   {r['Emppopratio']:.1f}%")


if __name__ == "__main__":
    main()
