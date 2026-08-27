"""
parse_manual_kdol_labforce.py
Parse the KDOL LMIS labor force file. KLIC exports this two ways and BOTH are
accepted (detected by content, not extension): the Telerik report builder's
HTML-as-.xls, and the newer real .xlsx. In KLIC this report lives under LAUS —
KDOL is Kansas's LAUS partner agency, so these ARE the state's LAUS estimates,
published monthly rather than on the BLS annual county schedule. This is NOT the
UI claims dataset originally
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


def _read_kdol_export(path: Path) -> pd.DataFrame:
    """Read the KDOL labor force export from either format KLIC emits.

    KLIC's Telerik report builder exports HTML-as-.xls: a bare <table> whose
    header sits in <td> cells, so pandas does not recognize it as a header row
    and it arrives as data row 0. KLIC's newer "export to Excel" path emits a
    real .xlsx with a proper header on a sheet named "Data". Same 23 columns
    either way, so detect by content (a real xlsx is a zip) rather than by
    extension — the file is often saved under whichever suffix the browser
    offered.
    """
    is_xlsx = path.open("rb").read(2) == b"PK"
    if is_xlsx:
        return pd.read_excel(path, sheet_name=0)

    raw = pd.read_html(path)[0]
    raw.columns = [str(c).strip() for c in raw.iloc[0]]
    return raw.iloc[1:].reset_index(drop=True)


def parse_kdol_labforce(xls_path: Path, state_fips: str = "20") -> pd.DataFrame:
    raw = _read_kdol_export(Path(xls_path))

    # Normalize the code columns to zero-padded strings. The HTML export yields
    # strings ("04"); the .xlsx export yields integers (4). Without this the
    # Areatype comparisons below silently match nothing and the county/state
    # outputs come out EMPTY rather than erroring.
    _areatype = pd.to_numeric(raw["Areatype"], errors="coerce")
    raw["Areatype"] = _areatype.map(
        lambda v: f"{int(v):02d}" if pd.notna(v) else ""
    )

    sf = state_fips.zfill(2)
    raw = raw[raw["Stfips"].astype(str).str.zfill(2) == sf].copy()

    for col in _NUMERIC_COLS:
        if col in raw.columns:
            raw[col] = pd.to_numeric(
                raw[col].astype(str).str.replace(",", "").str.replace("%", ""),
                errors="coerce",
            )

    # 5-digit FIPS for counties; Area column is 6-digit "020001" → county_fips=001.
    # Go through to_numeric so the .xlsx export's integer Area (1) and the HTML
    # export's string Area ("000001") both land on "001" — a plain astype(str)
    # on a float column would yield "1.0" and silently produce garbage FIPS.
    raw["county_fips"] = pd.to_numeric(raw["Area"], errors="coerce").map(
        lambda v: f"{int(v):06d}"[-3:] if pd.notna(v) else ""
    )

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
    recent = recent[recent["_period"] >= cutoff]
    # Report the newest period as one value. Taking max(Periodyear) and
    # max(month) independently reads December off an earlier year and claims a
    # month the file does not contain (a 2025-07..2026-06 window printed as
    # "2026/12"), which is exactly the kind of thing a staleness check trusts.
    newest = int(recent["_period"].max())
    recent = recent.drop(columns=["_period"])
    rec_out = out / f"kdol_labforce_county_recent_s{sf}.parquet"
    recent.to_parquet(rec_out, index=False)
    print(f"Saved: {rec_out.name}  ({len(recent)} rows, "
          f"{newest // 100}/{newest % 100:02d} most-recent month)")

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
