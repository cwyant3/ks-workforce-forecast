"""
sector_model.py
Industry sector workforce forecasting using BLS QCEW historical employment data.

Modeling rules (per county × sector):
  Option B — independent OLS trend, used when:
      • 2023 employment >= MIN_OPT_B (2,000) AND
      • OLS slope is statistically significant (p < 0.05)
  Option A — state-share model, used when:
      • Employment < 2,000, OR
      • OLS trend is not significant, OR
      • County data is suppressed / unavailable
  Option A logic:
      If county has ≥2 historical observations → use county's avg share of
          state sector employment × state-level OLS projection.
      Otherwise → use county's share of state working-age population ×
          state-level OLS projection.

Wages are projected with OLS at county level (fall back to state level).
"""

import numpy as np
import pandas as pd
from scipy import stats

MIN_OPT_B = 2_000   # employment threshold for independent time-series model

SECTORS = [
    "Healthcare",
    "Manufacturing",
    "Hospitality & Entertainment",
    "IT/Computer Services",
    "Skilled Trades",
]


# ── Statistical helpers ───────────────────────────────────────────────────────

def _ols_project(
    years_hist: np.ndarray,
    values_hist: np.ndarray,
    years_proj: np.ndarray,
    pi_coverage: float = 0.80,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool, float, float]:
    """
    Fit OLS linear trend and project with prediction intervals.

    Returns
    -------
    proj, ci_lo, ci_hi : projected values and 80% prediction interval bounds
    significant        : True if OLS slope p-value < 0.05 and slope != 0
    slope              : fitted slope (units per year)
    p_value            : two-sided p-value for slope
    """
    n = len(years_hist)
    if n < 3:
        mean_val = float(np.nanmean(values_hist))
        std_val  = float(np.nanstd(values_hist)) if n > 1 else mean_val * 0.10
        proj     = np.full(len(years_proj), mean_val)
        margin   = 1.282 * std_val   # ~80% normal interval
        return (proj, np.maximum(proj - margin, 0.0), proj + margin,
                False, 0.0, 1.0)

    slope, intercept, _, p, _ = stats.linregress(years_hist, values_hist)
    proj = np.maximum(intercept + slope * years_proj, 0.0)

    # Prediction interval (t-distribution, df = n-2)
    residuals = values_hist - (intercept + slope * years_hist)
    s         = float(np.std(residuals, ddof=2)) if n > 2 else float(np.std(residuals, ddof=1))
    x_mean    = float(np.mean(years_hist))
    sxx       = float(np.sum((years_hist - x_mean) ** 2)) or 1.0
    t_crit    = float(stats.t.ppf((1 + pi_coverage) / 2, df=max(n - 2, 1)))
    pred_se   = s * np.sqrt(1.0 + 1.0 / n + (years_proj - x_mean) ** 2 / sxx)
    ci_lo     = np.maximum(proj - t_crit * pred_se, 0.0)
    ci_hi     = proj + t_crit * pred_se

    significant = bool(p < 0.05 and abs(slope) > 0)
    return proj, ci_lo, ci_hi, significant, float(slope), float(p)


# ── Option A share model ──────────────────────────────────────────────────────

def _option_a(
    county_hist: pd.DataFrame,
    state_sector_proj: np.ndarray,
    state_sector_ci_lo: np.ndarray,
    state_sector_ci_hi: np.ndarray,
    state_hist: pd.DataFrame,
    county_cohort_p50: np.ndarray,
    state_cohort_p50: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Project county sector employment via state-level sector projections × county share.

    Share estimation priority:
      1. County's average historical share of state sector employment (if ≥2 paired obs)
      2. County's working-age population share of state total (demographic proxy)
    """
    county_valid = county_hist[county_hist["employment"].notna()]
    state_valid  = state_hist[state_hist["employment"].notna()]

    share = None

    if len(county_valid) >= 2 and len(state_valid) >= 2:
        merged = county_valid[["year", "employment"]].merge(
            state_valid[["year", "employment"]], on="year", suffixes=("_c", "_s")
        )
        merged = merged[merged["employment_s"] > 0]
        if len(merged) >= 2:
            shares = merged["employment_c"] / merged["employment_s"]
            share  = float(shares.mean())

    if share is None:
        # Fall back to demographic share
        total_s = float(np.mean(state_cohort_p50)) if state_cohort_p50.mean() > 0 else 1.0
        total_c = float(np.mean(county_cohort_p50))
        share   = total_c / total_s if total_s > 0 else 0.01

    share = max(share, 0.0)
    return (state_sector_proj  * share,
            state_sector_ci_lo * share,
            state_sector_ci_hi * share)


# ── Single county × sector forecast ──────────────────────────────────────────

def _forecast_one(
    sector: str,
    county_hist: pd.DataFrame,
    state_sector_proj: np.ndarray,
    state_sector_ci_lo: np.ndarray,
    state_sector_ci_hi: np.ndarray,
    state_hist: pd.DataFrame,
    state_wage_proj: np.ndarray,
    county_cohort_p50: np.ndarray,
    state_cohort_p50: np.ndarray,
    proj_years: np.ndarray,
) -> pd.DataFrame:
    """Return annual projections for one (county, sector) pair."""
    county_valid = county_hist[county_hist["employment"].notna()].sort_values("year")
    latest_emp   = float(county_valid["employment"].iloc[-1]) if len(county_valid) else 0.0
    n_obs        = len(county_valid)

    # ── Employment projection ─────────────────────────────────────────────────
    if latest_emp >= MIN_OPT_B and n_obs >= 3:
        yrs = county_valid["year"].values.astype(float)
        emp = county_valid["employment"].values.astype(float)
        proj, ci_lo, ci_hi, sig, slope, p_val = _ols_project(yrs, emp, proj_years)
        if sig:
            method = "option_b"
            note   = f"Independent OLS trend (slope {slope:+.0f}/yr, p={p_val:.3f})"
        else:
            proj, ci_lo, ci_hi = _option_a(
                county_valid, state_sector_proj, state_sector_ci_lo,
                state_sector_ci_hi, state_hist, county_cohort_p50, state_cohort_p50)
            method = "option_a_fallback"
            note   = f"Trend not significant (p={p_val:.3f}); state share model used"
            sig    = False
    else:
        if latest_emp == 0.0:
            reason = "no data or fully suppressed"
        elif latest_emp < MIN_OPT_B:
            reason = f"employment {latest_emp:,.0f} < {MIN_OPT_B:,} threshold"
        else:
            reason = f"only {n_obs} observations (need ≥3)"
        proj, ci_lo, ci_hi = _option_a(
            county_valid, state_sector_proj, state_sector_ci_lo,
            state_sector_ci_hi, state_hist, county_cohort_p50, state_cohort_p50)
        method = "option_a"
        note   = f"State share model ({reason})"
        sig    = False

    # ── Wage projection ───────────────────────────────────────────────────────
    wage_valid = county_valid[
        county_valid["avg_annual_pay"].notna() & (county_valid["avg_annual_pay"] > 0)
    ]
    if len(wage_valid) >= 3:
        yrs_w = wage_valid["year"].values.astype(float)
        wgs   = wage_valid["avg_annual_pay"].values.astype(float)
        wage_proj, _, _, _, _, _ = _ols_project(yrs_w, wgs, proj_years)
    elif len(wage_valid) >= 1:
        wage_proj = np.full(len(proj_years), float(wage_valid["avg_annual_pay"].iloc[-1]))
    else:
        wage_proj = state_wage_proj   # fall back to state wages

    return pd.DataFrame({
        "year":        proj_years.astype(int),
        "emp_proj":    np.round(proj,     1),
        "emp_ci_lo":   np.round(ci_lo,    1),
        "emp_ci_hi":   np.round(ci_hi,    1),
        "wage_proj":   np.round(wage_proj, 0),
        "method":      method,
        "significant": sig,
        "note":        note,
    })


# ── Public entry point ────────────────────────────────────────────────────────

def run_all_sectors(
    county_qcew:  pd.DataFrame,
    state_qcew:   pd.DataFrame,
    state_totals: pd.DataFrame,
    cohort_proj:  pd.DataFrame,
    state_fips:   str = "20",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Produce sector employment and wage forecasts for every county × sector.

    Parameters
    ----------
    county_qcew  : from fetch_qcew — county_fips, year, sector, employment, avg_annual_pay
    state_qcew   : same structure, state-level
    state_totals : year, total_employment (all industries at state level)
    cohort_proj  : from run_all_counties — county_fips, county_name, year, p50, p10, p90
    state_fips   : 2-digit state FIPS

    Returns
    -------
    county_sector_df : county_fips, county_name, sector, year, emp_proj, emp_ci_lo,
                       emp_ci_hi, wage_proj, emp_2023, method, significant, note
    state_sector_df  : sector, year, emp_proj, emp_ci_lo, emp_ci_hi, wage_proj,
                       emp_2023, method, significant
    """
    proj_years = np.array(sorted(cohort_proj["year"].unique()), dtype=float)

    # ── Precompute state-level p50 aggregate (for share fallback denominator) ─
    state_cohort_agg = (cohort_proj.groupby("year")["p50"]
                        .sum().reset_index()
                        .sort_values("year"))
    state_cohort_p50 = state_cohort_agg["p50"].values.astype(float)

    # ── Precompute state-level sector projections (always Option B for state) ─
    state_projs: dict[str, tuple] = {}   # sector -> (proj, ci_lo, ci_hi, wage_proj)
    state_sector_rows = []

    for sector in SECTORS:
        s_hist = (state_qcew[state_qcew["sector"] == sector]
                  .sort_values("year")
                  .dropna(subset=["employment"]))

        if len(s_hist) >= 3:
            yrs_s = s_hist["year"].values.astype(float)
            emp_s = s_hist["employment"].values.astype(float)
            proj_s, ci_lo_s, ci_hi_s, sig_s, slope_s, p_s = _ols_project(
                yrs_s, emp_s, proj_years)
            meth_s = "option_b" if sig_s else "option_b_flat"
            note_s = (f"State OLS (slope {slope_s:+.0f}/yr, p={p_s:.3f})"
                      if sig_s else f"State OLS not significant (p={p_s:.3f})")
        elif len(s_hist) >= 1:
            last   = float(s_hist["employment"].iloc[-1])
            proj_s = np.full(len(proj_years), last)
            ci_lo_s = proj_s * 0.85
            ci_hi_s = proj_s * 1.15
            sig_s   = False
            meth_s  = "option_b_flat"
            note_s  = "Insufficient state history; held constant"
        else:
            proj_s = ci_lo_s = ci_hi_s = np.zeros(len(proj_years))
            sig_s  = False
            meth_s = "no_data"
            note_s = "No state data available"

        # State wage projection
        sw_hist = s_hist[s_hist["avg_annual_pay"].notna() & (s_hist["avg_annual_pay"] > 0)]
        if len(sw_hist) >= 3:
            wage_proj_s, _, _, _, _, _ = _ols_project(
                sw_hist["year"].values.astype(float),
                sw_hist["avg_annual_pay"].values.astype(float),
                proj_years)
        elif len(sw_hist) >= 1:
            wage_proj_s = np.full(len(proj_years), float(sw_hist["avg_annual_pay"].iloc[-1]))
        else:
            wage_proj_s = np.zeros(len(proj_years))

        state_projs[sector] = (proj_s, ci_lo_s, ci_hi_s, wage_proj_s)

        emp_2023_s = s_hist[s_hist["year"] == 2023]["employment"]
        state_sector_rows.append(pd.DataFrame({
            "sector":      sector,
            "year":        proj_years.astype(int),
            "emp_proj":    np.round(proj_s, 1),
            "emp_ci_lo":   np.round(ci_lo_s, 1),
            "emp_ci_hi":   np.round(ci_hi_s, 1),
            "wage_proj":   np.round(wage_proj_s, 0),
            "emp_2023":    float(emp_2023_s.iloc[0]) if len(emp_2023_s) else None,
            "method":      meth_s,
            "significant": sig_s,
            "note":        note_s,
        }))

    state_sector_df = pd.concat(state_sector_rows, ignore_index=True) \
        if state_sector_rows else pd.DataFrame()

    # ── County × sector loop ──────────────────────────────────────────────────
    county_fips_list = sorted(cohort_proj["county_fips"].unique())
    all_county       = []
    total_ops        = len(county_fips_list) * len(SECTORS)
    done             = 0

    print(f"  Running sector model for {len(county_fips_list)} counties × "
          f"{len(SECTORS)} sectors…")

    for fips in county_fips_list:
        county_rows_df = cohort_proj[cohort_proj["county_fips"] == fips].sort_values("year")
        county_name    = county_rows_df["county_name"].iloc[0]
        c_cohort_p50   = county_rows_df["p50"].values.astype(float)
        c_cohort_p10   = county_rows_df["p10"].values.astype(float)
        c_cohort_p90   = county_rows_df["p90"].values.astype(float)

        for sector in SECTORS:
            proj_s, ci_lo_s, ci_hi_s, wage_s = state_projs[sector]
            s_hist = (state_qcew[state_qcew["sector"] == sector]
                      .sort_values("year").dropna(subset=["employment"]))

            c_hist = (county_qcew[
                (county_qcew["county_fips"] == fips) &
                (county_qcew["sector"] == sector)
            ].sort_values("year"))

            result = _forecast_one(
                sector         = sector,
                county_hist    = c_hist,
                state_sector_proj   = proj_s,
                state_sector_ci_lo  = ci_lo_s,
                state_sector_ci_hi  = ci_hi_s,
                state_hist          = s_hist,
                state_wage_proj     = wage_s,
                county_cohort_p50   = c_cohort_p50,
                state_cohort_p50    = state_cohort_p50,
                proj_years          = proj_years,
            )
            result["county_fips"] = fips
            result["county_name"] = county_name
            result["sector"]      = sector
            result["state_fips"]  = state_fips

            # Attach 2023 QCEW baseline
            emp_2023 = c_hist[c_hist["year"] == 2023]["employment"]
            result["emp_2023"] = float(emp_2023.iloc[0]) if len(emp_2023) else None

            all_county.append(result)
            done += 1
            if done % 200 == 0 or done == total_ops:
                print(f"    {done}/{total_ops} county-sector combinations")

    county_sector_df = pd.concat(all_county, ignore_index=True) \
        if all_county else pd.DataFrame()

    return county_sector_df, state_sector_df
