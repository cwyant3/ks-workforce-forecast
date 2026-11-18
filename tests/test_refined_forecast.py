"""Refined forecast settings (2026-11-18): estimator, survey error, virtual schools."""
import numpy as np
import pandas as pd
import pytest

import fetch_acs
import fetch_ksde
import forecast_settings
from cohort_model import (MODEL_COHORTS, WORKFORCE_GROUPS, CountyCohortModel, _fit_refined_components,
                          fit_migration_model, fit_refined_migration_model, run_all_counties)

VINTAGES = [2009, 2015, 2019, 2021, 2024]
SHAPE = {"under_5": 5, "5_9": 5, "10_14": 5, "15_17": 3, "18_24": 7, "25_29": 5, "30_34": 5,
         "35_39": 5, "40_44": 5, "45_49": 5, "50_54": 5, "55_59": 5, "60_64": 5}


def _state(sizes, rates, survey_cv=0.0, seed=0):
    """Synthetic five-vintage ACS frame with a known survey error per county."""
    helper = CountyCohortModel({}, [], n_sim=1)
    rng = np.random.default_rng(seed)
    rows = []
    for index, (size, rate) in enumerate(zip(sizes, rates)):
        cohorts = {g: size * SHAPE[g] / 45 for g in MODEL_COHORTS}
        cv = survey_cv / np.sqrt(size)
        for year in range(VINTAGES[0], VINTAGES[-1] + 1):
            if year > VINTAGES[0]:
                cohorts = helper._step(cohorts, rate)
            if year in VINTAGES:
                error = np.exp(cv * rng.standard_normal()) if cv else 1.0
                row = {f"pop_{g}": cohorts[g] * error for g in MODEL_COHORTS}
                row.update(county_fips=f"{index:03d}", county_name=f"County {index}", year=year,
                           state_fips="99", pop_total=sum(row.values()),
                           pop_working_age=sum(row[f"pop_{g}"] for g in WORKFORCE_GROUPS),
                           acs_period_start_year=year - 4, acs_period_end_year=year,
                           working_age_survey_cv=cv)
                rows.append(row)
    return pd.DataFrame(rows)


def test_kansas_is_not_a_refined_state():
    """Kansas sits behind an adopted release; changing it is a release decision."""
    assert not forecast_settings.refined("20") and forecast_settings.extra_acs_years("20") == ()
    assert forecast_settings.refined("8") and forecast_settings.extra_acs_years("29") == (2009,)


def test_default_estimator_ignores_the_survey_error_column():
    acs = _state([20000] * 12, np.linspace(-0.01, 0.01, 12), survey_cv=1.0)
    with_column, _ = fit_migration_model(acs)
    without, _ = fit_migration_model(acs.drop(columns="working_age_survey_cv"))
    pd.testing.assert_frame_equal(with_column, without)


def test_refined_model_needs_the_survey_error_column():
    acs = _state([20000] * 12, [0.0] * 12).drop(columns="working_age_survey_cv")
    with pytest.raises(ValueError, match="working_age_survey_cv"):
        fit_migration_model(acs, refined=True)


def test_refined_chain_uses_three_periods_and_keeps_the_statewide_rate():
    rng = np.random.default_rng(4)
    sizes = np.exp(rng.uniform(np.log(800), np.log(400000), 80))
    # Larger counties grow faster: the size tilt must not move the statewide rate.
    rates = 0.002 * (np.log(sizes) - np.log(sizes).mean()) + rng.normal(0, 0.003, 80)
    acs = _state(sizes, rates, survey_cv=1.0, seed=2)
    params, info = fit_refined_migration_model(acs)
    assert info["method"] == "state_pooled_refined" and info["chain_years"] == [2009, 2015, 2019, 2024]
    assert set(params.method) == {"state_pooled_refined"}
    assert info["size_slope"] == pytest.approx(0.002, rel=0.25)
    latest = acs[acs.year == 2024].set_index("county_fips").pop_working_age
    mean_size = acs[acs.year.isin(info["chain_years"])].groupby("county_fips").pop_working_age.mean()
    z = np.log(mean_size) - info["log_size_mean"]
    pattern = info["state_mean"] + info["size_slope"] * z
    assert float((pattern * mean_size).sum() / mean_size.sum()) == pytest.approx(info["state_mean"], abs=1e-12)
    # Shrinkage is toward the size pattern, so the forecast stays near the statewide rate.
    weighted = float((params.mig_mean * latest).sum() / latest.sum())
    assert weighted == pytest.approx(info["state_mean"], abs=0.001)
    assert ((params.mig_shrink_weight >= 0) & (params.mig_shrink_weight <= 1)).all()


def test_refined_variances_are_recovered_and_survey_error_is_not_refitted():
    rng = np.random.default_rng(8)
    counties, spans = 4000, np.array([6.0, 4.0, 5.0])
    sizes = np.exp(rng.uniform(np.log(600), np.log(300000), counties))
    tau, omega, nu = 0.005, 0.004, 0.3
    rel = np.broadcast_to((2.0 / sizes)[:, None], (counties, 4))
    errors = rng.standard_normal((counties, 4)) * np.sqrt(rel)
    x = (tau * rng.standard_normal((counties, 1))
         + np.sqrt(omega ** 2 + nu / sizes)[:, None] * rng.standard_normal((counties, 3))
         + np.diff(errors, axis=1) / spans)
    survey = np.zeros((counties, 3, 3))
    for k in range(3):
        survey[:, k, k] = (rel[:, k] + rel[:, k + 1]) / spans[k] ** 2
        if k < 2:
            survey[:, k, k + 1] = survey[:, k + 1, k] = -rel[:, k + 1] / (spans[k] * spans[k + 1])
    tau2, omega2, fitted_nu = _fit_refined_components(x, 1 / sizes, survey)
    assert np.sqrt(tau2) == pytest.approx(tau, rel=0.1)
    assert np.sqrt(omega2) == pytest.approx(omega, rel=0.15)
    assert fitted_nu == pytest.approx(nu, rel=0.15)


def test_refined_small_counties_get_wider_shocks_and_baseline_error():
    rng = np.random.default_rng(6)
    sizes = np.array([900] * 40 + [200000] * 40)
    rates = rng.normal(0.0, 0.004, 80)
    acs = _state(sizes, rates, survey_cv=1.5, seed=3)
    # Small counties move more from period to period, and it does not reverse.
    jitter = rng.normal(0, 0.03, (40, len(VINTAGES)))
    small = acs.county_fips.astype(int) < 40
    for g in WORKFORCE_GROUPS + ["working_age"]:
        acs.loc[small, f"pop_{g}"] *= np.exp(np.cumsum(jitter, axis=1)).ravel()
    params, info = fit_refined_migration_model(acs)
    assert info["size_volatility"] > 0
    assert params.iloc[:40].mig_annual_sd.min() > params.iloc[40:].mig_annual_sd.max()
    assert params.iloc[:40].base_cv.min() > params.iloc[40:].base_cv.max()
    proj = run_all_counties(acs, 2025, 2030, n_sim=200, random_seed=1, refined=True)
    assert set(proj.migration_method) == {"state_pooled_refined"}
    width = ((proj.p90 - proj.p10) / proj.p50)[proj.year == 2030].to_numpy()
    assert width[:40].min() > width[40:].max()


def test_survey_cv_treats_controlled_cells_as_exact_and_refuses_mostly_missing_rows():
    working = [v for g in fetch_acs.WORKFORCE_GROUPS for v in fetch_acs.AGE_GROUPS[g]]
    others = [v for g in fetch_acs.YOUTH_GROUPS + fetch_acs.RETIREMENT_GROUPS for v in fetch_acs.AGE_GROUPS[g]]
    row = {v[:-1] + "M": 164.5 for v in working + others}      # standard error 100 per cell
    frame = pd.DataFrame([row, {**row, working[0][:-1] + "M": None},
                          {v[:-1] + "M": None for v in working + others}])
    frame["pop_working_age"] = 60000.0
    cv = fetch_acs.working_age_survey_cv(frame)
    w, c = len(working) * 100.0 ** 2, len(others) * 100.0 ** 2
    assert cv.iloc[0] == pytest.approx(np.sqrt(w * c / (w + c)) / 60000)
    # One controlled cell lowers the error slightly; it is never read as missing.
    assert 0 < cv.iloc[1] < cv.iloc[0]
    assert np.isnan(cv.iloc[2])
    # Smaller than the root-sum-of-squares of the working-age cells alone.
    assert cv.iloc[0] < np.sqrt(w) / 60000


def test_fully_virtual_schools_are_subtracted_from_their_district_county(monkeypatch):
    monkeypatch.setattr(fetch_ksde.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch_ksde, "_fetch_grade",
                        lambda year, grade, state_fips: [{"leaid": "0800001", "enrollment": 100},
                                                         {"leaid": "0800002", "enrollment": 50}])
    monkeypatch.setattr(fetch_ksde, "_fetch_school_grade",
                        lambda year, grade, state_fips: [{"ncessch": "080000100001", "enrollment": 30},
                                                         {"ncessch": 80000200002, "enrollment": 20},
                                                         {"ncessch": "080000100003", "enrollment": -1}])
    counties = {"0800001": "067", "0800002": "005"}
    virtual = {"080000100001": "0800001", "080000100003": "0800001"}   # 080000200002 is not virtual
    plain = fetch_ksde._fetch_year_grades(2024, counties, "08")
    without = fetch_ksde._fetch_year_grades(2024, counties, "08", virtual)
    assert plain.groupby("county_fips").enrollment.sum().to_dict() == {"005": 650, "067": 1300}
    assert without.groupby("county_fips").enrollment.sum().to_dict() == {"005": 650, "067": 910}


def test_virtual_school_list_takes_schools_flagged_in_any_year(monkeypatch):
    pages = {2022: [{"ncessch": "080000100001", "leaid": "0800001", "virtual": 1},
                    {"ncessch": "080000100002", "leaid": "0800001", "virtual": 3}],
             2024: [{"ncessch": "080000100009", "leaid": "0800002", "virtual": 1.0},
                    {"ncessch": "080000100002", "leaid": "0800001", "virtual": 2},
                    {"ncessch": "080000100004", "leaid": "0800001", "virtual": None}]}
    monkeypatch.setattr(fetch_ksde, "_paginate", lambda url, params: pages[int(url.rstrip("/").split("/")[-1])])
    assert fetch_ksde._fetch_full_virtual_schools([2022, 2024], "08") == {
        "080000100001": "0800001", "080000100009": "0800002"}


def test_youth_update_records_when_virtual_schools_were_left_out():
    acs = pd.DataFrame([{"county_fips": "001", "year": 2024, "pop_under_5": 500.0, "pop_5_9": 1000.0,
                         "pop_10_14": 1000.0, "pop_15_17": 600.0, "pop_youth": 3100.0,
                         "pop_working_age": 9000.0, "pop_retirement": 2000.0, "pop_total": 14100.0}])
    grades = pd.DataFrame([{"county_fips": "001", "year": year, "grade": grade, "enrollment": 200}
                           for year in (2022, 2024) for grade in range(13)])
    plain = fetch_ksde.apply_ksde_override(acs, grades)
    marked = fetch_ksde.apply_ksde_override(acs, grades.assign(virtual_excluded=True))
    assert plain.ksde_method.iloc[0] == "ccd_ratio_2022_2024"
    assert marked.ksde_method.iloc[0] == "ccd_ratio_2022_2024_novirtual"
