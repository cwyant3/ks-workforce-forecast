"""Origin isolation, suppression preservation, and matched fertility comparisons."""
import numpy as np
import pandas as pd
import pytest

from fertility_validation import (COUNTIES, MATERNAL_BANDS, COHORT_ID,
                                  cohort_birth_path, compare_fertility_models)


@pytest.fixture
def inputs():
    native = pd.DataFrame([dict(county_fips=c, year=y, sex="female", age_low=0,
                               age_high=None, population=12000., availability_status="published")
                           for c in sorted(COUNTIES) for y in range(2010, 2025)])
    records = []
    for county in ("20173", "20"):
        multiplier = 1 if county == "20173" else 105
        for year in range(2006, 2025):
            for low, high in MATERNAL_BANDS:
                records.append(dict(county_fips=county, year=year, start_year=year, end_year=year,
                                    age_low=low, age_high=high, measure="births_by_maternal_age",
                                    births=(high-low+1)*multiplier, status="published"))
            for measure, number in (("births_total", 50), ("births_unknown_age", 5)):
                records.append(dict(county_fips=county, year=year, start_year=year, end_year=year,
                                    age_low=None, age_high=None, measure=measure,
                                    births=number*multiplier, status="published"))
    return pd.DataFrame(records), native


def test_all_models_share_pairs_and_equal_horizon_metrics(inputs):
    births, native = inputs
    ranking, errors, coverage = compare_fertility_models("20173", births, native)
    assert len(ranking) == 6
    assert coverage["matched_comparisons_per_model"] == 21
    pairs = set(zip(errors.origin, errors.horizon))
    for model, rows in errors.groupby("model"):
        assert set(zip(rows.origin, rows.horizon)) == pairs
        scored = ranking[ranking.model.eq(model)].iloc[0]
        assert scored["Mean absolute error"] == pytest.approx(rows.groupby("horizon").error.mean().mean())
        assert scored["Mean signed bias"] == pytest.approx(rows.groupby("horizon").signed_error.mean().mean())
        grouped = rows.groupby("horizon")
        assert scored["WAPE (%)"] == pytest.approx((100*grouped.error.sum()/grouped.actual.sum()).mean())
    assert all(origin["unknown_age_births_pooled"] == 25 for origin in coverage["origins"])
    # Known-age frozen rates deliberately do not reconstruct the unknown five.
    frozen = ranking[ranking.model.eq(COHORT_ID)].iloc[0]
    assert frozen["Mean signed bias"] == pytest.approx(-5)


def test_later_births_and_population_cannot_leak_into_origin(inputs):
    births, native = inputs
    _, first, _ = compare_fertility_models("20173", births, native, origins=[2015])
    births.loc[births.year > 2015, "births"] *= 3
    native.loc[native.year > 2015, "population"] *= 100
    _, later, _ = compare_fertility_models("20173", births, native, origins=[2015])
    assert first.prediction.tolist() == later.prediction.tolist()
    assert first.actual.tolist() != later.actual.tolist()


def test_suppression_uses_whole_state_rate_and_independent_totals_only_as_targets(inputs):
    births, native = inputs
    totals = births[births.measure.eq("births_total")].copy()
    births.loc[births.measure.eq("births_total"), ["status", "births"]] = ["suppressed", np.nan]
    hidden = births.county_fips.eq("20173") & births.year.eq(2013) & births.age_low.eq(15)
    births.loc[hidden, ["status", "births"]] = ["suppressed", np.nan]
    # Distinct public state rate makes fallback detectable, not merely equal by accident.
    births.loc[births.county_fips.eq("20") & births.age_low.eq(15), "births"] *= 2
    ranking, errors, coverage = compare_fertility_models("20173", births, native,
                                                        totals_history=totals, origins=[2015])
    audit = next(row for row in coverage["rate_audit"] if row["age_low"] == 15)
    assert audit["rate_basis"] == "statewide_pooled"
    assert audit["rate"] == pytest.approx(.02)
    assert errors.actual.eq(50).all()
    assert not ranking.empty
    assert births.loc[hidden, "births"].isna().all()
    assert "never used to reconstruct" in coverage["totals_basis"]


def test_missing_target_removes_same_pair_for_every_model(inputs):
    births, native = inputs
    births.loc[births.county_fips.eq("20173") & births.year.eq(2018)
               & births.measure.eq("births_total"), "births"] = np.nan
    ranking, errors, coverage = compare_fertility_models("20173", births, native, origins=[2015])
    assert set(errors.horizon) == {1, 5}
    assert errors.groupby("model").size().eq(2).all()
    assert ranking.empty  # No implicit reweighting after a whole horizon vanishes.
    assert any("Target total" in row["reason"] for row in coverage["skipped"])


def test_suppressed_state_band_cannot_be_reconstructed(inputs):
    births, native = inputs
    births.loc[births.year.eq(2013) & births.age_low.eq(15), ["status", "births"]] = ["suppressed", np.nan]
    ranking, errors, coverage = compare_fertility_models("20173", births, native, origins=[2015])
    assert ranking.empty and errors.empty
    assert "No usable pooled rate" in coverage["skipped"][0]["reason"]


def test_zero_actual_total_makes_wape_unavailable_not_infinite(inputs):
    births, native = inputs
    births.loc[births.measure.eq("births_total"), "births"] = 0
    ranking, _, _ = compare_fertility_models("20173", births, native, origins=[2015])
    assert ranking["WAPE (%)"].isna().all()
    assert np.isfinite(ranking["Mean absolute error"]).all()


def test_annual_exposure_averages_before_and_after_aging():
    population = np.zeros(120)
    rates = np.zeros(120)
    population[20] = 100
    rates[20] = 1
    assert cohort_birth_path(population, rates, horizon=2) == {1: 50., 2: 0.}
