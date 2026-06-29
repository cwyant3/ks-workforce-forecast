"""
fetch_projections_central.py
Fetches state long-term occupational employment projections from Projections
Central (the DOL/ETA-funded Projections Managing Partnership clearinghouse).

Why this exists
---------------
The cohort-component model computes labor *supply* (people available to work)
identically for every state. It does NOT compute labor *demand* — occupation
projections are published forecasts we ingest, not numbers we calculate. For
Kansas those come from the KDOL occproj file; for other states the equivalent
state-LMI projections are standardized and served by Projections Central. This
module is therefore the multi-state generalization of the KS occupational
projection layer (see parse_manual_ks_occproj.py / fetch_bls_proj.py).

Data source: Projections Central REST API (read-only HTTPS GET, no auth).
  Host:  https://public.projectionscentral.org
  Path:  /Projections/LongTermRestJson/{FIPS}[+{FIPS}...]
  (The www. apex domain is an Angular SPA; the DATA host is public.*)

Response: {"rows": [...], "pager": {current_page, total_pages, items_per_page}}.
Paginated at 100 rows/page; current_page is 0-indexed.

Three live-confirmed gotchas this module handles:
  1. VINTAGE/COVERAGE VARIES BY STATE. The API serves only each state's latest
     PUBLISHED cycle and exposes no historical access. As of 2026-06 only ~20
     states had filed the 2024–2034 cycle (among the KS bloc: Colorado yes;
     KS/NE/MO/OK not yet). A state with no published cycle returns 0 rows — we
     treat that as a gap (warn, return empty), NOT an error, so the dataset
     auto-fills as states publish without code changes.
  2. STFIPS HAS NO LEADING ZERO. Colorado is "8", not "08" — same class of bug
     as the SSA county-ANSI fix. We zero-pad on the way out.
  3. ALL VALUES ARE STRINGS ("37960", "17.6"); blanks/footnotes coerce to None.

Output DataFrame columns (aligned with fetch_bls_proj.fetch_national_projections):
  state_fips, area, soc_code, occ_title, sector,
  base_year, proj_year, base_emp, proj_emp, emp_change, pct_change,
  annual_openings, projection_source ("projections_central_lt"), vintage
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

import pandas as pd
import requests

# Reuse the canonical SOC-major-group → dashboard-sector map and the sector
# rollup so the PC layer aggregates identically to the BLS/KS projection layers.
from fetch_bls_proj import SOC2_TO_SECTOR, sector_demand_outlook

_API_HOST = "https://public.projectionscentral.org"
_LT_PATH  = "/Projections/LongTermRestJson"
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)",
    "Accept": "application/json",
}
_PAGE_SIZE_HINT = 100   # API default; we honor pager.total_pages regardless
_MAX_PAGES = 500        # backstop against a runaway pager

_OUT_COLS = [
    "state_fips", "area", "soc_code", "occ_title", "sector",
    "base_year", "proj_year", "base_emp", "proj_emp", "emp_change",
    "pct_change", "annual_openings", "projection_source", "vintage",
]


def _fips_param(state_fips: str) -> str:
    """API expects the integer form with no leading zero (Colorado 08 -> '8')."""
    return str(int(str(state_fips).strip()))


def _num(val) -> float | None:
    """Coerce an SSA/PC string cell ('37,960', '17.6', '', 'N/A') to float|None."""
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if not s or s.upper() in ("N/A", "NA", "*", "**", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _get_page(session: requests.Session, fips_param: str, page: int) -> dict:
    url = f"{_API_HOST}{_LT_PATH}/{fips_param}"
    for attempt in range(3):
        try:
            resp = session.get(url, params={"page": page}, timeout=90)
            if resp.status_code == 200 and "json" in resp.headers.get("content-type", "").lower():
                return resp.json()
            # Non-JSON (SPA shell) or transient status — brief backoff and retry.
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return {"rows": [], "pager": {}}


def _normalise_row(row: dict) -> dict:
    soc = str(row.get("OccCode", "")).strip()
    soc2 = soc[:2]
    by = _num(row.get("BaseYear"))
    py = _num(row.get("ProjYear"))
    return {
        "state_fips":        str(int(row.get("STFIPS", "0"))).zfill(2),
        "area":              str(row.get("Area", "")).strip() or None,
        "soc_code":          soc or None,
        "occ_title":         str(row.get("Title", "")).strip() or None,
        "sector":            SOC2_TO_SECTOR.get(soc2),
        "base_year":         int(by) if by is not None else None,
        "proj_year":         int(py) if py is not None else None,
        "base_emp":          _num(row.get("Base")),
        "proj_emp":          _num(row.get("Projected")),
        "emp_change":        _num(row.get("Change")),
        "pct_change":        _num(row.get("PercentChange")),
        "annual_openings":   _num(row.get("AvgAnnualOpenings")),
        "projection_source": "projections_central_lt",
        "vintage":           (f"{int(by)}-{int(py)}" if by is not None and py is not None else None),
    }


def fetch_pc_longterm(
    state_fips: str = "20",
    cache_dir: Path | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """
    Fetch Projections Central long-term occupational projections for one state.

    Returns a DataFrame with _OUT_COLS. A state with no published cycle returns
    an EMPTY DataFrame (with the right columns) and a warning — this is a normal
    coverage gap, not an error, and is deliberately NOT cached so the state
    auto-fills on a later run once it publishes.
    """
    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    sf = str(state_fips).zfill(2)
    cache_file = cache_dir / f"pc_longterm_s{sf}.parquet"
    if cache_file.exists() and not force:
        print(f"  [cache] PC long-term projections {sf}")
        return pd.read_parquet(cache_file)

    fips_param = _fips_param(sf)
    session = requests.Session()
    session.headers.update(_HTTP_HEADERS)

    rows: list[dict] = []
    first = _get_page(session, fips_param, 0)
    pager = first.get("pager", {}) or {}
    total_pages = int(pager.get("total_pages", 0) or 0)
    rows.extend(first.get("rows", []) or [])

    for page in range(1, min(total_pages, _MAX_PAGES)):
        rows.extend(_get_page(session, fips_param, page).get("rows", []) or [])

    if not rows:
        warnings.warn(
            f"\nProjections Central returned 0 rows for state {sf}. This state "
            f"has not published a long-term cycle to PC yet (coverage gap, not an "
            f"error). The layer will populate automatically on a later run.\n",
            UserWarning,
            stacklevel=2,
        )
        return pd.DataFrame(columns=_OUT_COLS)

    df = pd.DataFrame([_normalise_row(r) for r in rows])
    # Keep only detailed SOC codes (DD-DDDD); drop any summary/total artifacts.
    df = df[df["soc_code"].fillna("").str.match(r"^\d{2}-\d{4}$")].reset_index(drop=True)
    df = df.reindex(columns=_OUT_COLS)

    df.to_parquet(cache_file, index=False)
    vint = ", ".join(sorted(v for v in df["vintage"].dropna().unique()))
    print(f"  [saved] pc_longterm_s{sf}.parquet  ({len(df)} occupations, vintage {vint})")
    return df


def pc_sector_outlook(pc_df: pd.DataFrame) -> pd.DataFrame:
    """Roll PC occupation rows up to the 5 dashboard sectors (reuses the shared
    sector_demand_outlook so PC aggregates identically to BLS/KS layers)."""
    if pc_df.empty:
        return pd.DataFrame()
    return sector_demand_outlook(pc_df)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch Projections Central long-term projections")
    ap.add_argument("--state", default="20", help="State FIPS (e.g. 20=KS, 08=CO)")
    ap.add_argument("--cache-dir", default="data/pc_cache")
    ap.add_argument("--output-dir", default="data/outputs")
    ap.add_argument("--force", action="store_true", help="Ignore cache, re-fetch live")
    args = ap.parse_args()

    sf = str(args.state).zfill(2)
    df = fetch_pc_longterm(sf, cache_dir=Path(args.cache_dir), force=args.force)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    occ_out = out_dir / f"pc_occ_proj_s{sf}.parquet"
    df.to_parquet(occ_out, index=False)
    print(f"  Saved: {occ_out.name}  ({len(df)} rows)")

    outlook = pc_sector_outlook(df)
    if not outlook.empty:
        sec_out = out_dir / f"pc_occ_by_sector_s{sf}.parquet"
        outlook.to_parquet(sec_out, index=False)
        print(f"  Saved: {sec_out.name}  ({len(outlook)} sector rows)")
    else:
        print(f"  No sector rollup written (no projection data for state {sf}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
