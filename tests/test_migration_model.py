"""State-pooled migration model: estimator, shrinkage and simulated ranges."""
import numpy as np
import pandas as pd
import pytest

from cohort_model import (MODEL_COHORTS, WORKFORCE_GROUPS, CountyCohortModel,
                          _fit_variance_components, _period_average_factor,
                          fit_migration_model, nonoverlapping_chain, run_all_counties)

VINTAGES = [2015, 2019, 2021, 2024]
SHAPE = {"under_5": 5, "5_9": 5, "10_14": 5, "15_17": 3, "18_24": 7, "25_29": 5, "30_34": 5,
         "35_39": 5, "40_44": 5, "45_49": 5, "50_54": 5, "55_59": 5, "60_64": 5}


def _state(sizes, rates, noise_cv=0.0, seed=0, transient_sd=0.0):
    """Synthetic ACS frame: each county ages forward with its own annual rate.

    ``transient_sd`` adds a one-off deviation to each county's rate in each of
    the two periods (through 2019, and after).
    """
    helper = CountyCohortModel({}, [], n_sim=1)
    rng = np.random.default_rng(seed)
    rows = []
    for index, (size, rate) in enumerate(zip(sizes, rates)):
        cohorts = {g: size * SHAPE[g] / 45 for g in MODEL_COHORTS}
        one_off = transient_sd * rng.standard_normal(2)
        for year in range(VINTAGES[0], VINTAGES[-1] + 1):
            if year > VINTAGES[0]:
                cohorts = helper._step(cohorts, rate + one_off[int(year > 2019)])
            if year in VINTAGES:
                error = np.exp(noise_cv / np.sqrt(size) * rng.standard_normal()) if noise_cv else 1.0
                row = {f"pop_{g}": cohorts[g] * error for g in MODEL_COHORTS}
                row.update(county_fips=f"{index:03d}", county_name=f"County {index}", year=year,
                           state_fips="99", pop_total=sum(row.values()),
                           pop_working_age=sum(row[f"pop_{g}"] for g in WORKFORCE_GROUPS),
                           acs_period_start_year=year - 4, acs_period_end_year=year)
                rows.append(row)
    return pd.DataFrame(rows)


def test_chain_ends_at_latest_vintage_and_skips_overlapping_ones():
    history = [{"year": y, "acs_period_start_year": y - 4, "acs_period_end_year": y} for y in VINTAGES]
    assert [row["year"] for row in nonoverlapping_chain(history)] == [2015, 2019, 2024]


def test_run_all_counties_uses_the_age_structured_residual():
    """Regression: history once carried no age cohorts, so every county fell
    back to aggregate working-age growth plus a flat 0.3%."""
    rates = np.linspace(-0.02, 0.02, 12)
    acs = _state([20000] * 12, rates)
    proj = run_all_counties(acs, 2025, 2026, n_sim=50, random_seed=1)
    first = proj.groupby("county_fips").first()
    assert set(first.migration_method) == {"state_pooled_shrinkage"}
    # Youth entering mid-period are not scaled by migration, so recovery is close, not exact.
    assert np.allclose(first.mig_raw_pct.to_numpy() / 100, rates, atol=1.5e-3)
    aggregate = [(g.pop_working_age.iloc[-1] / g.pop_working_age.iloc[0]) ** (1 / 9) - 1 + 0.003
                 for _, g in acs[acs.year.isin([2015, 2024])].groupby("county_fips")]
    assert np.abs(first.mig_raw_pct.to_numpy() / 100 - aggregate).max() > 2e-3


def test_variance_components_are_recovered_from_simulated_counties():
    rng = np.random.default_rng(3)
    counties, spans = 3000, np.array([4.0, 5.0])
    sizes = np.exp(rng.uniform(np.log(600), np.log(300000), counties))
    tau, omega, kappa = 0.007, 0.004, 2.0
    errors = rng.standard_normal((counties, 3)) * np.sqrt(kappa / sizes)[:, None]
    x = (tau * rng.standard_normal((counties, 1)) + omega * rng.standard_normal((counties, 2))
         + np.column_stack([(errors[:, 1] - errors[:, 0]) / 4, (errors[:, 2] - errors[:, 1]) / 5]))
    noise = np.diag(2 / spans ** 2)
    noise[0, 1] = noise[1, 0] = -1 / 20
    tau2, omega2, fitted_kappa = _fit_variance_components(x, 1 / sizes, noise)
    assert np.sqrt(tau2) == pytest.approx(tau, rel=0.1)
    assert np.sqrt(omega2) == pytest.approx(omega, rel=0.15)
    assert fitted_kappa == pytest.approx(kappa, rel=0.15)


def test_small_noisy_counties_shrink_further_than_large_ones():
    rng = np.random.default_rng(5)
    sizes = np.array([800] * 30 + [150000] * 30)
    rates = rng.normal(0.0, 0.006, 60)
    params, info = fit_migration_model(_state(sizes, rates, noise_cv=1.5, seed=9))
    small, large = params.iloc[:30], params.iloc[30:]
    assert info["method"] == "state_pooled_shrinkage" and info["chain_years"] == [2015, 2019, 2024]
    assert small.mig_shrink_weight.max() < large.mig_shrink_weight.min()
    assert ((params.mig_shrink_weight >= 0) & (params.mig_shrink_weight <= 1)).all()
    # Share of a group's own departures from the statewide rate that is kept
    # (a regression slope; single-county ratios blow up near the state rate).
    def kept(group):
        own, used = group.mig_raw - info["state_mean"], group.mig_mean - info["state_mean"]
        return float((own * used).sum() / (own ** 2).sum())
    assert 0 < kept(small) < 0.7 and 0.9 < kept(large) < 1.05
    assert small.mig_mean_sd.min() > large.mig_mean_sd.max()
    assert small.base_cv.min() > large.base_cv.max()


def test_too_little_history_falls_back_to_county_estimates():
    acs = _state([5000] * 12, [0.0] * 12)
    params, info = fit_migration_model(acs[acs.year == 2024])
    assert params is None and info["method"] == "county_history_only"
    proj = run_all_counties(acs[acs.year == 2024], 2025, 2025, n_sim=5, random_seed=1)
    assert set(proj.migration_method) == {"county_history_only"}


def _baseline(size=9000):
    return {f"pop_{g}": size * SHAPE[g] / 45 for g in MODEL_COHORTS}


def test_each_uncertainty_source_widens_the_range_and_keeps_the_median():
    def spread(**overrides):
        migration = dict(mig_mean=0.002, mig_annual_sd=0.0, mig_mean_sd=0.0, mig_state_sd=0.0, base_cv=0.0)
        migration.update(overrides)
        model = CountyCohortModel(_baseline(), [], n_sim=4000, base_year=2024, migration=migration)
        last = model.project(2025, 2034, random_seed=11).iloc[-1]
        return last.p90 - last.p10, last.p50

    none, median = spread()
    assert none == pytest.approx(0, abs=1e-6)
    for source in ({"mig_annual_sd": 0.005}, {"mig_mean_sd": 0.005}, {"mig_state_sd": 0.005}, {"base_cv": 0.03}):
        width, shifted = spread(**source)
        assert width > 0 and shifted == pytest.approx(median, rel=0.01)
    # A rate held for ten years moves the total more than ten independent shocks.
    assert spread(mig_mean_sd=0.005)[0] > 2 * spread(mig_annual_sd=0.005)[0]


def test_shared_statewide_draw_correlates_counties():
    migration = dict(mig_mean=0.0, mig_annual_sd=0.002, mig_mean_sd=0.0, mig_state_sd=0.01, base_cv=0.0)
    common = np.random.default_rng(2).standard_normal(1500)

    def last_year(seed, shock):
        model = CountyCohortModel(_baseline(), [], n_sim=1500, base_year=2024, migration=migration)
        return model.project(2025, 2030, include_simulations=True, random_seed=seed,
                             common_shock=shock)[1]["wf"][:, -1]

    assert np.corrcoef(last_year(1, common), last_year(2, common))[0, 1] > 0.8
    assert abs(np.corrcoef(last_year(1, None), last_year(2, None))[0, 1]) < 0.1
    with pytest.raises(ValueError, match="one draw per simulation"):
        last_year(1, common[:10])


def test_fallback_with_few_counties_still_uses_the_age_structured_residual():
    """Below MIN_POOLED_COUNTIES each county estimates alone, from its own cohorts."""
    rates = [-0.015, 0.0, 0.015]
    acs = _state([20000] * 3, rates)
    first = run_all_counties(acs, 2025, 2025, n_sim=5, random_seed=1).groupby("county_fips").first()
    assert set(first.migration_method) == {"county_history_only"}
    assert np.allclose(first.mig_mean_pct.to_numpy() / 100, rates, atol=1.5e-3)
    aggregate = [(g.pop_working_age.iloc[-1] / g.pop_working_age.iloc[0]) ** (1 / 9) - 1 + 0.003
                 for _, g in acs[acs.year.isin([2015, 2024])].groupby("county_fips")]
    assert np.abs(first.mig_mean_pct.to_numpy() / 100 - aggregate).max() > 2e-3


def test_run_all_counties_gives_every_county_the_same_statewide_draw(monkeypatch):
    seen = []
    original = CountyCohortModel.project

    def record(self, *args, **kwargs):
        seen.append(kwargs.get("common_shock"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CountyCohortModel, "project", record)
    run_all_counties(_state([20000] * 12, np.linspace(-0.01, 0.01, 12)), 2025, 2026,
                     n_sim=40, random_seed=3, return_state_simulations=True)
    assert len(seen) == 12 and all(shock is not None and shock.shape == (40,) for shock in seen)
    assert all(np.array_equal(shock, seen[0]) for shock in seen) and np.std(seen[0]) > 0


def test_fit_separates_lasting_county_differences_from_shared_vintage_noise():
    """One vintage's survey error enters two periods with opposite signs. A fit
    that ignored or reversed that link would put the lasting spread near zero."""
    rng = np.random.default_rng(21)
    counties, tau, noise_cv = 300, 0.006, 1.5
    acs = _state([1500] * counties, rng.normal(0.0, tau, counties), noise_cv=noise_cv, seed=4)
    _, info = fit_migration_model(acs)
    assert np.sqrt(info["persistent_var"]) == pytest.approx(tau, rel=0.25)
    # Working-age residents are 47/45 of the helper's size unit.
    assert info["sampling_kappa"] == pytest.approx(noise_cv ** 2 * 47 / 45, rel=0.3)


def test_statewide_rate_spread_and_annual_shock_follow_their_formulas():
    rng = np.random.default_rng(8)
    sizes = np.array([400000] + [3000] * 40)
    rates = np.concatenate([[0.012], rng.normal(-0.008, 0.004, 40)])
    acs = _state(sizes, rates, noise_cv=1.0, seed=2, transient_sd=0.006)
    _, info = fit_migration_model(acs)
    assert info["transient_var"] > 0 and info["annual_sd"] > np.sqrt(info["transient_var"])
    period_rates = np.array(info["state_period_rates"])
    # Population-weighted: the one large county outweighs forty small ones.
    assert info["state_mean"] > 0 > np.mean(rates)
    assert info["state_mean"] == pytest.approx(np.average(period_rates, weights=[4, 5]))
    assert info["state_sd"] == pytest.approx(np.std(period_rates, ddof=1) * np.sqrt(1.5))
    assert info["state_sd"] > 0
    assert info["annual_sd"] ** 2 * _period_average_factor(4.5, info["ar1"]) == pytest.approx(info["transient_var"])


def test_period_average_factor_handles_whole_and_fractional_spans():
    assert _period_average_factor(1, 0.3) == pytest.approx(1.0)
    assert _period_average_factor(4, 0.3) == pytest.approx(0.38837, abs=1e-5)
    assert _period_average_factor(5, 0.3) == pytest.approx(0.32257, abs=1e-5)
    assert _period_average_factor(4.5, 0.3) == pytest.approx((0.38837 + 0.32257) / 2, abs=1e-5)
    assert _period_average_factor(4, 0.0) == pytest.approx(0.25)
