"""
fetch_ssa_disability.py
Fetches SSA county-level SSDI (Social Security Disability Insurance) and SSI
(Supplemental Security Income) beneficiary counts for working-age adults (18-64).

Used to adjust the raw ACS working-age population headcount downward by the
fraction receiving federal disability benefits — producing the "structurally
available" workforce before applying the LAUS labor force participation rate.

Three-layer workforce estimation (see participation_model.py):
  Layer 1: ACS working-age population (raw count)
  Layer 2: minus SSA disability → structurally available population
  Layer 3: × LAUS LFPR → effective labor force

Data source: SSA Policy Statistics — OASDI County-Level Data
  https://www.ssa.gov/policy/docs/statcomps/oasdi_county/

File URL pattern (varies slightly by year):
  https://www.ssa.gov/policy/docs/statcomps/oasdi_county/{year}/oc{YY}.xlsx

Note: Some individuals receive both SSDI and SSI; the counts may slightly
double-count. This is documented in output with a caveat flag.

Output DataFrame columns:
  state_fips (str), county_fips (3-digit str), year (int),
  ssdi_18_64 (int | None), ssi_18_64 (int | None),
  total_disabled_18_64 (int),   — SSDI + SSI (note: may include dual recipients)
  disability_rate_pct (float),  — total_disabled_18_64 / working_age_pop * 100
  disability_adjusted_pop (int) — working_age_pop - total_disabled_18_64
"""

import io
import time
import warnings
import requests
import pandas as pd
from pathlib import Path

SSA_YEARS     = list(range(2015, 2023))   # 2015–2022 (lag ~18 months)
_SSA_BASE     = "https://www.ssa.gov/policy/docs/statcomps/oasdi_county"
_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}


def _ssa_url(year: int) -> list[str]:
    """Return candidate SSA Excel URLs for a given year (format varies by year)."""
    yy = str(year)[-2:]
    return [
        f"{_SSA_BASE}/{year}/oc{yy}.xlsx",
        f"{_SSA_BASE}/{year}/oc{yy}.xls",
        f"{_SSA_BASE}/{year}/oasdi_county_{year}.xlsx",
    ]


def _download_ssa(year: int) -> bytes | None:
    for url in _ssa_url(year):
        try:
            resp = requests.get(url, headers=_HTTP_HEADERS, timeout=120)
            if resp.status_code == 200 and len(resp.content) > 1000:
                print(f"    Downloaded SSA {year} from {url.rsplit('/', 1)[-1]}")
                return resp.content
        except Exception:
            pass
    return None


# ── Excel parsing ─────────────────────────────────────────────────────────────

# SSA column patterns to detect FIPS and beneficiary counts
# The SSA files have changed format across years; these are heuristic matches.
_FIPS_HINTS    = ["fips", "county_fips", "cnty", "area"]
_SSDI_18_HINTS = ["disabled_workers_18_64", "di_18_64", "dis_18_64",
                   "disabled workers 18", "18-64", "18 to 64"]
_SSI_18_HINTS  = ["ssi_18_64", "ssi 18", "recipients 18", "aged_18_64",
                   "ssi aged and disabled 18"]


def _find_col(cols: list[str], hints: list[str]) -> str | None:
    cols_lower = [c.lower().replace(" ", "_") for c in cols]
    for hint in hints:
        hint_l = hint.lower().replace(" ", "_")
        for i, col_l in enumerate(cols_lower):
            if hint_l in col_l:
                return cols[i]
    return None


def _parse_ssa_excel(content: bytes, state_fips: str, year: int) -> pd.DataFrame:
    """
    Parse SSA county Excel file. The file structure varies significantly
    across years; this function tries multiple sheet names and column heuristics.
    """
    sf = state_fips.zfill(2)
    xls = pd.ExcelFile(io.BytesIO(content))

    # Try sheets with "county" or data-like names
    sheet_order = []
    for s in xls.sheet_names:
        s_lower = s.lower()
        if any(kw in s_lower for kw in ["county", "data", "all", "table"]):
            sheet_order.insert(0, s)
        else:
            sheet_order.append(s)
    sheet_order = sheet_order or xls.sheet_names

    for sheet in sheet_order:
        try:
            raw = xls.parse(sheet, dtype=str, header=None)
        except Exception:
            continue

        # Scan rows to find the header (first row where FIPS-like content appears)
        header_row = None
        for idx in range(min(20, len(raw))):
            row_vals = [str(v).lower() for v in raw.iloc[idx] if pd.notna(v)]
            if any("fips" in v or "county" in v or "state" in v for v in row_vals):
                header_row = idx
                break

        if header_row is None:
            continue

        df = xls.parse(sheet, dtype=str, header=header_row)
        df.columns = [str(c).strip() for c in df.columns]

        fips_col = _find_col(df.columns, _FIPS_HINTS + ["state", "county"])
        if fips_col is None:
            continue

        # Find state+county FIPS — SSA files sometimes have 5-digit combined FIPS
        # or separate state/county columns
        state_col  = _find_col(df.columns, ["state_fips", "state code", "state"])
        county_col = _find_col(df.columns, ["county_fips", "county code", "county"])

        # Build 5-digit FIPS
        if fips_col and df[fips_col].astype(str).str.len().max() >= 5:
            df["_fips5"] = df[fips_col].astype(str).str.strip().str.zfill(5)
        elif state_col and county_col:
            df["_fips5"] = (df[state_col].astype(str).str.zfill(2) +
                            df[county_col].astype(str).str.zfill(3))
        else:
            continue

        # Filter to target state
        df = df[df["_fips5"].str[:2] == sf].copy()
        if df.empty:
            continue

        df["county_fips"] = df["_fips5"].str[-3:]
        df["state_fips"]  = sf
        df["year"]        = year

        # Find disability counts
        ssdi_col = _find_col(df.columns, _SSDI_18_HINTS)
        ssi_col  = _find_col(df.columns, _SSI_18_HINTS)

        def _safe_int(series: pd.Series) -> pd.Series:
            return pd.to_numeric(
                series.astype(str).str.replace(",", "").str.strip(),
                errors="coerce"
            ).astype("Int64")

        df["ssdi_18_64"] = _safe_int(df[ssdi_col]) if ssdi_col else pd.NA
        df["ssi_18_64"]  = _safe_int(df[ssi_col])  if ssi_col  else pd.NA

        result = df[["state_fips", "county_fips", "year",
                      "ssdi_18_64", "ssi_18_64"]].dropna(
            subset=["county_fips"]
        ).reset_index(drop=True)

        if len(result) >= 10:   # sanity: KS has 105 counties
            return result

    return pd.DataFrame()


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_ssa_disability(
    state_fips: str = "20",
    years: list[int] | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch SSA working-age disability beneficiary counts by county.

    Parameters
    ----------
    state_fips : 2-digit state FIPS (default "20" = Kansas)
    years      : years to fetch (default 2015–2022)
    cache_dir  : parquet cache directory

    Returns
    -------
    DataFrame: state_fips, county_fips, year,
    ssdi_18_64, ssi_18_64, total_disabled_18_64

    Note: total_disabled_18_64 = ssdi_18_64 + ssi_18_64 (dual recipients counted once
    in SSA admin data, but separately in these two series — slight overcount is flagged
    in the disability_caveat column).
    """
    if years is None:
        years = SSA_YEARS
    sf = state_fips.zfill(2)

    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / f"ssa_disability_s{sf}.parquet"
    if cache_file.exists():
        print(f"  [cache] SSA disability {sf}")
        return pd.read_parquet(cache_file)

    frames = []
    for year in years:
        year_cache = cache_dir / f"ssa_disability_s{sf}_{year}.parquet"
        if year_cache.exists():
            print(f"  [cache] SSA {sf} {year}")
            frames.append(pd.read_parquet(year_cache))
            continue

        content = _download_ssa(year)
        if content is None:
            print(f"    Warning: SSA {year} not available — skipping")
            continue

        year_df = _parse_ssa_excel(content, sf, year)
        if year_df.empty:
            print(f"    Warning: SSA {year} parsed 0 rows — skipping")
            continue

        year_df.to_parquet(year_cache, index=False)
        print(f"    [saved] ssa_{sf}_{year}.parquet  ({len(year_df)} counties)")
        frames.append(year_df)
        time.sleep(1.5)

    if not frames:
        warnings.warn(
            "\n\nSSA disability data could not be loaded.\n"
            "If download fails, place file manually at:\n"
            f"  {cache_dir / 'ssa_manual.csv'}\n"
            "Required columns: state_fips, county_fips (3-digit), year,\n"
            "                  ssdi_18_64, ssi_18_64\n"
            "Source: https://www.ssa.gov/policy/docs/statcomps/oasdi_county/\n",
            UserWarning,
            stacklevel=2,
        )
        # Check for manual file
        manual_path = cache_dir / "ssa_manual.csv"
        if manual_path.exists():
            df = pd.read_csv(manual_path, dtype=str)
        else:
            return pd.DataFrame(columns=[
                "state_fips", "county_fips", "year",
                "ssdi_18_64", "ssi_18_64", "total_disabled_18_64",
                "disability_caveat",
            ])
    else:
        df = pd.concat(frames, ignore_index=True)

    # Compute totals
    df["ssdi_18_64"] = pd.to_numeric(df["ssdi_18_64"], errors="coerce").astype("Int64")
    df["ssi_18_64"]  = pd.to_numeric(df["ssi_18_64"],  errors="coerce").astype("Int64")

    df["total_disabled_18_64"] = (
        df["ssdi_18_64"].fillna(0) + df["ssi_18_64"].fillna(0)
    ).astype("Int64")

    # Caveat: if both series present, count may include dual recipients
    df["disability_caveat"] = (
        df["ssdi_18_64"].notna() & df["ssi_18_64"].notna()
    ).map({True: "dual_recipients_possible", False: "single_series_only"})

    df = df.sort_values(["county_fips", "year"]).reset_index(drop=True)
    df.to_parquet(cache_file, index=False)
    print(f"  [saved] ssa_disability_s{sf}.parquet  ({len(df)} rows)")
    return df


def compute_disability_rate(
    ssa_df: pd.DataFrame,
    acs_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join SSA disability counts with ACS working-age population to compute
    county-level disability rates and disability-adjusted population.

    Returns ssa_df with added columns:
      pop_working_age        — from ACS (nearest vintage)
      disability_rate_pct    — total_disabled_18_64 / pop_working_age × 100
      disability_adjusted_pop — pop_working_age - total_disabled_18_64
    """
    acs_pop = acs_df[["county_fips", "acs_period_midpoint_year", "pop_working_age"]].copy()
    acs_pop = acs_pop.rename(columns={"acs_period_midpoint_year": "year"})
    acs_years = sorted(acs_pop["year"].unique())

    def _nearest(y: int) -> int:
        return min(acs_years, key=lambda a: abs(a - y))

    df = ssa_df.copy()
    df["_acs_year"] = df["year"].apply(_nearest)

    merged = df.merge(
        acs_pop.rename(columns={"year": "_acs_year"}),
        on=["county_fips", "_acs_year"],
        how="left",
    ).drop(columns=["_acs_year"])

    wap = merged["pop_working_age"]
    dis = pd.to_numeric(merged["total_disabled_18_64"], errors="coerce").fillna(0)

    merged["disability_rate_pct"] = (dis / wap * 100).round(2).clip(0, 60)
    merged["disability_adjusted_pop"] = (wap - dis).clip(lower=0).round(0).astype("Int64")

    return merged
