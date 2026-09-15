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
SSI 18-64 is not in this file — set NA.

Data year: edition YY reports beneficiaries as of DECEMBER YY. The Table 4 title
row says so in every sheet ("... December 2025" in oasdi_sc25.xlsx), and that is
where the year is read from — the filename only cross-checks it. Until
2026-09-15 this parser stamped pub_year - 1, which labelled December-2025 counts
as year 2024 (see docs/data-refresh-log.md, 2026-09-15 SSA entries).

Usage:
    python scripts/parse_manual_ssa.py --state 20
    python scripts/parse_manual_ssa.py --state 20 --pub-year 2025   # override
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# SSA names the workbook for its edition year: oasdi_sc25.xlsx is the 2025
# edition, reporting data as of December 2025 (the Table 4 title says so).
# Deriving the year from the filename keeps the parser from mislabelling a new
# edition with the previous cycle's year — the failure this replaced, where
# --pub-year defaulted to 2024 and refresh_dashboard.py never passed the flag.
_PUB_YEAR_RE = re.compile(r"oasdi_sc(\d{2})", re.IGNORECASE)

# "December 2025" in the Table 4 title. SSA separates the word and the year
# with a non-breaking space in the workbook, so allow any short non-digit run.
_DEC_YEAR_RE = re.compile(r"December\D{0,3}((?:19|20)\d{2})", re.IGNORECASE)


def data_year_from_title(raw: pd.DataFrame, max_rows: int = 4) -> int | None:
    """Reference year from the 'December YYYY' in a Table 4 sheet's title rows.

    Read from the source, not the filename: this is what makes the label
    self-correcting if SSA ever changes its naming convention.
    """
    for i in range(min(max_rows, len(raw))):
        for cell in raw.iloc[i].tolist():
            if cell is None or (isinstance(cell, float) and pd.isna(cell)):
                continue
            m = _DEC_YEAR_RE.search(str(cell))
            if m:
                return int(m.group(1))
    return None


def pub_year_from_name(path: Path) -> int | None:
    """Publication year encoded in an oasdi_sc{YY} filename, or None."""
    m = _PUB_YEAR_RE.search(path.name)
    return 2000 + int(m.group(1)) if m else None


def newest_workbook(cache: Path) -> Path | None:
    """Newest SSA workbook in `cache` by filename.

    Editions sort chronologically (oasdi_sc24 < oasdi_sc25), so the last match
    is the newest. Prefer the oasdi_sc* naming over the looser oasdi_* fallback,
    because a legacy name like oasdi_2024.xlsx would otherwise sort ahead of
    every sc-named edition ("2" < "s") and win.
    """
    for pattern in ("oasdi_sc*.xlsx", "oasdi_*.xlsx"):
        matches = sorted(cache.glob(pattern))
        if matches:
            return matches[-1]
    return None

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

    # Data year comes from the sheet title. The filename's edition year must
    # agree — a disagreement means either SSA changed conventions or the file
    # was misnamed, and a mislabelled parquet is worse than a failed parse
    # (same guard shape as fetch_bls_proj's cycle-mismatch check).
    data_year = data_year_from_title(raw)
    if data_year is None:
        print(f"  !! no 'December YYYY' found in the title rows of {sheet!r}; "
              f"falling back to the edition year {pub_year} from the filename")
        data_year = pub_year
    elif data_year != pub_year:
        raise ValueError(
            f"{xlsx_path.name}: sheet {sheet!r} reports December {data_year} but "
            f"the filename says edition {pub_year}. Edition YY should hold "
            f"December YY data; refusing to write a mislabelled parquet."
        )

    # Find the row where county data starts (county name in col 0, blank state-total row preceded it).
    # Header row 3 has "Disabled workers" at col index 9.
    sf = state_fips.zfill(2)
    rows = []
    for i in range(5, len(raw)):
        county   = str(raw.iloc[i, 0]).strip()
        ansi     = str(raw.iloc[i, 2]).strip()
        if not county or county.lower() in ("nan", ""):
            continue
        # SSA drops the leading zero on the county ANSI code for states with
        # FIPS < 10 (e.g. Colorado county 08001 is stored as "8001"). Accept a
        # 4- or 5-digit code, zero-pad to 5, then split state(2)+county(3). The
        # state-total row (col 2 = bare FIPS, 1–2 digits) is excluded by length,
        # and the state-prefix match guards against any stray non-county row.
        if not ansi.isdigit() or len(ansi) not in (4, 5):
            continue
        fips5 = ansi.zfill(5)
        if fips5[:2] != sf:
            continue
        # Disabled workers (SSDI proxy for working-age 18–64)
        disabled_workers = pd.to_numeric(str(raw.iloc[i, 9]).replace(",", ""),
                                         errors="coerce")
        rows.append({
            "state_fips":     fips5[:2],
            "county_fips":    fips5[2:],
            "year":           data_year,
            "ssdi_18_64":     int(disabled_workers) if pd.notna(disabled_workers) else None,
            "ssi_18_64":      None,
            "source":         "oasdi_sc_manual",
        })

    if not rows:
        raise ValueError(
            f"parse_state: 0 county rows parsed from sheet {sheet!r} for state "
            f"{sf}. The workbook layout may have changed (expected county name in "
            f"col 0, ANSI code in col 2, disabled-workers count in col 9)."
        )

    df = pd.DataFrame(rows)
    df["total_disabled_18_64"] = df["ssdi_18_64"]
    df["disability_caveat"]    = "ssdi_only_no_ssi"
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="20", help="State FIPS (default 20=KS)")
    ap.add_argument("--pub-year", type=int, default=None,
                    help="SSA edition year (edition YY holds December YY data). "
                         "Default: inferred from the workbook filename; the sheet "
                         "title must agree.")
    ap.add_argument("--cache-dir", default="data/ssa_cache")
    ap.add_argument("--output-dir", default="data/outputs")
    args = ap.parse_args()

    cache = Path(args.cache_dir)
    out   = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    xlsx = newest_workbook(cache)
    if xlsx is None:
        print(f"No oasdi_sc*.xlsx found in {cache}", file=sys.stderr)
        sys.exit(1)

    pub_year = args.pub_year or pub_year_from_name(xlsx)
    if pub_year is None:
        print(f"Could not infer the publication year from {xlsx.name!r}. "
              f"Re-run with an explicit --pub-year.", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {xlsx.name}, state={args.state}, edition {pub_year} "
          f"(data year read from the sheet title; expected {pub_year})")

    df = parse_state(xlsx, args.state, pub_year)
    print(f"Parsed {len(df)} counties for state {args.state}")
    print(df.head(5).to_string())

    # Save the raw counts in BOTH locations:
    #  - outputs/  : the dashboard's display file (run_forecast later overwrites
    #                this with the rate-computed version when it runs).
    #  - ssa_cache/: the path fetch_ssa_disability() reads as its cache, so
    #                run_forecast's Step 15 finds this data (the auto-download is
    #                dead/403) and feeds the participation model's disability
    #                Layer 2. Without this, projections_effective omits the
    #                disability adjustment for every state. The cache copy stays
    #                raw and is never overwritten, so no double rate-compute.
    sf = args.state.zfill(2)
    parquet_out = out / f"ssa_disability_s{sf}.parquet"
    df.to_parquet(parquet_out, index=False)
    print(f"Saved: {parquet_out}")

    cache_out = cache / f"ssa_disability_s{sf}.parquet"
    df.to_parquet(cache_out, index=False)
    print(f"Saved: {cache_out}  (read by run_forecast Step 15)")


if __name__ == "__main__":
    main()
