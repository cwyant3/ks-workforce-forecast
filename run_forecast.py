"""
run_forecast.py
Orchestrates the full pipeline:
  1. Fetch ACS data (or load from cache)
  2. Run cohort-component model for every county
  3. Save outputs for the dashboard

Usage:
    python run_forecast.py [--state 20] [--key YOUR_CENSUS_KEY] [--sims 2000]

To get a free Census API key (strongly recommended for repeated runs):
    https://api.census.gov/data/key_signup.html
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

# Load .env if present (never committed — see .gitignore)
def _load_env():
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

BASE_DIR    = Path(__file__).parent
CACHE_DIR   = BASE_DIR / "data" / "acs_cache"
QCEW_CACHE  = BASE_DIR / "data" / "qcew_cache"
OUTPUT_DIR  = BASE_DIR / "data" / "outputs"

# Ensure the project root is importable
sys.path.insert(0, str(BASE_DIR))
from fetch_acs    import fetch_all
from cohort_model import run_all_counties
from fetch_qcew   import fetch_state_qcew
from sector_model import run_all_sectors


def main(state_fips: str = "20", api_key: str | None = None,
         n_sim: int = 2000, start_year: int = 2026, end_year: int = 2035,
         run_sectors: bool = True):

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True,  exist_ok=True)

    # ── 1. Fetch ACS data ──────────────────────────────────────────────────
    print("\n=== STEP 1: Fetching ACS data ===")
    acs_df = fetch_all(state_fips=state_fips, api_key=api_key, cache_dir=CACHE_DIR)
    print(f"  Loaded {len(acs_df)} county-year rows "
          f"({acs_df['county_fips'].nunique()} counties, "
          f"years: {sorted(acs_df['year'].unique())})")

    acs_out = OUTPUT_DIR / f"acs_combined_s{state_fips}.parquet"
    acs_df.to_parquet(acs_out, index=False)
    print(f"  Saved: {acs_out.name}")

    # ── 2. Run cohort model ────────────────────────────────────────────────
    print(f"\n=== STEP 2: Running cohort-component model ===")
    proj_df = run_all_counties(acs_df, start_year=start_year, end_year=end_year, n_sim=n_sim)

    proj_out = OUTPUT_DIR / f"projections_s{state_fips}.parquet"
    proj_df.to_parquet(proj_out, index=False)
    print(f"  Saved: {proj_out.name}")

    # ── 3. Build summary table (one row per county) ────────────────────────
    print("\n=== STEP 3: Building county summary ===")
    summary = _build_summary(proj_df, start_year, end_year)
    summary_out = OUTPUT_DIR / f"county_summary_s{state_fips}.csv"
    summary.to_csv(summary_out, index=False)
    print(f"  Saved: {summary_out.name}")

    # ── 4. Build state aggregate ───────────────────────────────────────────
    print("\n=== STEP 4: Building state aggregate ===")
    state_proj = _build_state_aggregate(proj_df)
    state_out  = OUTPUT_DIR / f"state_projection_s{state_fips}.parquet"
    state_proj.to_parquet(state_out, index=False)
    print(f"  Saved: {state_out.name}")

    # ── 5. Fetch QCEW sector data (optional) ──────────────────────────────
    if run_sectors:
        print("\n=== STEP 5: Fetching BLS QCEW sector data ===")
        QCEW_CACHE.mkdir(parents=True, exist_ok=True)
        county_fips3 = summary["county_fips"].astype(str).str.zfill(3).tolist()
        county_qcew, state_qcew, state_totals = fetch_state_qcew(
            state_fips=state_fips,
            county_fips3_list=county_fips3,
            cache_dir=QCEW_CACHE,
        )
        print(f"  QCEW: {len(county_qcew)} county-sector-year rows, "
              f"{len(state_qcew)} state-sector-year rows")

        # ── 6. Run sector forecast model ───────────────────────────────────
        print("\n=== STEP 6: Running industry sector forecast model ===")
        county_sector_df, state_sector_df = run_all_sectors(
            county_qcew  = county_qcew,
            state_qcew   = state_qcew,
            state_totals = state_totals,
            cohort_proj  = proj_df,
            state_fips   = state_fips,
        )

        sec_county_out = OUTPUT_DIR / f"sector_projections_s{state_fips}.parquet"
        sec_state_out  = OUTPUT_DIR / f"state_sector_projection_s{state_fips}.parquet"
        county_sector_df.to_parquet(sec_county_out, index=False)
        state_sector_df.to_parquet(sec_state_out,   index=False)
        print(f"  Saved: {sec_county_out.name}")
        print(f"  Saved: {sec_state_out.name}")

    # ── 7. Print quick summary ─────────────────────────────────────────────
    _print_summary(summary, state_fips, start_year, end_year)

    print(f"\nAll outputs in: {OUTPUT_DIR.resolve()}")
    print("Run the dashboard with:\n  streamlit run dashboard/app.py\n")
    return proj_df, summary


def _build_summary(proj_df: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """One-row-per-county summary: baseline + mid + end-point metrics."""
    base = proj_df.groupby("county_fips").first()[
        ["county_name", "workforce_base", "pop_total_base", "mig_mean_pct", "mig_std_pct"]
    ].reset_index()

    mid_year = (start_year + end_year) // 2

    end_src = proj_df[proj_df["year"] == end_year][
        ["county_fips", "p5", "p10", "p25", "p50", "p75", "p90", "p95",
         "mean", "retirements_p50", "entries_p50", "pct_change_p50"]
    ].rename(columns={
        "p5":              "wf_end_p5",
        "p10":             "wf_end_p10",
        "p25":             "wf_end_p25",
        "p50":             "wf_end_p50",
        "p75":             "wf_end_p75",
        "p90":             "wf_end_p90",
        "p95":             "wf_end_p95",
        "mean":            "wf_end_mean",
        "retirements_p50": "annual_retirements_end",
        "entries_p50":     "annual_entries_end",
        "pct_change_p50":  "pct_change_end",
    })

    mid_src = proj_df[proj_df["year"] == mid_year][
        ["county_fips", "p50", "pct_change_p50"]
    ].rename(columns={"p50": "wf_mid_p50", "pct_change_p50": "pct_change_mid"})

    summary = base.merge(end_src, on="county_fips", how="left") \
                  .merge(mid_src, on="county_fips", how="left")
    summary["state_fips"]          = proj_df["state_fips"].iloc[0]
    summary["forecast_end_year"]   = end_year
    summary["forecast_start_year"] = start_year
    return summary.sort_values("pct_change_end")


def _build_state_aggregate(proj_df: pd.DataFrame) -> pd.DataFrame:
    """Sum county projections to state level per year."""
    cols = ["p5", "p10", "p25", "p50", "p75", "p90", "p95",
            "mean", "retirements_p50", "entries_p50", "workforce_base"]
    agg  = proj_df.groupby("year")[cols].sum().reset_index()
    agg["pct_change_p50"] = (
        (agg["p50"] - agg["workforce_base"].iloc[0]) / agg["workforce_base"].iloc[0] * 100
    ).round(2)
    return agg


def _print_summary(summary: pd.DataFrame, state_fips: str, sy: int, ey: int):
    total_base  = summary["workforce_base"].sum()
    total_end   = summary["wf_end_p50"].sum()
    net_change  = total_end - total_base
    pct_change  = net_change / total_base * 100

    growing   = (summary["pct_change_end"] > 0).sum()
    declining = (summary["pct_change_end"] <= 0).sum()

    print(f"\n{'='*55}")
    print(f"  Kansas Workforce Forecast  {sy}–{ey}")
    print(f"{'='*55}")
    print(f"  Base workforce (2023):   {total_base:>12,.0f}")
    print(f"  Projected {ey} (median): {total_end:>12,.0f}")
    print(f"  Net change:              {net_change:>+12,.0f}  ({pct_change:+.1f}%)")
    print(f"  Counties growing:        {growing:>3} / {len(summary)}")
    print(f"  Counties declining:      {declining:>3} / {len(summary)}")
    print(f"\n  Top 5 growing counties:")
    for _, r in summary.nlargest(5, "pct_change_end").iterrows():
        print(f"    {r['county_name']:<25}  {r['pct_change_end']:>+6.1f}%")
    print(f"\n  Top 5 declining counties:")
    for _, r in summary.nsmallest(5, "pct_change_end").iterrows():
        print(f"    {r['county_name']:<25}  {r['pct_change_end']:>+6.1f}%")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    _load_env()

    parser = argparse.ArgumentParser(description="Run workforce cohort-component forecast")
    parser.add_argument("--state",  default="20",  help="State FIPS code (default: 20 = Kansas)")
    parser.add_argument("--key",    default=None,  help="Census API key (overrides .env)")
    parser.add_argument("--sims",   default=2000,  type=int, help="Monte Carlo simulations per county")
    parser.add_argument("--start",       default=2026,  type=int, help="Forecast start year")
    parser.add_argument("--end",         default=2035,  type=int, help="Forecast end year")
    parser.add_argument("--no-sectors",  action="store_true",
                        help="Skip QCEW fetch and sector forecast (faster for cohort-only runs)")
    args = parser.parse_args()

    api_key = args.key or os.environ.get("CENSUS_API_KEY")
    if api_key:
        print(f"  Census API key: loaded ({'--key flag' if args.key else '.env'})")
    else:
        print("  Census API key: not set (keyless mode — rate-limited)")

    main(state_fips=args.state, api_key=api_key,
         n_sim=args.sims, start_year=args.start, end_year=args.end,
         run_sectors=not args.no_sectors)
