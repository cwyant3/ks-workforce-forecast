"""
fetch_ksde.py
Fetches Kansas K-12 enrollment data by district and aggregates to county FIPS.
Used to replace ACS-derived youth cohort counts with actual enrollment figures,
improving the near-term (4–14 year) workforce entry projection.

KS-ONLY MODULE.

Primary data source: NCES Common Core of Data (CCD) via Urban Institute
  Education Data Portal API (free, no key required)
  Enrollment: https://educationdata.urban.org/api/v1/school-districts/ccd/enrollment/{year}/grade-{N}/
  Directory:  https://educationdata.urban.org/api/v1/school-districts/ccd/directory/{year}/

  API notes (corrected from original):
    - Enrollment endpoint uses path segment `grade-{N}` (e.g. grade-9), NOT grade-eoy.
    - State filter for enrollment endpoint uses `fips=20`, NOT state_fips_code.
    - Enrollment endpoint returns leaid + fips only; county_code comes from the directory.
    - Race/sex totals: filter race=99&sex=99 for all-race, both-sex aggregate.

Fallback: manually placed file at {cache_dir}/ksde_manual.csv
  Required columns: year, county_fips (3-digit or 5-digit), grade_group
                    (one of: 'k_5', '6_8', '9_12', 'total'), enrollment

Grade → ACS cohort mapping:
  9-12 (ages 14-17) → pop_15_17    — enters workforce in 1–3 years
  6-8  (ages 11-13) → contributes to pop_10_14
  K-5  (ages  5-10) → contributes to pop_5_9 / pop_10_14

Output DataFrame columns:
  state_fips (str), county_fips (3-digit str), year (int),
  grade_group (str), enrollment (int),
  enrollment_trend_slope (float), pct_change_5yr (float | None),
  pipeline_alert (bool)  — True if >10% decline in 5 years
"""

import time
import warnings
import requests
import pandas as pd
import numpy as np
from pathlib import Path

_KS_FIPS   = "20"
_ENROLL_BASE = "https://educationdata.urban.org/api/v1/school-districts/ccd/enrollment"
_DIR_BASE    = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory"
_PAGE_SIZE   = 1000

# CCD grade numbers: 0=Kindergarten, 1-12=grades 1-12
_GRADE_GROUPS: dict[str, list[int]] = {
    "k_5":  [0, 1, 2, 3, 4, 5],
    "6_8":  [6, 7, 8],
    "9_12": [9, 10, 11, 12],
}

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}

KSDE_YEARS = list(range(2010, 2024))

_MAX_RETRIES = 3
_RETRY_DELAY = 2.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_with_retry(url: str, params: dict, timeout: int = 60) -> requests.Response | None:
    """GET with simple retry on timeout or 5xx."""
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=_HTTP_HEADERS, timeout=timeout)
            if resp.status_code in (200, 404):
                return resp
            if resp.status_code >= 500:
                print(f"    {resp.status_code} on attempt {attempt+1}, retrying…")
                time.sleep(_RETRY_DELAY * (attempt + 1))
        except requests.exceptions.Timeout:
            print(f"    Timeout on attempt {attempt+1}, retrying…")
            time.sleep(_RETRY_DELAY * (attempt + 1))
        except Exception as exc:
            print(f"    Error: {exc}")
            return None
    return None


def _paginate(url: str, base_params: dict) -> list[dict]:
    """Fetch all pages from a paginated Urban Institute API endpoint."""
    rows = []
    params = {**base_params, "per_page": _PAGE_SIZE, "page": 1}
    while True:
        resp = _get_with_retry(url, params)
        if resp is None or resp.status_code == 404:
            break
        data = resp.json()
        batch = data.get("results", [])
        rows.extend(batch)
        if not data.get("next"):
            break
        params["page"] += 1
        time.sleep(0.3)
    return rows


# ── Directory: leaid → county_fips lookup ────────────────────────────────────

def _fetch_leaid_county_map(year: int) -> dict[str, str]:
    """
    Fetch district directory for one year; return {leaid: county_fips_3digit}.
    Uses state_fips_code=20 filter (directory uses this param; enrollment uses fips).
    """
    url = f"{_DIR_BASE}/{year}/"
    rows = _paginate(url, {"state_fips_code": int(_KS_FIPS)})
    mapping: dict[str, str] = {}
    for row in rows:
        leaid = str(row.get("leaid", "")).strip()
        county = str(row.get("county_code", "")).strip()
        if leaid and county and len(county) >= 3:
            mapping[leaid] = county[-3:].zfill(3)
    return mapping


# ── Grade-level enrollment fetch ──────────────────────────────────────────────

def _fetch_grade(year: int, grade: int) -> list[dict]:
    """
    Fetch district-level enrollment for one year and grade (all races, both sexes).
    Returns [{leaid, year, grade, enrollment}].
    Enrollment endpoint filter: fips=20 (not state_fips_code).
    """
    url = f"{_ENROLL_BASE}/{year}/grade-{grade}/"
    rows = _paginate(url, {"fips": int(_KS_FIPS), "race": 99, "sex": 99})
    return rows


def _fetch_year_all_groups(
    year: int, leaid_county: dict[str, str]
) -> pd.DataFrame:
    """Fetch all grade groups for one year; aggregate to county level."""
    frames = []

    for group, grades in _GRADE_GROUPS.items():
        group_enrollment: dict[str, int] = {}  # county_fips → total
        for grade in grades:
            rows = _fetch_grade(year, grade)
            for row in rows:
                leaid  = str(row.get("leaid", ""))
                county = leaid_county.get(leaid)
                if county is None:
                    continue
                enroll = row.get("enrollment") or 0
                group_enrollment[county] = group_enrollment.get(county, 0) + int(enroll)
            time.sleep(0.2)

        if group_enrollment:
            df_grp = pd.DataFrame([
                {"county_fips": c, "year": year, "grade_group": group,
                 "enrollment": v, "state_fips": _KS_FIPS}
                for c, v in group_enrollment.items()
            ])
            frames.append(df_grp)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # Append derived "total" row per county
    totals = (
        df.groupby("county_fips", as_index=False)["enrollment"]
        .sum()
        .assign(year=year, grade_group="total", state_fips=_KS_FIPS)
    )
    return pd.concat([df, totals], ignore_index=True)


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

    # Build leaid → county_fips map from the most recent available year
    print(f"  KSDE/CCD: fetching district-to-county map for {max(years)}...")
    leaid_county = _fetch_leaid_county_map(max(years))
    if not leaid_county:
        print("  [KSDE] Directory fetch failed — checking for manual file…")
        manual = _load_manual(cache_dir)
        if manual is None:
            warnings.warn(
                f"\n\nKSDE enrollment data not available.\n"
                f"CCD API unreachable at {_ENROLL_BASE}\n"
                f"Options:\n"
                f"  1. Verify internet access to educationdata.urban.org\n"
                f"  2. Place a manual file at: {cache_dir / 'ksde_manual.csv'}\n"
                f"     Required columns: year, county_fips, grade_group, enrollment\n"
                f"     grade_group values: k_5, 6_8, 9_12, total\n"
                "Then re-run with --ksde flag.\n",
                UserWarning,
                stacklevel=2,
            )
            return pd.DataFrame(columns=[
                "state_fips", "county_fips", "year", "grade_group", "enrollment",
                "enrollment_trend_slope", "pct_change_5yr", "pipeline_alert",
            ])
        frames = [manual]
    else:
        print(f"  KSDE/CCD: {len(leaid_county)} districts mapped to counties")
        frames = []
        api_ok = False
        for year in years:
            print(f"  KSDE/CCD {year}…")
            try:
                yr_df = _fetch_year_all_groups(year, leaid_county)
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
                    f"\n\nKSDE enrollment data not available.\n"
                    f"CCD API returned no data for years {years}.\n"
                    f"Place a manual file at: {cache_dir / 'ksde_manual.csv'}\n",
                    UserWarning,
                    stacklevel=2,
                )
                return pd.DataFrame(columns=[
                    "state_fips", "county_fips", "year", "grade_group", "enrollment",
                    "enrollment_trend_slope", "pct_change_5yr", "pipeline_alert",
                ])
            frames = [manual]

    df = pd.concat(frames, ignore_index=True)
    df["enrollment"]  = pd.to_numeric(df["enrollment"],  errors="coerce").fillna(0).astype(int)
    df["year"]        = pd.to_numeric(df["year"],        errors="coerce").astype(int)
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
    historical ACS rows are left unchanged.

    Mapping:
      KSDE 9-12 enrollment  → pop_15_17
      KSDE 6-8  enrollment  → pop_10_14
      KSDE K-5  enrollment  → pop_5_9

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
    patched_cells = 0

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
                acs.loc[county_mask, col]             = float(enroll_val)
                acs.loc[county_mask, "ksde_override"] = True
                patched_cells += int(county_mask.sum())

    bl_mask = acs["year"] == baseline_year
    youth_cols = ["pop_under_5", "pop_5_9", "pop_10_14", "pop_15_17"]
    if all(c in acs.columns for c in youth_cols):
        acs.loc[bl_mask, "pop_youth"] = acs.loc[bl_mask, youth_cols].sum(axis=1)
    total_parts = ["pop_youth", "pop_working_age", "pop_retirement"]
    if all(c in acs.columns for c in total_parts):
        acs.loc[bl_mask, "pop_total"] = acs.loc[bl_mask, total_parts].sum(axis=1)

    print(f"  KSDE override applied: {patched_cells} county-cohort cells patched "
          f"(ACS year {baseline_year} <- KSDE year {ksde_use_year})")
    return acs
