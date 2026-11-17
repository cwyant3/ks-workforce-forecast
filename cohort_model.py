"""
cohort_model.py
Annual cohort-component workforce projection model with Monte Carlo ranges.

Components modeled each year:
  1. Survival  — age-specific annual mortality (CDC 2021 national life tables)
  2. Aging     — fraction of each cohort advances to the next cohort
  3. Entries   — 15-17 cohort ages into 18-24 (youth pipeline)
  4. Retirements — 60-64 cohort ages out of workforce into 65+
  5. Migration — net annual residual change, see fit_migration_model

Migration (revised 2026-11-17). A county's residual rate is what remains after
aging its own ACS age structure forward between non-overlapping ACS 5-year
vintages. One county's residual is mostly survey noise in small counties, so
run_all_counties pools every county in the state (fit_migration_model):

  - each county's rate is shrunk part of the way toward the statewide rate, by
    how much of the county-to-county spread persists from one period to the
    next and how large the county's survey error is;
  - the simulated range adds four sources: uncertainty in the county's
    long-run rate, a statewide shift shared by every county, year-to-year
    shocks, and sampling error in the ACS baseline count itself.

The ranges are model ranges conditional on these assumptions. See
docs/cohort-migration-model.md for the estimator and its backtest.
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize

# ── CDC 2021 Abridged Life Tables — annual probability of surviving one year ──
# Source: National Vital Statistics Reports, Vol 72 No 12 (Nov 2023)
ANNUAL_SURVIVAL: dict[str, float] = {
    "under_5":  0.99912,
    "5_9":      0.99985,
    "10_14":    0.99975,
    "15_17":    0.99957,
    "18_24":    0.99915,
    "25_29":    0.99870,
    "30_34":    0.99820,
    "35_39":    0.99736,
    "40_44":    0.99614,
    "45_49":    0.99430,
    "50_54":    0.99155,
    "55_59":    0.98751,
    "60_64":    0.98143,
    "65_69":    0.97218,
    "70_74":    0.95754,
    "75_plus":  0.90000,
}

# Width of each cohort in years → fraction that ages into next cohort annually
COHORT_WIDTH: dict[str, int] = {
    "under_5": 5,
    "5_9": 5,
    "10_14": 5,
    "15_17": 3,
    "18_24": 7,
    "25_29": 5,
    "30_34": 5,
    "35_39": 5,
    "40_44": 5,
    "45_49": 5,
    "50_54": 5,
    "55_59": 5,
    "60_64": 5,   # aging out = retirement exit
}

YOUTH_PIPELINE_GROUPS = ["under_5", "5_9", "10_14", "15_17"]
WORKFORCE_GROUPS = ["18_24", "25_29", "30_34", "35_39", "40_44",
                    "45_49", "50_54", "55_59", "60_64"]
MODEL_COHORTS = YOUTH_PIPELINE_GROUPS + WORKFORCE_GROUPS
DEFAULT_RANDOM_SEED = 20260424

# ── Migration model assumptions (not estimated) ──────────────────────────────
MIGRATION_AR1 = 0.3              # year-to-year persistence of annual shocks
MAX_ACS_OVERLAP = 0.20           # two 5-year vintages may share one year
MIN_POOLED_COUNTIES = 10         # fewer complete histories: no pooled fit
# With one historical period, persistent and one-off county differences cannot
# be told apart. Half is assumed to persist; two or more periods estimate it.
DEFAULT_PERSISTENT_SHARE = 0.5
# With one period the statewide rate has no observed spread; assume 0.25 pt/yr.
DEFAULT_STATE_SD = 0.0025


def nonoverlapping_chain(history: list[dict]) -> list[dict]:
    """ACS rows, oldest first, that end at the latest vintage and do not overlap.

    Walking back from the latest row keeps the newest data in use. Comparing
    only neighbouring vintages (the pre-2026-11-17 rule) dropped 2019→2024
    whenever a 2021 row sat between them.
    """
    rows = sorted(history, key=lambda x: x["year"])
    if not rows:
        return []
    chain = [rows[-1]]
    for row in reversed(rows[:-1]):
        if CountyCohortModel._acs_overlap_share(row, chain[-1]) <= MAX_ACS_OVERLAP:
            chain.append(row)
    return chain[::-1]


def _has_cohorts(row: dict) -> bool:
    return all(row.get(f"pop_{g}") is not None and not pd.isna(row.get(f"pop_{g}"))
               for g in MODEL_COHORTS)


def _period_average_factor(n_years: float, phi: float) -> float:
    """Variance of an n-year mean of unit-variance AR(1) values.

    A fractional span (periods of 4 and 5 years average 4.5) is interpolated
    between the two whole-year values.
    """
    def whole(n: int) -> float:
        lags = np.arange(1, n)
        return float((n + 2 * np.sum((n - lags) * phi ** lags)) / n ** 2)

    low = max(int(np.floor(n_years)), 1)
    high = max(int(np.ceil(n_years)), 1)
    weight = float(n_years) - low if high > low else 0.0
    return (1 - weight) * whole(low) + weight * whole(high)


class CountyCohortModel:
    """
    Cohort-component model for a single county.

    Parameters
    ----------
    baseline : dict
        Population counts by age group for the ACS base year.
        Expected keys: pop_15_17, pop_18_24, ..., pop_60_64
    history : list[dict]
        ACS historical records with age cohorts and period metadata. Used for
        the county's own residual rate when ``migration`` is not supplied.
    n_sim : int
        Number of Monte Carlo simulations.
    base_year : int
        ACS reference year.
    migration : dict, optional
        Pooled estimates for this county from fit_migration_model: mig_mean,
        mig_annual_sd, mig_mean_sd, mig_state_sd, base_cv. Without it the
        model uses the county's own history and simulates annual shocks only,
        which understates uncertainty; run_all_counties always supplies it
        when the state has enough history.
    """

    def __init__(self, baseline: dict, history: list[dict],
                 n_sim: int = 2000, base_year: int = 2023,
                 migration: dict | None = None):
        self.baseline  = baseline
        self.n_sim     = n_sim
        self.base_year = base_year
        self.mig_mean, self.mig_std = self._estimate_migration(history)
        self.mig_mean_sd  = 0.0   # uncertainty in the county's long-run rate
        self.mig_state_sd = 0.0   # statewide shift shared by all counties
        self.base_cv      = 0.0   # sampling error of the ACS baseline count
        self.migration_method = "county_history_only"
        if migration is not None:
            self.mig_mean     = float(migration["mig_mean"])
            self.mig_std      = float(migration["mig_annual_sd"])
            self.mig_mean_sd  = float(migration["mig_mean_sd"])
            self.mig_state_sd = float(migration["mig_state_sd"])
            self.base_cv      = float(migration["base_cv"])
            self.migration_method = "state_pooled_shrinkage"

    # ── Migration estimation ────────────────────────────────────────────────

    def _estimate_migration(self, history: list[dict]) -> tuple[float, float]:
        """
        County-only estimate of the annual residual rate (fraction of the
        working-age population) and a spread for annual shocks.

        Method: age-structured cohort-survival residual over each pair in the
        non-overlapping ACS chain, averaged with weights equal to the years
        each pair spans. Rows without age cohorts fall back to aggregate
        working-age growth (legacy).
        """
        if len(history) < 2:
            return 0.0, 0.005

        pairs = self._chain_rates(nonoverlapping_chain(history))
        used_non_overlap = bool(pairs)
        if not pairs:
            history_sorted = sorted(history, key=lambda x: x["year"])
            pairs = self._chain_rates(history_sorted)

        if not pairs:
            return 0.0, 0.01

        rates = np.array([rate for rate, _ in pairs])
        years = np.array([n for _, n in pairs], dtype=float)
        mean = float(np.average(rates, weights=years))
        # Minimum uncertainty is wider when only overlapping ACS vintages exist.
        min_std = 0.005 if used_non_overlap else 0.01
        std  = float(max(np.std(rates, ddof=1) if len(rates) > 1 else 0, min_std))
        return mean, std

    def _chain_rates(self, rows: list[dict]) -> list[tuple[float, int]]:
        """(annual rate, years spanned) for each consecutive pair of rows."""
        pairs = []
        for prev, curr in zip(rows, rows[1:]):
            rate = self._migration_residual_rate(prev, curr)
            if rate is not None:
                pairs.append((rate, int(curr["year"]) - int(prev["year"])))
        return pairs

    def _migration_residual_rate(self, prev: dict, curr: dict) -> float | None:
        """Estimate annual migration rate from a pair of ACS cohort snapshots."""
        n_years = int(curr["year"]) - int(prev["year"])
        if n_years <= 0:
            return None

        if not _has_cohorts(prev):
            return self._aggregate_migration_residual_rate(prev, curr, n_years)

        observed = float(curr.get("pop_working_age", 0) or 0)
        cohorts = {g: float(prev[f"pop_{g}"]) for g in MODEL_COHORTS}
        for _ in range(n_years):
            cohorts = self._step(cohorts, 0.0)

        expected = sum(cohorts.get(g, 0) for g in WORKFORCE_GROUPS)
        if expected <= 0 or observed <= 0:
            return None

        return float((observed / expected) ** (1.0 / n_years) - 1.0)

    @staticmethod
    def _aggregate_migration_residual_rate(prev: dict, curr: dict, n_years: int) -> float | None:
        """Legacy fallback for history rows without age cohorts."""
        p0 = float(prev.get("pop_working_age", 0) or 0)
        p1 = float(curr.get("pop_working_age", 0) or 0)
        if p0 <= 0 or p1 <= 0 or n_years <= 0:
            return None
        annual_growth = (p1 / p0) ** (1.0 / n_years) - 1.0
        return float(annual_growth - (-0.003))

    @staticmethod
    def _acs_overlap_share(prev: dict, curr: dict) -> float:
        """Return the share of a 5-year ACS period overlapping the prior row.

        Boundary note: 5-year vintages four years apart (e.g. 2015→2019) share
        exactly 1 year (the final year of prev equals the first of curr),
        giving overlap_share = 1/5 = 0.20, which MAX_ACS_OVERLAP admits.
        2019→2021 (3/5) and 2021→2024 (2/5) are excluded.
        """
        p_start = int(prev.get("acs_period_start_year", prev["year"] - 4))
        p_end   = int(prev.get("acs_period_end_year", prev["year"]))
        c_start = int(curr.get("acs_period_start_year", curr["year"] - 4))
        c_end   = int(curr.get("acs_period_end_year", curr["year"]))
        overlap = max(0, min(p_end, c_end) - max(p_start, c_start) + 1)
        width = max(1, c_end - c_start + 1)
        return overlap / width

    # ── Cohort step ────────────────────────────────────────────────────────

    def _step(self, cohorts: dict, mig_rate) -> dict:
        """Advance all cohorts by exactly one year."""
        new, _ = self._step_with_flows(cohorts, mig_rate)
        return new

    def _step_with_flows(self, cohorts: dict, mig_rate) -> tuple[dict, dict]:
        """Advance cohorts by one year and return pre-migration flow metrics.

        Values may be floats or equal-length arrays (one entry per simulation).
        """
        new: dict = {}

        # Known youth cohorts from the ACS baseline age forward; births after
        # the baseline do not enter the 18-24 workforce range during this window.
        aging_out_under_5 = cohorts.get("under_5", 0) / COHORT_WIDTH["under_5"]
        new["under_5"] = (cohorts.get("under_5", 0) - aging_out_under_5) \
                         * ANNUAL_SURVIVAL["under_5"]

        aging_in_5_9  = aging_out_under_5
        aging_out_5_9 = cohorts.get("5_9", 0) / COHORT_WIDTH["5_9"]
        new["5_9"] = (cohorts.get("5_9", 0) - aging_out_5_9 + aging_in_5_9) \
                     * ANNUAL_SURVIVAL["5_9"]

        aging_in_10_14  = aging_out_5_9
        aging_out_10_14 = cohorts.get("10_14", 0) / COHORT_WIDTH["10_14"]
        new["10_14"] = (cohorts.get("10_14", 0) - aging_out_10_14 + aging_in_10_14) \
                       * ANNUAL_SURVIVAL["10_14"]

        aging_in_15_17  = aging_out_10_14
        aging_out_15_17 = cohorts.get("15_17", 0) / COHORT_WIDTH["15_17"]
        new["15_17"] = (cohorts.get("15_17", 0) - aging_out_15_17 + aging_in_15_17) \
                       * ANNUAL_SURVIVAL["15_17"]

        # 18-24 receives from 15-17
        aging_in_18  = aging_out_15_17
        aging_out_18 = cohorts.get("18_24", 0) * (1.0 / COHORT_WIDTH["18_24"])
        new["18_24"]  = (cohorts.get("18_24", 0) - aging_out_18 + aging_in_18) \
                        * ANNUAL_SURVIVAL["18_24"]

        # 25-29 through 55-59: receive aging-in from prior cohort
        prev_seq = ["18_24", "25_29", "30_34", "35_39", "40_44", "45_49", "50_54", "55_59"]
        curr_seq = ["25_29", "30_34", "35_39", "40_44", "45_49", "50_54", "55_59", "60_64"]
        for prev, curr in zip(prev_seq, curr_seq):
            aging_in  = cohorts.get(prev, 0) * (1.0 / COHORT_WIDTH[prev])
            aging_out = cohorts.get(curr, 0) * (1.0 / COHORT_WIDTH[curr])
            new[curr] = (cohorts.get(curr, 0) - aging_out + aging_in) \
                        * ANNUAL_SURVIVAL[curr]

        # Net migration is proportional across working-age cohorts, so every
        # cohort grows by the same rate. A rate below -100% empties the county.
        factor = np.maximum(1.0 + mig_rate, 0.0)
        for g in WORKFORCE_GROUPS:
            new[g] = new[g] * factor

        flows = {
            "entries": aging_in_18 * ANNUAL_SURVIVAL["18_24"],
            "retirements": (cohorts.get("60_64", 0) / COHORT_WIDTH["60_64"])
                           * ANNUAL_SURVIVAL["60_64"],
        }
        return new, flows

    # ── Projection ─────────────────────────────────────────────────────────

    def project(self, start_year: int = 2026, end_year: int = 2035,
                include_simulations: bool = False,
                random_seed: int | None = None,
                common_shock: np.ndarray | None = None):
        """
        Run Monte Carlo projection from base_year to end_year.

        ``common_shock`` is one standard-normal draw per simulation shared by
        every county in a state, so county simulations add up to a state total
        with correlated migration. Omitted, the county draws its own.

        Returns a DataFrame with columns:
            year, mean, p5, p10, p25, p50, p75, p90, p95,
            retirements_annual (median), entries_annual (median)
        """
        years          = list(range(start_year, end_year + 1))
        n_years        = len(years)
        # Reported years are end-of-year conditions. A start_year of 2024 is
        # one annual step after a 2023 baseline, so warm up only through the
        # year before the first reported year.
        warm_up_steps  = max(start_year - self.base_year - 1, 0)
        total_steps    = warm_up_steps + n_years
        phi            = MIGRATION_AR1
        rng = np.random.default_rng(random_seed)

        # Each simulation holds one long-run rate for the whole horizon: the
        # county's estimate, its uncertainty, and the statewide shift.
        level = self.mig_mean + self.mig_mean_sd * rng.standard_normal(self.n_sim)
        if self.mig_state_sd:
            if common_shock is None:
                common_shock = rng.standard_normal(self.n_sim)
            common_shock = np.asarray(common_shock, dtype=float)
            if common_shock.shape != (self.n_sim,):
                raise ValueError("common_shock needs one draw per simulation")
            level = level + self.mig_state_sd * common_shock

        # Annual shocks around that rate, AR(1) with stationary sd mig_std.
        shocks = rng.normal(0, self.mig_std, (self.n_sim, total_steps))
        deviation = np.zeros((self.n_sim, total_steps))
        deviation[:, 0] = shocks[:, 0]
        for t in range(1, total_steps):
            deviation[:, t] = phi * deviation[:, t - 1] + np.sqrt(1 - phi**2) * shocks[:, t]
        mig_rates = level[:, None] + deviation

        # The baseline is a survey estimate. Its sampling error scales every
        # simulated count; the median is unchanged.
        base_scale = (np.exp(self.base_cv * rng.standard_normal(self.n_sim))
                      if self.base_cv else np.ones(self.n_sim))

        cohorts = {g: np.full(self.n_sim, float(self.baseline.get(f"pop_{g}", 0)))
                   for g in MODEL_COHORTS}

        # Warm-up: step from base_year → start_year (not recorded)
        for t in range(warm_up_steps):
            cohorts = self._step(cohorts, mig_rates[:, t])

        sims_wf    = np.zeros((self.n_sim, n_years))
        sims_ret   = np.zeros((self.n_sim, n_years))
        sims_entry = np.zeros((self.n_sim, n_years))
        for i, t in enumerate(range(warm_up_steps, total_steps)):
            cohorts, flows = self._step_with_flows(cohorts, mig_rates[:, t])
            sims_wf[:, i]    = sum(cohorts[g] for g in WORKFORCE_GROUPS) * base_scale
            sims_ret[:, i]   = flows["retirements"] * base_scale
            sims_entry[:, i] = flows["entries"] * base_scale

        percentiles = [5, 10, 25, 50, 75, 90, 95]
        result = pd.DataFrame({"year": years})
        for p in percentiles:
            result[f"p{p}"] = np.percentile(sims_wf, p, axis=0)
        result["mean"]              = sims_wf.mean(axis=0)
        result["retirements_p50"]   = np.percentile(sims_ret,   50, axis=0)
        result["entries_p50"]       = np.percentile(sims_entry, 50, axis=0)
        result["mig_mean_pct"]      = self.mig_mean * 100
        result["mig_std_pct"]       = self.mig_std  * 100
        result["mig_mean_sd_pct"]   = float(np.hypot(self.mig_mean_sd, self.mig_state_sd)) * 100
        result["base_cv_pct"]       = self.base_cv * 100
        result["migration_method"]  = self.migration_method

        if include_simulations:
            return result, {
                "wf": sims_wf,
                "retirements": sims_ret,
                "entries": sims_entry,
            }
        return result

    # ── Convenience ────────────────────────────────────────────────────────

    @property
    def workforce_2023(self) -> float:
        return sum(float(self.baseline.get(f"pop_{g}", 0)) for g in WORKFORCE_GROUPS)


# ── State-pooled migration model ─────────────────────────────────────────────

def _fit_variance_components(x: np.ndarray, inv_n: np.ndarray, noise: np.ndarray) -> np.ndarray:
    """Gaussian maximum-likelihood fit of (persistent, transient, sampling) variances.

    For county i the centred residual rates across periods have covariance
        tau2·J + omega2·I + (kappa / N_i)·noise
    where ``noise`` carries the ACS sampling error of the vintages each period
    starts and ends on (adjacent periods share a vintage with opposite sign).
    With one period, tau2 and omega2 are not separable and their sum is
    returned as tau2 with omega2 = 0.
    """
    n_periods = x.shape[1]
    ones, eye = np.ones((n_periods, n_periods)), np.eye(n_periods)

    def unpack(params):
        values = np.exp(np.clip(params, -40.0, 10.0))
        return (values[0], values[1], values[2]) if n_periods > 1 else (values[0], 0.0, values[1])

    def negative_log_likelihood(params):
        tau2, omega2, kappa = unpack(params)
        sigma = tau2 * ones + omega2 * eye + kappa * inv_n[:, None, None] * noise
        sign, logdet = np.linalg.slogdet(sigma)
        if (sign <= 0).any():
            return np.inf
        quad = np.einsum("ik,ik->i", x, np.linalg.solve(sigma, x[..., None])[..., 0])
        return float(np.sum(logdet + quad))

    # Starting scales: the largest half of counties carry little sampling
    # error (rate variance), the smallest half are dominated by it (kappa).
    order = np.argsort(inv_n)
    half = max(len(order) // 2, 1)
    rate_var = max(float(np.mean(x[order[:half]] ** 2)), 1e-10)
    kappa0 = max(float(np.median((x[order[half:]] ** 2).mean(axis=1) / inv_n[order[half:]]
                                 / noise.diagonal().mean())) if len(order) > half else 1.0, 1e-6)
    best = None
    for share in (0.5, 0.95):
        for kappa_scale in (0.3, 3.0):
            start = ([rate_var * share, rate_var * (1 - share), kappa0 * kappa_scale]
                     if n_periods > 1 else [rate_var, kappa0 * kappa_scale])
            result = minimize(negative_log_likelihood, np.log(start), method="Nelder-Mead",
                              options={"xatol": 1e-7, "fatol": 1e-9, "maxiter": 5000})
            if best is None or result.fun < best.fun:
                best = result
    return np.array(unpack(best.x))


def fit_migration_model(acs_df: pd.DataFrame, phi: float = MIGRATION_AR1):
    """Pool every county's residual rates into one state model.

    Returns (per-county parameter frame indexed by county_fips, info dict), or
    (None, info) when the state lacks enough history for a pooled fit.

    Model, in annual rates, for county i and historical period k:
        rate_ik = state_k + county_i + transient_ik + ACS sampling error
    county_i has variance tau2 across counties and persists; transient_ik has
    variance omega2 and does not; sampling error has variance kappa / N_i per
    vintage. The forecast rate is the statewide mean plus the best linear
    unbiased prediction of county_i, which pulls small counties part of the way
    to the statewide rate and leaves large counties near their own. The
    statewide rate is population-weighted, so it reflects the large counties.
    """
    helper = CountyCohortModel({}, [], n_sim=1)
    latest_year = acs_df["year"].max()
    records = {}
    for fips, group in acs_df.groupby("county_fips"):
        chain = nonoverlapping_chain(group.to_dict("records"))
        years = tuple(int(row["year"]) for row in chain)
        rates = [helper._migration_residual_rate(prev, curr) if _has_cohorts(prev) else None
                 for prev, curr in zip(chain, chain[1:])]
        size = float(np.mean([float(row.get("pop_working_age", 0) or 0) for row in chain]))
        latest = float(group.loc[group["year"] == latest_year, "pop_working_age"].sum())
        records[fips] = (years, rates, size, latest)

    info = {"method": "county_history_only", "counties_fitted": 0}
    if not records:
        return None, info
    chain_years = pd.Series([r[0] for r in records.values()]).mode().iloc[0]
    spans = np.diff(chain_years).astype(float)
    fitted = [fips for fips, (years, rates, size, _) in records.items()
              if years == chain_years and size > 0 and rates and all(r is not None for r in rates)]
    if len(spans) == 0 or len(fitted) < MIN_POOLED_COUNTIES:
        return None, info

    rates = np.array([records[f][1] for f in fitted])
    sizes = np.array([records[f][2] for f in fitted])
    n_periods = len(spans)
    state_rates = (rates * sizes[:, None]).sum(axis=0) / sizes.sum()
    centred = rates - state_rates

    # Sampling error of period k is (e_end - e_start) / span_k, so neighbouring
    # periods share one vintage's error with opposite signs.
    noise = np.diag(2.0 / spans ** 2)
    for k in range(n_periods - 1):
        noise[k, k + 1] = noise[k + 1, k] = -1.0 / (spans[k] * spans[k + 1])

    tau2, omega2, kappa = _fit_variance_components(centred, 1.0 / sizes, noise)
    if n_periods == 1:
        tau2, omega2 = DEFAULT_PERSISTENT_SHARE * tau2, (1 - DEFAULT_PERSISTENT_SHARE) * tau2
        state_sd_period = DEFAULT_STATE_SD
    else:
        state_sd_period = float(np.std(state_rates, ddof=1))
    state_mean = float(np.average(state_rates, weights=spans))
    # A future period's statewide rate differs from the historical mean by its
    # own variation plus the error in that mean.
    state_sd = float(state_sd_period * np.sqrt(1 + 1 / n_periods))
    annual_sd = float(np.sqrt(omega2 / _period_average_factor(spans.mean(), phi)))

    ones = np.ones(n_periods)
    position = {fips: i for i, fips in enumerate(fitted)}
    rows = {}
    for fips, (years, county_rates, size, latest) in records.items():
        row = {"mig_mean": state_mean, "mig_mean_sd": float(np.sqrt(tau2)),
               "mig_annual_sd": annual_sd, "mig_state_sd": state_sd,
               "base_cv": float(np.sqrt(kappa / latest)) if latest > 0 else 0.0,
               "mig_raw": np.nan, "mig_shrink_weight": 0.0}
        if fips in position:
            x = centred[position[fips]]
            sigma = tau2 * np.outer(ones, ones) + omega2 * np.eye(n_periods) + kappa / size * noise
            solve = np.linalg.solve(sigma, np.column_stack([x, ones]))
            reliability = tau2 * float(ones @ solve[:, 1])
            row["mig_mean"] = state_mean + tau2 * float(ones @ solve[:, 0])
            row["mig_mean_sd"] = float(np.sqrt(max(tau2 * (1 - reliability), 0.0)))
            row["mig_raw"] = float(np.average(np.array(county_rates), weights=spans))
            row["mig_shrink_weight"] = reliability
        rows[fips] = row

    info = {"method": "state_pooled_shrinkage", "counties_fitted": len(fitted),
            "chain_years": list(chain_years), "state_period_rates": state_rates.tolist(),
            "state_mean": state_mean, "state_sd": state_sd,
            "persistent_var": float(tau2), "transient_var": float(omega2),
            "sampling_kappa": float(kappa), "annual_sd": annual_sd,
            "persistent_share_assumed": n_periods == 1, "ar1": phi}
    return pd.DataFrame.from_dict(rows, orient="index"), info


def run_all_counties(acs_df: pd.DataFrame,
                     start_year: int = 2026,
                     end_year: int   = 2035,
                     n_sim: int      = 2000,
                     return_state_simulations: bool = False,
                     random_seed: int | None = DEFAULT_RANDOM_SEED):
    """
    Run CountyCohortModel for every county in the ACS DataFrame.

    Parameters
    ----------
    acs_df : DataFrame from fetch_acs.fetch_all()
    start_year, end_year : forecast window
    n_sim : Monte Carlo draws per county

    Returns
    -------
    Long-format DataFrame with one row per (county, year).
    """
    base_year = acs_df["year"].max()
    baseline_df = acs_df[acs_df["year"] == base_year]

    counties = baseline_df["county_fips"].unique()
    print(f"Running projections for {len(counties)} counties "
          f"({start_year}–{end_year}, {n_sim} simulations each)…")
    if random_seed is not None:
        print(f"  Random seed: {random_seed}")

    migration, info = fit_migration_model(acs_df)
    if migration is None:
        print("  Migration: too little history for a state-pooled fit; "
              "each county uses its own residual and annual shocks only.")
    else:
        print(f"  Migration: pooled over {info['counties_fitted']} counties, vintages "
              f"{info['chain_years']}; statewide {info['state_mean']*100:+.2f}%/yr "
              f"(sd {info['state_sd']*100:.2f}), persistent county sd "
              f"{np.sqrt(info['persistent_var'])*100:.2f}, annual shock sd {info['annual_sd']*100:.2f}")

    all_results = []
    # One extra child seed drives the statewide shift every county shares.
    seed_seq = (np.random.SeedSequence(random_seed).spawn(len(counties) + 1)
                if random_seed is not None else [None] * (len(counties) + 1))
    common_shock = np.random.default_rng(seed_seq[-1]).standard_normal(n_sim)
    for i, fips in enumerate(sorted(counties)):
        county_rows = acs_df[acs_df["county_fips"] == fips].sort_values("year")
        county_base = baseline_df[baseline_df["county_fips"] == fips].iloc[0]

        # Full rows: the residual needs each vintage's age cohorts. Passing only
        # year and pop_working_age silently selected the legacy aggregate rule.
        history = county_rows.to_dict("records")

        baseline_dict = county_base.to_dict()

        county_migration = (migration.loc[fips].to_dict()
                            if migration is not None and fips in migration.index else None)
        model   = CountyCohortModel(baseline_dict, history, n_sim=n_sim, base_year=base_year,
                                    migration=county_migration)
        county_seed = (
            int(seed_seq[i].generate_state(1)[0])
            if random_seed is not None else None
        )
        if return_state_simulations:
            proj, sims = model.project(
                start_year, end_year,
                include_simulations=True,
                random_seed=county_seed,
                common_shock=common_shock,
            )
        else:
            proj = model.project(start_year, end_year, random_seed=county_seed,
                                 common_shock=common_shock)

        if county_migration is not None:
            proj["mig_raw_pct"]       = county_migration["mig_raw"] * 100
            proj["mig_shrink_weight"] = county_migration["mig_shrink_weight"]
        else:
            proj["mig_raw_pct"]       = model.mig_mean * 100
            proj["mig_shrink_weight"] = 1.0
        proj["county_fips"]       = fips
        proj["county_name"]       = county_base["county_name"]
        proj["workforce_base"]    = model.workforce_2023
        proj["pop_total_base"]    = float(county_base.get("pop_total", 0))
        proj["state_fips"]        = str(county_base.get("state_fips", county_base.get("state", ""))).zfill(2)
        proj["estimate_type"]     = county_base.get("estimate_type", "ACS 5-year")
        proj["acs_overlap_note"]  = "ACS 5-year vintage rows are overlapping period estimates"
        proj["base_year"]         = base_year
        proj["random_seed"]       = random_seed

        all_results.append(proj)
        if return_state_simulations:
            if i == 0:
                state_sims = {name: values.copy() for name, values in sims.items()}
            else:
                for name, values in sims.items():
                    state_sims[name] += values

        if (i + 1) % 20 == 0 or (i + 1) == len(counties):
            print(f"  {i+1}/{len(counties)} counties complete")

    combined = pd.concat(all_results, ignore_index=True)
    combined["pct_change_p50"] = (
        (combined["p50"] - combined["workforce_base"]) / combined["workforce_base"] * 100
    ).round(2)

    if return_state_simulations:
        return combined, state_sims
    return combined
