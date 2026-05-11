"""
cohort_model.py
Annual cohort-component workforce projection model with Monte Carlo confidence intervals.

Components modeled each year:
  1. Survival  — age-specific annual mortality (CDC 2021 national life tables)
  2. Aging     — fraction of each cohort advances to the next cohort
  3. Entries   — 15-17 cohort ages into 18-24 (youth pipeline)
  4. Retirements — 60-64 cohort ages out of workforce into 65+
  5. Migration — net annual migration drawn from estimated county-level distribution

Confidence intervals are generated via Monte Carlo simulation (default 2 000 runs)
by sampling migration rates from the county's estimated historical distribution.
"""

import numpy as np
import pandas as pd

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


class CountyCohortModel:
    """
    Cohort-component model for a single county.

    Parameters
    ----------
    baseline : dict
        Population counts by age group for the ACS base year (2023).
        Expected keys: pop_15_17, pop_18_24, ..., pop_60_64
    history : list[dict]
        List of ACS historical records. The estimator uses vintage and period
        metadata when available so overlapping ACS 5-year rows do not receive
        the same weight as non-overlapping observations.
    n_sim : int
        Number of Monte Carlo simulations for CI estimation.
    base_year : int
        ACS reference year (default 2023).
    """

    def __init__(self, baseline: dict, history: list[dict],
                 n_sim: int = 2000, base_year: int = 2023):
        self.baseline  = baseline
        self.n_sim     = n_sim
        self.base_year = base_year
        self.mig_mean, self.mig_std = self._estimate_migration(history)

    # ── Migration estimation ────────────────────────────────────────────────

    def _estimate_migration(self, history: list[dict]) -> tuple[float, float]:
        """
        Estimate annual net migration rate (as fraction of working-age pop)
        from observed historical working-age population change.

        Method: age-structured cohort-survival residual.

        For each usable ACS vintage pair, age the earlier county age structure
        forward without migration, compare the expected working-age population
        to the later observed working-age population, and annualize the gap as
        the estimated net migration residual.
        """
        if len(history) < 2:
            return 0.0, 0.005

        history_sorted = sorted(history, key=lambda x: x["year"])
        rates = []
        for i in range(1, len(history_sorted)):
            if self._acs_overlap_share(history_sorted[i - 1], history_sorted[i]) > 0.20:
                continue
            rate = self._migration_residual_rate(history_sorted[i - 1], history_sorted[i])
            if rate is not None:
                rates.append(rate)

        used_non_overlap = bool(rates)
        if not rates:
            for i in range(1, len(history_sorted)):
                rate = self._migration_residual_rate(history_sorted[i - 1], history_sorted[i])
                if rate is not None:
                    rates.append(rate)

        if not rates:
            return 0.0, 0.01

        mean = float(np.mean(rates))
        # Minimum uncertainty is wider when only overlapping ACS vintages exist.
        min_std = 0.005 if used_non_overlap else 0.01
        std  = float(max(np.std(rates, ddof=1) if len(rates) > 1 else 0, min_std))
        return mean, std

    def _migration_residual_rate(self, prev: dict, curr: dict) -> float | None:
        """Estimate annual migration rate from a pair of ACS cohort snapshots."""
        n_years = int(curr["year"]) - int(prev["year"])
        if n_years <= 0:
            return None

        observed = float(curr.get("pop_working_age", 0) or 0)
        cohorts = {}
        for group in MODEL_COHORTS:
            value = prev.get(f"pop_{group}")
            if value is None:
                return self._aggregate_migration_residual_rate(prev, curr, n_years)
            cohorts[group] = float(value or 0)

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

        Boundary note: consecutive non-overlapping 5-year vintages (e.g. 2015→2019
        or 2019→2023) share exactly 1 year (the final year of prev equals the first
        of curr), giving overlap_share = 1/5 = 0.20.  The caller's threshold is
        > 0.20 (strict), so these pairs are *included* as valid observations — this
        is intentional.  Only the 2019→2021 pair (3/5 = 0.60 overlap) is excluded.
        """
        p_start = int(prev.get("acs_period_start_year", prev["year"] - 4))
        p_end   = int(prev.get("acs_period_end_year", prev["year"]))
        c_start = int(curr.get("acs_period_start_year", curr["year"] - 4))
        c_end   = int(curr.get("acs_period_end_year", curr["year"]))
        overlap = max(0, min(p_end, c_end) - max(p_start, c_start) + 1)
        width = max(1, c_end - c_start + 1)
        return overlap / width

    # ── Cohort step ────────────────────────────────────────────────────────

    def _step(self, cohorts: dict[str, float], mig_rate: float) -> dict[str, float]:
        """Advance all cohorts by exactly one year."""
        new, _ = self._step_with_flows(cohorts, mig_rate)
        return new

    def _step_with_flows(self, cohorts: dict[str, float],
                         mig_rate: float) -> tuple[dict[str, float], dict[str, float]]:
        """Advance cohorts by one year and return pre-migration flow metrics."""
        new: dict[str, float] = {}

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

        # Apply net migration proportionally across working-age cohorts
        wf_total = sum(new.get(g, 0) for g in WORKFORCE_GROUPS)
        if wf_total > 0:
            migration = wf_total * mig_rate
            for g in WORKFORCE_GROUPS:
                share     = new.get(g, 0) / wf_total
                new[g]    = new.get(g, 0) + migration * share

        flows = {
            "entries": aging_in_18 * ANNUAL_SURVIVAL["18_24"],
            "retirements": (cohorts.get("60_64", 0) / COHORT_WIDTH["60_64"])
                           * ANNUAL_SURVIVAL["60_64"],
        }
        return new, flows

    # ── Projection ─────────────────────────────────────────────────────────

    def project(self, start_year: int = 2026, end_year: int = 2035,
                include_simulations: bool = False,
                random_seed: int | None = None):
        """
        Run Monte Carlo projection from base_year to end_year.

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

        sims_wf    = np.zeros((self.n_sim, n_years))
        sims_ret   = np.zeros((self.n_sim, n_years))
        sims_entry = np.zeros((self.n_sim, n_years))
        rng = np.random.default_rng(random_seed)

        for s in range(self.n_sim):
            # Draw a migration rate sequence for this simulation
            # Use a slightly auto-correlated draw (AR(1) φ=0.3) to reflect that
            # migration conditions tend to persist year-over-year
            phi = 0.3
            total_steps = warm_up_steps + n_years
            mig_shocks  = rng.normal(0, self.mig_std, total_steps)
            mig_rates   = np.zeros(total_steps)
            mig_rates[0] = self.mig_mean + mig_shocks[0]
            for t in range(1, total_steps):
                mig_rates[t] = self.mig_mean + phi * (mig_rates[t - 1] - self.mig_mean) \
                               + np.sqrt(1 - phi**2) * mig_shocks[t]

            # Initialize cohorts from 2023 baseline
            cohorts = {g: float(self.baseline.get(f"pop_{g}", 0))
                       for g in MODEL_COHORTS}

            # Warm-up: step from base_year → start_year (not recorded)
            for t in range(warm_up_steps):
                cohorts = self._step(cohorts, mig_rates[t])

            # Forecast window
            for i, t in enumerate(range(warm_up_steps, warm_up_steps + n_years)):
                cohorts, flows = self._step_with_flows(cohorts, mig_rates[t])

                wf = sum(cohorts.get(g, 0) for g in WORKFORCE_GROUPS)
                sims_wf[s, i]    = wf
                sims_ret[s, i]   = flows["retirements"]
                sims_entry[s, i] = flows["entries"]

        percentiles = [5, 10, 25, 50, 75, 90, 95]
        result = pd.DataFrame({"year": years})
        for p in percentiles:
            result[f"p{p}"] = np.percentile(sims_wf, p, axis=0)
        result["mean"]              = sims_wf.mean(axis=0)
        result["retirements_p50"]   = np.percentile(sims_ret,   50, axis=0)
        result["entries_p50"]       = np.percentile(sims_entry, 50, axis=0)
        result["mig_mean_pct"]      = self.mig_mean * 100
        result["mig_std_pct"]       = self.mig_std  * 100

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

    all_results = []
    county_seed_seq = (
        np.random.SeedSequence(random_seed).spawn(len(counties))
        if random_seed is not None else [None] * len(counties)
    )
    for i, fips in enumerate(sorted(counties)):
        county_rows = acs_df[acs_df["county_fips"] == fips].sort_values("year")
        county_base = baseline_df[baseline_df["county_fips"] == fips].iloc[0]

        hist_cols = ["year", "pop_working_age"]
        for col in ["acs_period_start_year", "acs_period_end_year"]:
            if col in county_rows.columns:
                hist_cols.append(col)
        history = county_rows[hist_cols].to_dict("records")

        baseline_dict = county_base.to_dict()

        model   = CountyCohortModel(baseline_dict, history, n_sim=n_sim, base_year=base_year)
        county_seed = (
            int(county_seed_seq[i].generate_state(1)[0])
            if random_seed is not None else None
        )
        if return_state_simulations:
            proj, sims = model.project(
                start_year, end_year,
                include_simulations=True,
                random_seed=county_seed,
            )
        else:
            proj = model.project(start_year, end_year, random_seed=county_seed)

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
