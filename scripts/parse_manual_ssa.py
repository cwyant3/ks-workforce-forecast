"""
parse_manual_ssa.py
Parse the SSA "OASDI Beneficiaries by State and County" multi-sheet workbook
(oasdi_sc{YY}.xlsx) into the parquet output the participation model expects.

This handles the file structure used by SSA from publication year 2024 onward,
which differs from the legacy oasdi_county/oc{YY}.xlsx format that
fetch_ssa_disability.py was built for.

Workbook structure:
  Sheet "Table 4 - {State}" — one per state — contains:
    row 0: state name
    row 1: table title
    row 2: column group headers (County / ANSI Code / Total / Retirement / Survivors / Disability / Aged 65+)
    row 3: sub-headers (Retired workers, Spouses, Children, etc.)
    row 4: state total
    row 5+: per-county rows with 5-digit ANSI code = state+county FIPS

Disability columns (offsets within row 3): 9=Disabled workers, 10=Spouses, 11=Children
We treat "Disabled workers" as the working-age SSDI count (proxy for ssdi_18_64).
SSI 18-64 is not in this file — set NA. Data year = publication year - 1.

Usage:
    python scripts/parse_manual_ssa.py --state 20 --pub-year 2024
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# State FIPS → SSA sheet name suffix (the part after "Table 4 - ")
_STATE_FIPS_TO_NAME = {
    "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas",
    "06": "California", "08": "Colorado", "09": "Connecticut", "10": "Delaware",
    "12": "Florida", "13": "Georgia", "15": "Hawaii", "16": "Idaho",
    "17": "Illinois", "18": "Indiana", "19": "Iowa", "20": "Kansas",
    "21": "Kentucky", "22": "Louisiana", "23": "Maine", "24": "Maryland",
    "25": "Massachusetts", "26": "Michigan", "27": "Minnesota", "28": "Mississippi",
    "29": "Missouri", "30": "Montana", "31": "Nebraska", "32": "Nevada",
    "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico", "36": "New York",
    "37": "North Carolina", "38": "North Dakota", "39": "Ohio", "40": "Oklahoma",
    "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island", "45": "South Carolina",
    "46": "South Dakota", "47": "Tennessee", "48": "Texas", "49": "Utah",
    "50": "Vermont", "51": "Virginia", "53": "Washington", "54": "West Virginia",
    "55": "Wisconsin", "56": "Wyoming",
}


def parse_state(xlsx_path: Path, state_fips: str, pub_year: int) -> pd.DataFrame:
    state_name = _STATE_FIPS_TO_NAME[state_fips.zfill(2)]
    sheet = f"Table 4 - {state_name}"
    raw   = pd.read_excel(xlsx_path, sheet_name=sheet, dtype=str, header=None)

    # Find the row where county data starts (county name in col 0, blank state-total row preceded it).
    # Header row 3 has "Disabled workers" at col index 9.
    rows = []
    for i in range(5, len(raw)):
        county   = str(raw.iloc[i, 0]).strip()
        ansi     = str(raw.iloc[i, 2]).strip()
        if not county or county.lower() in ("nan", ""):
            continue
        if not ansi or not ansi.isdigit() or len(ansi) != 5:
            continue
        # Disabled workers (SSDI proxy for working-age 18–64)
        disabled_workers = pd.to_numeric(str(raw.iloc[i, 9]).replace(",", ""),
                                         errors="coerce")
        rows.append({
            "state_fips":     ansi[:2],
            "county_fips":    ansi[2:],
            "year":           pub_year - 1,
            "ssdi_18_64":     int(disabled_workers) if pd.notna(disabled_workers) else None,
            "ssi_18_64":      None,
            "source":         "oasdi_sc_manual",
        })

    df = pd.DataFrame(rows)
    df["total_disabled_18_64"] = df["ssdi_18_64"]
    df["disability_caveat"]    = "ssdi_only_no_ssi"
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="20", help="State FIPS (default 20=KS)")
    ap.add_argument("--pub-year", type=int, default=2024,
                    help="SSA publication year (data year = pub_year - 1)")
    ap.add_argument("--cache-dir", default="data/ssa_cache")
    ap.add_argument("--output-dir", default="data/outputs")
    args = ap.parse_args()

    cache = Path(args.cache_dir)
    out   = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    candidates = list(cache.glob("oasdi_sc*.xlsx")) + list(cache.glob("oasdi_*.xlsx"))
    if not candidates:
        print(f"No oasdi_sc*.xlsx found in {cache}", file=sys.stderr)
        sys.exit(1)
    xlsx = candidates[0]
    print(f"Parsing {xlsx.name}, state={args.state}, pub_year={args.pub_year}")

    df = parse_state(xlsx, args.state, args.pub_year)
    print(f"Parsed {len(df)} counties for state {args.state}")
    print(df.head(5).to_string())

    # Save in the location run_forecast.py expects
    sf = args.state.zfill(2)
    parquet_out = out / f"ssa_disability_s{sf}.parquet"
    df.to_parquet(parquet_out, index=False)
    print(f"Saved: {parquet_out}")


if __name__ == "__main__":
    main()
