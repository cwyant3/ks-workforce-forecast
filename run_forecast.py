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

import numpy as np
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

BASE_DIR     = Path(__file__).parent
CACHE_DIR    = BASE_DIR / "data" / "acs_cache"
QCEW_CACHE   = BASE_DIR / "data" / "qcew_cache"
LAUS_CACHE   = BASE_DIR / "data" / "laus_cache"
IPEDS_CACHE  = BASE_DIR / "data" / "ipeds_cache"
LODES_CACHE  = BASE_DIR / "data" / "lodes_cache"
OES_CACHE    = BASE_DIR / "data" / "oes_cache"
CBP_CACHE    = BASE_DIR / "data" / "cbp_cache"
JOLTS_CACHE  = BASE_DIR / "data" / "jolts_cache"
KDOL_CACHE   = BASE_DIR / "data" / "kdol_cache"
KSDE_CACHE   = BASE_DIR / "data" / "ksde_cache"
SSA_CACHE    = BASE_DIR / "data" / "ssa_cache"
BLS_PROJ_CACHE = BASE_DIR / "data" / "bls_proj_cache"
OUTPUT_DIR   = BASE_DIR / "data" / "outputs"

# Ensure the project root is importable
sys.path.insert(0, str(BASE_DIR))
from fetch_acs      import fetch_all
from cohort_model   import run_all_counties
from fetch_qcew     import fetch_state_qcew
from sector_model   import run_all_sectors
from fetch_laus     import fetch_laus, compute_lfpr
from fetch_ipeds    import fetch_ipeds, summarize_by_sector
from fetch_lodes    import fetch_lodes, compute_commute_metrics, latest_commute_snapshot
from fetch_oes      import fetch_oes_state, fetch_oes_by_sector
from fetch_cbp           import fetch_cbp, sector_estab_summary
from fetch_jolts         import fetch_jolts, compute_vacancy_rates
from fetch_kdol_ui       import fetch_kdol_ui, sector_pulse
from fetch_ksde          import fetch_ksde, apply_ksde_override
from fetch_ssa_disability import fetch_ssa_disability, compute_disability_rate
from fetch_bls_proj      import (fetch_national_projections, fetch_ks_state_projections,
                                  sector_demand_outlook)
from participation_model import (build_participation_table, participation_summary,
                                  project_effective_workforce)


def main(state_fips: str = "20", api_key: str | None = None,
         n_sim: int = 2000, start_year: int = 2026, end_year: int = 2035,
         run_sectors: bool = True,
         run_laus: bool = False,
         run_ipeds: bool = False,
         run_lodes: bool = False,
         run_oes: bool = False,
         run_cbp: bool = False,
         run_jolts: bool = False,
         run_kdol: bool = False,
         run_ksde: bool = False,
         run_ssa: bool = False,
         run_bls_proj: bool = False):

    OUTPUT_DIR.mkdir(parents=True,  exist_ok=True)
    CACHE_DIR.mkdir(parents=True,   exist_ok=True)
    LAUS_CACHE.mkdir(parents=True,  exist_ok=True)
    IPEDS_CACHE.mkdir(parents=True, exist_ok=True)
    LODES_CACHE.mkdir(parents=True, exist_ok=True)
    OES_CACHE.mkdir(parents=True,   exist_ok=True)
    CBP_CACHE.mkdir(parents=True,   exist_ok=True)
    JOLTS_CACHE.mkdir(parents=True, exist_ok=True)
    KDOL_CACHE.mkdir(parents=True,    exist_ok=True)
    KSDE_CACHE.mkdir(parents=True,    exist_ok=True)
    SSA_CACHE.mkdir(parents=True,     exist_ok=True)
    BLS_PROJ_CACHE.mkdir(parents=True, exist_ok=True)

    # ── 1. Fetch ACS data ──────────────────────────────────────────────────
    print("\n=== STEP 1: Fetching ACS data ===")
    acs_df = fetch_all(state_fips=state_fips, api_key=api_key, cache_dir=CACHE_DIR)
    print(f"  Loaded {len(acs_df)} county-year rows "
          f"({acs_df['county_fips'].nunique()} counties, "
          f"years: {sorted(acs_df['year'].unique())})")

    acs_out = OUTPUT_DIR / f"acs_combined_s{state_fips}.parquet"
    acs_df.to_parquet(acs_out, index=False)
    print(f"  Saved: {acs_out.name}")

    # ── 1b. KSDE youth cohort override (optional, KS-only) ────────────────
    if run_ksde and state_fips.zfill(2) == "20":
        print("\n=== STEP 1b: Applying KSDE K-12 enrollment override ===")
        ksde_df = fetch_ksde(state_fips=state_fips, cache_dir=KSDE_CACHE)
        if not ksde_df.empty:
            acs_df = apply_ksde_override(acs_df, ksde_df)
            ksde_out = OUTPUT_DIR / "ksde.parquet"
            ksde_df.to_parquet(ksde_out, index=False)
            print(f"  Saved: {ksde_out.name}")
        else:
            print("  KSDE: no data — cohort model will use ACS youth cohorts unchanged")
    elif run_ksde:
        print(f"\n=== STEP 1b: KSDE override — SKIPPED (KS-only; state={state_fips}) ===")

    # ── 2. Run cohort model ────────────────────────────────────────────────
    print(f"\n=== STEP 2: Running cohort-component model ===")
    proj_df, state_sims = run_all_counties(
        acs_df,
        start_year=start_year,
        end_year=end_year,
        n_sim=n_sim,
        return_state_simulations=True,
    )

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
    state_proj = _build_state_aggregate(proj_df, state_sims)
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

    # ── 7. Fetch LAUS labor force data (optional) ─────────────────────────
    if run_laus:
        print("\n=== STEP 7: Fetching BLS LAUS labor force statistics ===")
        bls_key = os.environ.get("BLS_API_KEY")
        if bls_key:
            print("  BLS API key: loaded (.env)")
        else:
            print("  BLS API key: not set (rate-limited, 25 series/request)")

        county_fips3 = summary["county_fips"].astype(str).str.zfill(3).tolist()
        laus_df = fetch_laus(
            state_fips=state_fips,
            county_fips3_list=county_fips3,
            api_key=bls_key,
            cache_dir=LAUS_CACHE,
        )
        print(f"  LAUS: {len(laus_df)} county-year rows, "
              f"{laus_df['county_fips'].nunique()} counties")

        # Compute LFPR by joining with ACS working-age population
        laus_lfpr_df = compute_lfpr(laus_df, acs_df)
        laus_out = OUTPUT_DIR / f"laus_s{state_fips}.parquet"
        laus_lfpr_df.to_parquet(laus_out, index=False)
        print(f"  Saved: {laus_out.name}")

    # ── 8. Fetch IPEDS completions data (optional) ────────────────────────
    if run_ipeds:
        print("\n=== STEP 8: Fetching NCES IPEDS completions ===")
        ipeds_df = fetch_ipeds(
            state_fips=state_fips,
            cache_dir=IPEDS_CACHE,
        )
        print(f"  IPEDS: {len(ipeds_df)} program-year rows, "
              f"{ipeds_df['unitid'].nunique()} institutions")

        ipeds_sector_df = summarize_by_sector(ipeds_df)
        ipeds_out        = OUTPUT_DIR / f"ipeds_s{state_fips}.parquet"
        ipeds_sector_out = OUTPUT_DIR / f"ipeds_by_sector_s{state_fips}.parquet"
        ipeds_df.to_parquet(ipeds_out, index=False)
        ipeds_sector_df.to_parquet(ipeds_sector_out, index=False)
        print(f"  Saved: {ipeds_out.name}")
        print(f"  Saved: {ipeds_sector_out.name}")

    # ── 9. Fetch LODES commute-flow data (optional) ───────────────────────
    if run_lodes:
        print("\n=== STEP 9: Fetching Census LEHD LODES commute flows ===")
        lodes_df = fetch_lodes(
            state_fips=state_fips,
            cache_dir=LODES_CACHE,
        )
        print(f"  LODES: {len(lodes_df)} county-pair-year rows, "
              f"{lodes_df['year'].nunique()} years")

        commute_df  = compute_commute_metrics(lodes_df, state_fips)
        snapshot_df = latest_commute_snapshot(lodes_df, state_fips)

        lodes_out    = OUTPUT_DIR / f"lodes_s{state_fips}.parquet"
        commute_out  = OUTPUT_DIR / f"commute_metrics_s{state_fips}.parquet"
        snapshot_out = OUTPUT_DIR / f"commute_snapshot_s{state_fips}.parquet"
        lodes_df.to_parquet(lodes_out,    index=False)
        commute_df.to_parquet(commute_out, index=False)
        snapshot_df.to_parquet(snapshot_out, index=False)
        print(f"  Saved: {lodes_out.name}")
        print(f"  Saved: {commute_out.name}")
        print(f"  Saved: {snapshot_out.name}")

    # ── 10. Fetch BLS OES occupation & wage data (optional) ───────────────
    if run_oes:
        print("\n=== STEP 10: Fetching BLS OES occupation employment & wages ===")
        oes_state_df  = fetch_oes_state(state_fips=state_fips, cache_dir=OES_CACHE)
        oes_sector_df = fetch_oes_by_sector(cache_dir=OES_CACHE)
        print(f"  OES state: {len(oes_state_df)} occupation-year rows")
        print(f"  OES sector: {len(oes_sector_df)} occupation-sector-year rows")

        oes_state_out  = OUTPUT_DIR / f"oes_state_s{state_fips}.parquet"
        oes_sector_out = OUTPUT_DIR / "oes_by_sector.parquet"
        oes_state_df.to_parquet(oes_state_out,  index=False)
        oes_sector_df.to_parquet(oes_sector_out, index=False)
        print(f"  Saved: {oes_state_out.name}")
        print(f"  Saved: {oes_sector_out.name}")

    # ── 11. Fetch CBP establishment trends (optional) ─────────────────────
    if run_cbp:
        print("\n=== STEP 11: Fetching Census County Business Patterns (CBP) ===")
        cbp_df = fetch_cbp(
            state_fips=state_fips,
            api_key=api_key,
            cache_dir=CBP_CACHE,
        )
        print(f"  CBP: {len(cbp_df)} county-NAICS-year rows")

        estab_trends = sector_estab_summary(cbp_df, state_fips)
        cbp_out    = OUTPUT_DIR / f"cbp_s{state_fips}.parquet"
        trends_out = OUTPUT_DIR / f"cbp_estab_trends_s{state_fips}.parquet"
        cbp_df.to_parquet(cbp_out, index=False)
        estab_trends.to_parquet(trends_out, index=False)
        print(f"  Saved: {cbp_out.name}")
        print(f"  Saved: {trends_out.name}")

    # ── 12. Fetch JOLTS vacancy & openings data (optional) ────────────────
    if run_jolts:
        print("\n=== STEP 12: Fetching BLS JOLTS job openings & vacancy rates ===")
        bls_key = os.environ.get("BLS_API_KEY")
        jolts_df = fetch_jolts(api_key=bls_key, cache_dir=JOLTS_CACHE)
        print(f"  JOLTS: {len(jolts_df)} monthly sector-element rows")

        vacancy_df = compute_vacancy_rates(jolts_df)
        jolts_out   = OUTPUT_DIR / "jolts.parquet"
        vacancy_out = OUTPUT_DIR / "jolts_vacancy_rates.parquet"
        jolts_df.to_parquet(jolts_out,    index=False)
        vacancy_df.to_parquet(vacancy_out, index=False)
        print(f"  Saved: {jolts_out.name}")
        print(f"  Saved: {vacancy_out.name}")

    # ── 13. Fetch KDOL UI claims data (optional, KS-only) ─────────────────
    if run_kdol:
        if state_fips.zfill(2) != "20":
            print(f"\n=== STEP 13: KDOL UI — SKIPPED (KS-only; state={state_fips}) ===")
        else:
            print("\n=== STEP 13: Fetching KDOL UI claims (Kansas labor market pulse) ===")
            kdol_df = fetch_kdol_ui(
                state_fips=state_fips,
                cache_dir=KDOL_CACHE,
            )
            if not kdol_df.empty:
                pulse_df = sector_pulse(kdol_df)
                kdol_out  = OUTPUT_DIR / "kdol_ui.parquet"
                pulse_out = OUTPUT_DIR / "kdol_sector_pulse.parquet"
                kdol_df.to_parquet(kdol_out,  index=False)
                pulse_df.to_parquet(pulse_out, index=False)
                print(f"  KDOL: {len(kdol_df)} county-sector-month rows")
                print(f"  Saved: {kdol_out.name}")
                print(f"  Saved: {pulse_out.name}")
            else:
                print("  KDOL: no data loaded — see warning above for manual file path")

    # ── 14. Fetch KSDE K-12 enrollment trends (optional, already applied above) ─
    # KSDE fetch + ACS override runs at Step 1b; here we only save additional
    # trend output if KSDE data was loaded (ksde_df may not exist in scope).

    # ── 15. Fetch SSA disability counts (optional) ────────────────────────
    _laus_for_participation = None
    _ssa_for_participation  = None

    if run_ssa:
        print("\n=== STEP 15: Fetching SSA disability beneficiary counts ===")
        ssa_raw_df = fetch_ssa_disability(state_fips=state_fips, cache_dir=SSA_CACHE)
        if not ssa_raw_df.empty:
            _ssa_for_participation = compute_disability_rate(ssa_raw_df, acs_df)
            ssa_out = OUTPUT_DIR / f"ssa_disability_s{state_fips}.parquet"
            _ssa_for_participation.to_parquet(ssa_out, index=False)
            print(f"  SSA: {len(_ssa_for_participation)} county-year rows")
            print(f"  Saved: {ssa_out.name}")
        else:
            print("  SSA: no data loaded — see warning above")

    # Load LAUS output for participation model if it was run this session
    if run_laus:
        laus_path = OUTPUT_DIR / f"laus_s{state_fips}.parquet"
        if laus_path.exists():
            _laus_for_participation = pd.read_parquet(laus_path)

    # Build three-layer participation table when any layer is available
    if run_ssa or run_laus:
        print("\n=== Building participation model (three-layer workforce estimate) ===")
        part_df = build_participation_table(
            acs_df  = acs_df,
            ssa_df  = _ssa_for_participation,
            laus_df = _laus_for_participation,
        )
        eff_proj_df = project_effective_workforce(part_df, proj_df)
        part_out    = OUTPUT_DIR / f"participation_s{state_fips}.parquet"
        eff_out     = OUTPUT_DIR / f"projections_effective_s{state_fips}.parquet"
        part_df.to_parquet(part_out, index=False)
        eff_proj_df.to_parquet(eff_out, index=False)
        print(f"  Layers used: {part_df['layers_used'].value_counts().to_dict()}")
        print(f"  Saved: {part_out.name}")
        print(f"  Saved: {eff_out.name}")

    # ── 16. Fetch BLS Employment Projections (optional) ───────────────────
    if run_bls_proj:
        print("\n=== STEP 16: Fetching BLS Employment Projections ===")
        natl_proj_df = fetch_national_projections(cache_dir=BLS_PROJ_CACHE)
        ks_proj_df   = pd.DataFrame()
        if state_fips.zfill(2) == "20":
            ks_proj_df = fetch_ks_state_projections(cache_dir=BLS_PROJ_CACHE)

        _proj_frames = [df for df in [natl_proj_df, ks_proj_df] if not df.empty]
        all_proj = pd.concat(_proj_frames, ignore_index=True) if _proj_frames else pd.DataFrame()
        if not all_proj.empty:
            outlook_df  = sector_demand_outlook(all_proj)
            proj_raw_out = OUTPUT_DIR / "bls_proj_occupations.parquet"
            outlook_out  = OUTPUT_DIR / "bls_proj_sector_outlook.parquet"
            all_proj.to_parquet(proj_raw_out, index=False)
            outlook_df.to_parquet(outlook_out, index=False)
            print(f"  BLS Projections: {len(all_proj)} occupations "
                  f"({all_proj['projection_source'].value_counts().to_dict()})")
            print(f"  Saved: {proj_raw_out.name}")
            print(f"  Saved: {outlook_out.name}")
        else:
            print("  BLS Projections: no data loaded — see warnings above")

    # ── 17. Print quick summary ────────────────────────────────────────────
    _print_summary(summary, state_fips, start_year, end_year)

    print(f"\nAll outputs in: {OUTPUT_DIR.resolve()}")
    print("Run the dashboard with:\n  streamlit run dashboard/app.py\n")
    return proj_df, summary


def _build_summary(proj_df: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """One-row-per-county summary: baseline + mid + end-point metrics."""
    base = proj_df.groupby("county_fips").first()[
        ["county_name", "workforce_base", "pop_total_base", "mig_mean_pct", "mig_std_pct",
         "state_fips", "estimate_type", "acs_overlap_note"]
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
    summary["forecast_end_year"]   = end_year
    summary["forecast_start_year"] = start_year
    return summary.sort_values("pct_change_end")


def _build_state_aggregate(
    proj_df: pd.DataFrame,
    state_simulations: dict[str, object] | None = None,
) -> pd.DataFrame:
    """Build state-level projections from aggregate simulations when available."""
    if state_simulations:
        years = sorted(proj_df["year"].unique())
        agg = pd.DataFrame({"year": years})
        wf = state_simulations["wf"]
        for p in [5, 10, 25, 50, 75, 90, 95]:
            agg[f"p{p}"] = np.percentile(wf, p, axis=0)
        agg["mean"] = wf.mean(axis=0)
        agg["retirements_p50"] = np.percentile(state_simulations["retirements"], 50, axis=0)
        agg["entries_p50"] = np.percentile(state_simulations["entries"], 50, axis=0)
        # Use the minimum projection year as the baseline anchor so this is
        # robust to unsorted input and future changes to start_year.
        _base_yr = proj_df["year"].min()
        agg["workforce_base"] = proj_df[proj_df["year"] == _base_yr]["workforce_base"].sum()
        agg["aggregate_method"] = "percentile_of_aggregate_simulations"
    else:
        cols = ["p5", "p10", "p25", "p50", "p75", "p90", "p95",
                "mean", "retirements_p50", "entries_p50", "workforce_base"]
        agg  = proj_df.groupby("year")[cols].sum().reset_index()
        agg["aggregate_method"] = "summed_county_percentiles_legacy"
    agg["pct_change_p50"] = (
        (agg["p50"] - agg["workforce_base"].iloc[0]) / agg["workforce_base"].iloc[0] * 100
    ).round(2)
    agg["state_fips"] = str(proj_df["state_fips"].iloc[0]).zfill(2)
    return agg


def _print_summary(summary: pd.DataFrame, state_fips: str, sy: int, ey: int):
    total_base  = summary["workforce_base"].sum()
    total_end   = summary["wf_end_p50"].sum()
    net_change  = total_end - total_base
    pct_change  = net_change / total_base * 100

    growing   = (summary["pct_change_end"] > 0).sum()
    declining = (summary["pct_change_end"] <= 0).sum()

    print(f"\n{'='*55}")
    print(f"  State {state_fips} Workforce Forecast  {sy}–{ey}")
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
    parser.add_argument("--laus",  action="store_true",
                        help="Fetch BLS LAUS labor force data and compute county LFPR")
    parser.add_argument("--ipeds", action="store_true",
                        help="Fetch NCES IPEDS completions data (postsecondary training output)")
    parser.add_argument("--lodes", action="store_true",
                        help="Fetch Census LEHD LODES commute-flow OD data")
    parser.add_argument("--oes",   action="store_true",
                        help="Fetch BLS OES occupation employment & wage benchmarks")
    parser.add_argument("--cbp",   action="store_true",
                        help="Fetch Census CBP establishment counts and compute trend slopes")
    parser.add_argument("--jolts", action="store_true",
                        help="Fetch BLS JOLTS job openings, hires, and vacancy rates")
    parser.add_argument("--kdol",  action="store_true",
                        help="Fetch Kansas KDOL UI claims (KS only; requires manual file if download fails)")
    parser.add_argument("--ksde", action="store_true",
                        help="Fetch KSDE K-12 enrollment and override ACS youth cohorts (KS only)")
    parser.add_argument("--ssa",  action="store_true",
                        help="Fetch SSA SSDI/SSI disability counts and build participation model Layer 2")
    parser.add_argument("--bls-proj", action="store_true", dest="bls_proj",
                        help="Fetch BLS national employment projections (display layer only)")
    parser.add_argument("--all", action="store_true",
                        help="Fetch all 10 datasets (equivalent to passing every dataset flag)")
    args = parser.parse_args()

    if args.all:
        args.laus = args.ipeds = args.lodes = args.oes = args.cbp = True
        args.jolts = args.kdol = args.ksde = args.ssa = args.bls_proj = True

    api_key = args.key or os.environ.get("CENSUS_API_KEY")
    if api_key:
        print(f"  Census API key: loaded ({'--key flag' if args.key else '.env'})")
    else:
        print("  Census API key: not set (keyless mode — rate-limited)")

    main(state_fips=args.state, api_key=api_key,
         n_sim=args.sims, start_year=args.start, end_year=args.end,
         run_sectors=not args.no_sectors,
         run_laus=args.laus,
         run_ipeds=args.ipeds,
         run_lodes=args.lodes,
         run_oes=args.oes,
         run_cbp=args.cbp,
         run_jolts=args.jolts,
         run_kdol=args.kdol,
         run_ksde=args.ksde,
         run_ssa=args.ssa,
         run_bls_proj=args.bls_proj)
