import numpy as np
import pandas as pd
import pytest

from dashboard.outlook_forecast import (county_rows, exact_sector, prepare_forecast,
    indexed_trajectories, scenario_projection, sector_band, _figure)


def frames():
    population = pd.DataFrame({"county_fips": ["173"] * 2, "state_fips": ["20"] * 2,
        "year": [2026, 2028], "base_year": [2024] * 2, "workforce_base": [110] * 2,
        "p50": [100.6, 120.4], "p10": [90, 100], "p90": [110, 140]})
    effective = population.assign(participation_adj_factor=.75, eff_p50=[75, 90],
                                  eff_p10=[68, 75], eff_p90=[82, 105])
    jobs = pd.DataFrame({"county_fips": ["173"] * 2, "state_fips": ["20"] * 2,
        "sector": ["Healthcare"] * 2, "year": [2026, 2028], "base_year": [2025] * 2,
        "emp_base": [50] * 2, "emp_proj": [55, 60], "emp_ci_lo": [45, 40],
        "emp_ci_hi": [65, 80], "baseline_availability_status": ["published"] * 2})
    return population, effective, jobs


def test_exact_naics_mapping_never_substitutes_group_for_component():
    assert exact_sector("62") == "Healthcare"
    assert exact_sector("31-33") == "Manufacturing"
    assert exact_sector("71+72") == "Hospitality & Entertainment"
    for code in ["22", "23", "81", "51", "54", "71", "72", "unknown"]:
        assert exact_sector(code) is None


def test_county_selection_does_not_mix_identical_codes_in_different_states():
    p, _, _ = frames()
    mixed = pd.concat([p, p.assign(state_fips="08")])
    assert len(county_rows(mixed, "20173")) == 2
    assert county_rows(mixed, "20015").empty
    with pytest.raises(ValueError, match="five-digit"):
        county_rows(p, "173")


def test_forecast_keeps_observed_points_separate_without_fabricated_history():
    p, e, j = frames()
    view = prepare_forecast(p, e, j, None, "20173", "62")
    assert view["population_base"] == (2024, 110)
    assert view["jobs_base"] == (2025, 50)
    assert list(view["population"].year) == [2026, 2028]
    assert list(view["participation"].lower) == [68, 75]
    fig = _figure(view["population"], "#36816B", "Model", "80% simulation range", view["population_base"])
    assert list(fig.data[-1].x) == [2024]
    assert fig.data[-1].mode == "markers"
    assert list(fig.data[2].x) == [2026, 2028]
    assert not hasattr(view, "shortage")


def test_missing_or_suppressed_industry_baseline_is_not_observed():
    p, e, j = frames()
    assert prepare_forecast(p, e, j, None, "20173", "23")["jobs"].empty
    view = prepare_forecast(p, e, j.assign(baseline_availability_status="suppressed"), None, "20173", "62")
    assert view["jobs_base"] is None


def test_state_share_model_discloses_history_and_band_limitations():
    p, e, j = frames()
    j = j.assign(method="option_a", history_last_year=2023, history_usable_years=2,
                 model_quality="older_baseline_proxy")
    view = prepare_forecast(p, e, j, None, "20173", "62")
    assert view["sector_metadata"]["history_last_year"] == [2023]
    assert view["sector_metadata"]["model_quality"] == ["older_baseline_proxy"]
    label, note = sector_band(view)
    assert label == "Scaled state-model band"
    assert "excludes uncertainty" in note
    j["method"] = "option_b"
    assert sector_band(prepare_forecast(p, e, j, None, "20173", "62"))[0] == "80% prediction interval"


def test_duplicate_model_years_fail_instead_of_averaging():
    p, e, j = frames()
    with pytest.raises(ValueError, match="one observation"):
        prepare_forecast(pd.concat([p, p]), e, j, None, "20173", "62")


def test_index_uses_same_model_year_and_never_interpolates():
    p, e, j = frames()
    view = prepare_forecast(p, e, j.iloc[1:], None, "20173", "62")
    indexed = indexed_trajectories(view)
    assert set(indexed.index_year) == {2028}
    assert set(indexed["index"]) == {100}
    assert set(indexed.year) == {2028}


def test_zero_scenario_reproduces_stored_rounded_baseline_exactly():
    p, e, _ = frames()
    result = scenario_projection(p, e, "20173")
    pd.testing.assert_series_equal(result.baseline_participation, result.scenario_participation, check_names=False)
    assert result.change.eq(0).all()
    assert result.scenario_population.equals(result.baseline_population)
    assert list(e.eff_p50) == [75, 90]  # No mutation of caller frames.


def test_scenario_additive_formula_uses_elapsed_years_not_row_count():
    p, e, _ = frames()
    result = scenario_projection(p, e, "20173", participation_pp=5, annual_net_migration=10)
    assert list(result.additional_residents) == [10, 30]
    expected = e.eff_p50.to_numpy() + (p.p50.to_numpy() + [10, 30]) * .8 - p.p50.to_numpy() * .75
    np.testing.assert_allclose(result.scenario_participation, expected)


def test_scenarios_cap_valid_population_and_participation():
    p, e, _ = frames()
    high = scenario_projection(p, e, "20173", participation_pp=100)
    assert high.scenario_factor.eq(1).all()
    assert high.scenario_participation.le(high.scenario_population).all()
    low = scenario_projection(p, e, "20173", participation_pp=-100, annual_net_migration=-1000)
    assert low.scenario_factor.eq(0).all()
    assert low.scenario_population.eq(0).all()
    assert low.scenario_participation.eq(0).all()


def test_scenarios_do_not_infer_missing_factors_or_invent_unmatched_years():
    p, e, _ = frames()
    assert scenario_projection(p, e.assign(participation_adj_factor=np.nan), "20173").empty
    assert scenario_projection(p, e.assign(participation_adj_factor=[.7, .8]), "20173").empty
    result = scenario_projection(p, e.iloc[:1], "20173")
    assert pd.isna(result.loc[1, "scenario_participation"])
    with pytest.raises(ValueError, match="finite"):
        scenario_projection(p, e, "20173", participation_pp=np.inf)
