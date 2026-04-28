"""
fetch_ksde.py
Fetches Kansas K-12 enrollment data by district and aggregates to county FIPS.
Used to replace ACS-derived youth cohort counts with actual enrollment figures,
improving the near-term (4–14 year) workforce entry projection.

KS-ONLY MODULE.

Primary data source: NCES Common Core of Data (CCD) via Urban Institute
  Education Data Portal API (free, no key required)
  https://educationdata.urban.org/api/v1/school-districts/ccd/enrollment/

Fallback: manually placed file at {cache_dir}/ksde_manual.csv
  Required columns: year, county_fips (3-digit or 5-digit), grade_group
                    (one of: 'k_5', '6_8', '9_12', 'total'), enrollment

Why NCES CCD instead of KSDE directly:
  KSDE's web portal does not expose stable download URLs. CCD aggregates
  KSDE-reported data and publishes it through a documented REST API that
  returns the same enrollment figures with reliable schema.

Grade → ACS cohort mapping:
  9-12 (ages 14-17) → pop_15_17    — enters workforce in 1–3 years
  6-8  (ages 11-13) → contributes to pop_10_14
  K-5  (ages  5-10) → contributes to pop_5_9 / pop_10_14

Output DataFrame columns:
  state_fips (str), county_fips (3-digit str), year (int),
  grade_group (str), enrollment (int),
  enrollment_trend_slope (float), pct_change_5yr (float | None),
  pipeline_alert (bool)  — True if >10% decline in 5 years

ACS override function apply_ksde_override():
  Patches ACS DataFrame youth cohort columns (pop_15_17, pop_10_14, pop_5_9)
  with KSDE enrollment values for the baseline year, before passing to
  cohort_model.run_all_counties().
"""

import io
import time
import warnings
import requests
import pandas as pd
import numpy as np
from pathlib import Path

_KS_FIPS   = "20"
_API_BASE   = "https://educationdata.urban.org/api/v1/school-districts/ccd/enrollment"
_PAGE_SIZE  = 1000

# CCD grade codes — 99=total, 0=Pre-K, 1-12=standard grades
_GRADE_GROUPS: dict[str, list[int]] = {
    "k_5":   [0, 1, 2, 3, 4, 5],       # K (0 in CCD) and grades 1–5
    "6_8":   [6, 7, 8],
    "9_12":  [9, 10, 11, 12],
    "total": [99],                       # CCD total-enrollment shortcut
}

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}

# Years available in NCES CCD enrollment API (1987–present; we use 2010+)
KSDE_YEARS = list(range(2010, 2024))


# ── CCD API fetch ─────────────────────────────────────────────────────────────

def _fetch_ccd_year(year: int, grade: int) -> list[dict]:
    """Fetch one year × grade from NCES CCD API; handles pagination."""
    url     = f"{_API_BASE}/{year}/grade-eoy/"
    params  = {
        "state_fips_code": int(_KS_FIPS),
        "grade":           grade,
        "per_page":        _PAGE_SIZE,
        "page":            1,
    }
    rows = []
    while True:
        resp = requests.get(url, params=params, headers=_HTTP_HEADERS, timeout=60)
        if resp.status_code == 404:
            return []    # year/grade combo not available
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("results", [])
        rows.extend(batch)
        if not data.get("next"):
            break
        params["page"] += 1
        time.sleep(0.3)
    return rows


def _ccd_rows_to_df(rows: list[dict], year: int, grade_group: str) -> pd.DataFrame:
    """Parse CCD API rows; aggregate to county_fips level."""
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "county_code" not in df.columns or "enrollment" not in df.columns:
        return pd.DataFrame()

    df["county_code"] = df["county_code"].astype(str).str.strip()
    # county_code is 5-digit FIPS; extract state prefix and 3-digit county
    ks_mask = df["county_code"].str[:2] == _KS_FIPS
    df = df[ks_mask].copy()
    if df.empty:
        return df

    df["county_fips"] = df["county_code"].str[-3:].str.zfill(3)
    df["enrollment"]  = pd.to_numeric(df["enrollment"], errors="coerce").fillna(0)

    agg = (
        df.groupby("county_fips", as_index=False)["enrollment"]
        .sum()
    )
    agg["year"]        = year
    agg["grade_group"] = grade_group
    agg["state_fips"]  = _KS_FIPS
    agg["enrollment"]  = agg["enrollment"].astype(int)
    return agg


def _fetch_year_all_groups(year: int) -> pd.DataFrame:
    """Fetch K-5, 6-8, 9-12, and total enrollment for one year from CCD."""
    frames = []

    # Total enrollment via grade=99 (fastest path)
    rows = _fetch_ccd_year(year, 99)
    df_total = _ccd_rows_to_df(rows, year, "total")
    if not df_total.empty:
        frames.append(df_total)
    time.sleep(0.5)

    # Grade-group sums
    for group, grades in _GRADE_GROUPS.items():
        if group == "total":
            continue
        group_rows = []
        for grade in grades:
            group_rows.extend(_fetch_ccd_year(year, grade))
            time.sleep(0.2)
        df_grp = _ccd_rows_to_df(group_rows, year, group)
        if not df_grp.empty:
            frames.append(df_grp)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── Manual file fallback ──────────────────────────────────────────────────────

def _load_manual(cache_dir: Path) -> pd.DataFrame | None:
    for name in ("ksde_manual.csv", "ksde_manual.xlsx"):
        path = cache_dir / name
        if path.exists():
            print(f"    Loading manual KSDE file: {path.name}")
            df = pd.read_csv(path, dtype=str) if path.suffix == ".csv" \
                 else pd.read_excel(path, dtype=str)
            df.columns = df.columns.str.strip().str.lower()
            df["state_fips"] = _KS_FIPS
            return df
    return None


# ── Trend computation ─────────────────────────────────────────────────────────

def _compute_trends(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each (county_fips, grade_group), compute:
      enrollment_trend_slope — OLS slope (students/year)
      pct_change_5yr         — % change over the last 5 available years
      pipeline_alert         — True if pct_change_5yr < -10%
    """
    results = []
    for (county, group), grp in df.groupby(["county_fips", "grade_group"]):
        grp = grp.sort_values("year")
        x   = grp["year"].values.astype(float)
        y   = grp["enrollment"].values.astype(float)

        slope = float(np.polyfit(x - x.mean(), y, 1)[0]) if len(grp) >= 2 else 0.0

        # 5-year change
        if len(grp) >= 5:
            start_val = float(grp.iloc[-5]["enrollment"])
            end_val   = float(grp.iloc[-1]["enrollment"])
            pct_5yr   = (end_val - start_val) / start_val * 100 if start_val else None
        else:
            pct_5yr = None

        alert = bool(pct_5yr is not None and pct_5yr < -10.0)

        for _, row in grp.iterrows():
            results.append({
                **row.to_dict(),
                "enrollment_trend_slope": round(slope, 2),
                "pct_change_5yr":         round(pct_5yr, 2) if pct_5yr is not None else None,
                "pipeline_alert":         alert,
            })

    return pd.DataFrame(results) if results else df.assign(
        enrollment_trend_slope=0.0, pct_change_5yr=None, pipeline_alert=False
    )


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_ksde(
    state_fips: str = "20",
    years: list[int] | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch Kansas K-12 enrollment by county and grade group.

    Uses NCES CCD API (Urban Institute portal) as primary source;
    falls back to {cache_dir}/ksde_manual.csv if API is unavailable.

    Parameters
    ----------
    state_fips : must be "20" (Kansas); raises for other states
    years      : school years to fetch (default 2010–2023)
    cache_dir  : parquet cache directory

    Returns
    -------
    DataFrame: state_fips, county_fips, year, grade_group, enrollment,
    enrollment_trend_slope, pct_change_5yr, pipeline_alert
    """
    if state_fips.zfill(2) != _KS_FIPS:
        raise ValueError(f"fetch_ksde is Kansas-only (FIPS 20). Got: {state_fips}")

    if years is None:
        years = KSDE_YEARS
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / "ksde.parquet"
    if cache_file.exists():
        print(f"  [cache] KSDE enrollment")
        return pd.read_parquet(cache_file)

    # Try CCD API
    frames = []
    api_ok  = False
    for year in years:
        print(f"  KSDE/CCD {year}…")
        try:
            yr_df = _fetch_year_all_groups(year)
            if not yr_df.empty:
                frames.append(yr_df)
                api_ok = True
            time.sleep(1.0)
        except Exception as exc:
            print(f"    Warning: CCD {year} failed — {exc}")

    if not api_ok:
        print("  [KSDE] CCD API unavailable — checking for manual file…")
        manual = _load_manual(cache_dir)
        if manual is None:
            warnings.warn(
                "\n\nKSDE enrollment data not available.\n"
                "Options:\n"
                f"  1. Verify internet access to {_API_BASE}\n"
                f"  2. Place a manual file at: {cache_dir / 'ksde_manual.csv'}\n"
                "     Required columns: year, county_fips, grade_group, enrollment\n"
                "     grade_group values: k_5, 6_8, 9_12, total\n"
                "Then re-run with --ksde flag.\n",
                UserWarning,
                stacklevel=2,
            )
            return pd.DataFrame(columns=[
                "state_fips", "county_fips", "year", "grade_group", "enrollment",
                "enrollment_trend_slope", "pct_change_5yr", "pipeline_alert",
            ])
        frames = [manual]

    df = pd.concat(frames, ignore_index=True)
    df["enrollment"] = pd.to_numeric(df["enrollment"], errors="coerce").fillna(0).astype(int)
    df["year"]       = pd.to_numeric(df["year"],       errors="coerce").astype(int)
    df["county_fips"] = df["county_fips"].astype(str).str[-3:].str.zfill(3)

    df = _compute_trends(df)
    df = df.sort_values(["county_fips", "grade_group", "year"]).reset_index(drop=True)

    df.to_parquet(cache_file, index=False)
    print(f"  [saved] ksde.parquet  ({len(df)} rows, "
          f"{df['county_fips'].nunique()} counties, "
          f"{df['year'].nunique()} years)")
    return df


# ── ACS youth cohort override ─────────────────────────────────────────────────

def apply_ksde_override(
    acs_df: pd.DataFrame,
    ksde_df: pd.DataFrame,
    baseline_year: int | None = None,
) -> pd.DataFrame:
    """
    Patch ACS youth cohort columns with KSDE enrollment values for the
    baseline (most recent) ACS year. Only affects the baseline row per county;
    historical ACS rows are left unchanged (migration estimation uses them as-is).

    Mapping:
      KSDE 9-12 enrollment  → pop_15_17  (ages 14–17; most proximate to 18-24 entry)
      KSDE 6-8  enrollment  → pop_10_14  (ages 11–13; pipeline horizon 5–8 yrs)
      KSDE K-5  enrollment  → pop_5_9    (ages 5–10;  pipeline horizon 9–13 yrs)

    Note: KSDE counts include ages slightly outside the ACS cohort boundaries
    (e.g. 9th graders may be 14 or 15). The enrollment count is used as a
    direct replacement, not a scaled adjustment. For counties where KSDE data
    is missing, the original ACS value is retained.

    Parameters
    ----------
    acs_df       : full ACS DataFrame from fetch_acs.fetch_all()
    ksde_df      : output of fetch_ksde()
    baseline_year: ACS year to patch (default: max year in acs_df)

    Returns
    -------
    Patched copy of acs_df with an added 'ksde_override' column (True/False).
    """
    if ksde_df.empty:
        acs_df = acs_df.copy()
        acs_df["ksde_override"] = False
        return acs_df

    if baseline_year is None:
        baseline_year = int(acs_df["year"].max())

    # Use the most recent KSDE year ≤ baseline_year
    ksde_years = sorted(ksde_df["year"].unique())
    ksde_use_year = max((y for y in ksde_years if y <= baseline_year),
                        default=max(ksde_years))

    ksde_base = ksde_df[ksde_df["year"] == ksde_use_year]

    group_to_col = {
        "9_12": "pop_15_17",
        "6_8":  "pop_10_14",
        "k_5":  "pop_5_9",
    }

    acs = acs_df.copy()
    acs["ksde_override"] = False

    for group, col in group_to_col.items():
        if col not in acs.columns:
            continue
        grp_df = ksde_base[ksde_base["grade_group"] == group][
            ["county_fips", "enrollment"]
        ].set_index("county_fips")["enrollment"]

        bl_mask = acs["year"] == baseline_year
        for county_fips, enroll_val in grp_df.items():
            county_mask = bl_mask & (acs["county_fips"] == county_fips)
            if county_mask.any():
                acs.loc[county_mask, col]            = float(enroll_val)
                acs.loc[county_mask, "ksde_override"] = True

    n_patched = acs["ksde_override"].sum()
    print(f"  KSDE override applied: {n_patched} county-cohort cells patched "
          f"(ACS year {baseline_year} ← KSDE year {ksde_use_year})")
    return acs
