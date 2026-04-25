"""
sector_model.py
Industry sector workforce forecasting using BLS QCEW historical employment data.

Modeling rules (per county × sector) — REVISED 2026-04-25:
  Option B — independent county OLS trend, used when:
      • 2023 employment >= MIN_OPT_B (500)  AND
      • Sufficient historical observations (n_obs >= 3)
      Significance is no longer a gating criterion — the 80% prediction
      interval already widens appropriately when the trend is uncertain,
      so the p < 0.05 gate was eliminating real (but noisy) county trends.
  Option A — state-share model, used only when:
      • 2023 employment < MIN_OPT_B, OR
      • n_obs < 3, OR
      • County data is suppressed / unavailable

Fitting method:
  Log-linear OLS (fit on log(employment), project, exponentiate back) is the
  default for employment because sector employment grows multiplicatively
  rather than additively. This produces more stable trends, prevents the
  projection from going negative, and yields more realistic long-run paths.
  Linear OLS is used automatically when any historical observation is zero
  (log undefined).

Wages are projected with linear OLS at county level (fall back to state level).
"""

import numpy as np
import pandas as pd
from scipy import stats

MIN_OPT_B = 500   # employment threshold for independent time-series model
                  # (lowered from 2,000 — captures 2.3× more KS county-sectors)

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
    log_linear: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool, float, float]:
    """
    Fit OLS linear (or log-linear) trend and project with prediction intervals.

    Parameters
    ----------
    log_linear : if True, fit OLS on log(values) and back-transform. Falls back
                 to linear OLS automatically if any value is <= 0.

    Returns
    -------
    proj, ci_lo, ci_hi : projected values and 80% prediction interval bounds
    significant        : True if OLS slope p-value < 0.05 (informational only —
                         no longer used to gate model selection)
    slope              : fitted slope (units per year, in fit space)
    p_value            : two-sided p-value for slope
    """
    n = len(years_hist)
    if n < 3:
        mean_val = float(np.nanmean(values_hist))
        std_val  = float(np.nanstd(values_hist)) if n > 1 else mean_val * 0.10
        proj     = np.full(len(years_proj), mean_val)
        margin   = 1.282 * std_val
        return (proj, np.maximum(proj - margin, 0.0), proj + margin,
                False, 0.0, 1.0)

    # Use log-linear only if all values strictly positive
    use_log = log_linear and np.all(values_hist > 0)
    y_fit   = np.log(values_hist) if use_log else values_hist.astype(float)

    slope, intercept, _, p, _ = stats.linregress(years_hist, y_fit)
    fit_proj = intercept + slope * years_proj

    # Prediction interval in fit space (t-distribution, df = n-2)
    residuals = y_fit - (intercept + slope * years_hist)
    s         = float(np.std(residuals, ddof=2)) if n > 2 else float(np.std(residuals, ddof=1))
    x_mean    = float(np.mean(years_hist))
    sxx       = float(np.sum((years_hist - x_mean) ** 2)) or 1.0
    t_crit    = float(stats.t.ppf((1 + pi_coverage) / 2, df=max(n - 2, 1)))
    pred_se   = s * np.sqrt(1.0 + 1.0 / n + (years_proj - x_mean) ** 2 / sxx)
    fit_lo    = fit_proj - t_crit * pred_se
    fit_hi    = fit_proj + t_crit * pred_se

    if use_log:
        proj  = np.exp(fit_proj)
        ci_lo = np.maximum(np.exp(fit_lo), 0.0)
        ci_hi = np.exp(fit_hi)
    else:
        proj  = np.maximum(fit_proj, 0.0)
        ci_lo = np.maximum(fit_lo,  0.0)
        ci_hi = fit_hi

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
        proj, ci_lo, ci_hi, sig, slope, p_val = _ols_project(
            yrs, emp, proj_years, log_linear=True)
        method   = "option_b"
        fit_kind = "log-linear" if np.all(emp > 0) else "linear"
        if sig:
            note = (f"Independent OLS trend ({fit_kind}); "
                    f"p={p_val:.3f}, statistically significant")
        else:
            note = (f"Independent OLS trend ({fit_kind}); "
                    f"p={p_val:.3f}, trend uncertain — CI reflects this")
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

    # ── Wage projection (linear OLS — wages add roughly linearly) ─────────────
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
        wage_proj = state_wage_proj

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
    """
    proj_years = np.array(sorted(cohort_proj["year"].unique()), dtype=float)

    state_cohort_agg = (cohort_proj.groupby("year")["p50"]
                        .sum().reset_index()
                        .sort_values("year"))
    state_cohort_p50 = state_cohort_agg["p50"].values.astype(float)

    state_projs: dict[str, tuple] = {}
    state_sector_rows = []

    for sector in SECTORS:
        s_hist = (state_qcew[state_qcew["sector"] == sector]
                  .sort_values("year")
                  .dropna(subset=["employment"]))

        if len(s_hist) >= 3:
            yrs_s = s_hist["year"].values.astype(float)
            emp_s = s_hist["employment"].values.astype(float)
            proj_s, ci_lo_s, ci_hi_s, sig_s, slope_s, p_s = _ols_project(
                yrs_s, emp_s, proj_years, log_linear=True)
            meth_s = "option_b"
            fit_kind = "log-linear" if np.all(emp_s > 0) else "linear"
            note_s = (f"State OLS ({fit_kind}); p={p_s:.3f}"
                      + (", significant" if sig_s else ", trend uncertain"))
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

        for sector in SECTORS:
            proj_s, ci_lo_s, ci_hi_s, wage_s = state_projs[sector]
            s_hist = (state_qcew[state_qcew["sector"] == sector]
                      .sort_values("year").dropna(subset=["employment"]))

            c_hist = (county_qcew[
                (county_qcew["county_fips"] == fips) &
                (county_qcew["sector"] == sector)
            ].sort_values("year"))

            result = _forecast_one(
                sector              = sector,
                county_hist         = c_hist,
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

            emp_2023 = c_hist[c_hist["year"] == 2023]["employment"]
            result["emp_2023"] = float(emp_2023.iloc[0]) if len(emp_2023) else None

            all_county.append(result)
            done += 1
            if done % 200 == 0 or done == total_ops:
                print(f"    {done}/{total_ops} county-sector combinations")

    county_sector_df = pd.concat(all_county, ignore_index=True) \
        if all_county else pd.DataFrame()

    return county_sector_df, state_sector_df
