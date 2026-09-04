import math

import numpy as np
import pandas as pd

from cohort_model import ANNUAL_SURVIVAL, MODEL_COHORTS, WORKFORCE_GROUPS, CountyCohortModel
from fetch_acs import _add_acs_metadata, _add_labor_force_status
from fetch_ksde import apply_ksde_override
from fetch_laus import MEASURES, _parse_series
from fetch_qcew import SECTOR_DISPLAY_NAMES
from participation_model import build_participation_table
from run_forecast import _build_state_aggregate
from scripts.validate_outputs import _failures_for_laus_grain


def test_acs_metadata_adds_state_and_period_fields():
    df = _add_acs_metadata(pd.DataFrame({"county_fips": ["001"], "year": [2023]}), 2023, "8")

    assert df.loc[0, "state_fips"] == "08"
    assert df.loc[0, "acs_period_start_year"] == 2019
    assert df.loc[0, "acs_period_end_year"] == 2023
    assert df.loc[0, "acs_period_midpoint_year"] == 2021
    assert df.loc[0, "estimate_type"] == "ACS 5-year"


def test_migration_estimator_skips_heavily_overlapping_acs_vintages():
    history = [
        {
            "year": 2015,
            "pop_working_age": 1000,
            "acs_period_start_year": 2011,
            "acs_period_end_year": 2015,
        },
        {
            "year": 2019,
            "pop_working_age": 1100,
            "acs_period_start_year": 2015,
            "acs_period_end_year": 2019,
        },
        {
            "year": 2021,
            "pop_working_age": 2000,
            "acs_period_start_year": 2017,
            "acs_period_end_year": 2021,
        },
    ]
    model = CountyCohortModel({}, history, n_sim=1)

    expected = (1100 / 1000) ** (1 / 4) - 1 - (-0.003)
    assert math.isclose(model.mig_mean, expected)


def test_step_ages_known_youth_pipeline_and_records_flows():
    model = CountyCohortModel({}, [], n_sim=1)
    cohorts = {
        "under_5": 500,
        "5_9": 500,
        "10_14": 500,
        "15_17": 300,
        "18_24": 700,
        "25_29": 500,
        "30_34": 500,
        "35_39": 500,
        "40_44": 500,
        "45_49": 500,
        "50_54": 500,
        "55_59": 500,
        "60_64": 500,
    }

    new, flows = model._step_with_flows(cohorts, 0.0)

    assert flows["entries"] == (300 / 3) * ANNUAL_SURVIVAL["18_24"]
    assert flows["retirements"] == (500 / 5) * ANNUAL_SURVIVAL["60_64"]
    assert new["15_17"] == (300 - 100 + 100) * ANNUAL_SURVIVAL["15_17"]
    assert new["under_5"] < cohorts["under_5"]


def test_first_projection_year_is_one_step_after_baseline():
    baseline = {
        "pop_under_5": 500,
        "pop_5_9": 500,
        "pop_10_14": 500,
        "pop_15_17": 300,
        "pop_18_24": 700,
        "pop_25_29": 500,
        "pop_30_34": 500,
        "pop_35_39": 500,
        "pop_40_44": 500,
        "pop_45_49": 500,
        "pop_50_54": 500,
        "pop_55_59": 500,
        "pop_60_64": 500,
    }
    model = CountyCohortModel(baseline, [], n_sim=1, base_year=2023)
    model.mig_mean = 0.0
    model.mig_std = 0.0

    cohorts = {
        g: float(baseline[f"pop_{g}"])
        for g in MODEL_COHORTS
    }
    one_step = model._step(cohorts, 0.0)
    expected_wf = sum(one_step[g] for g in WORKFORCE_GROUPS)

    proj = model.project(start_year=2024, end_year=2024)

    assert proj.loc[0, "year"] == 2024
    assert math.isclose(proj.loc[0, "p50"], expected_wf)


def test_projection_is_reproducible_with_random_seed():
    baseline = {
        "pop_under_5": 500,
        "pop_5_9": 500,
        "pop_10_14": 500,
        "pop_15_17": 300,
        "pop_18_24": 700,
        "pop_25_29": 500,
        "pop_30_34": 500,
        "pop_35_39": 500,
        "pop_40_44": 500,
        "pop_45_49": 500,
        "pop_50_54": 500,
        "pop_55_59": 500,
        "pop_60_64": 500,
    }
    model = CountyCohortModel(baseline, [], n_sim=20, base_year=2023)
    model.mig_mean = 0.01
    model.mig_std = 0.02

    first = model.project(start_year=2024, end_year=2026, random_seed=123)
    second = model.project(start_year=2024, end_year=2026, random_seed=123)
    different = model.project(start_year=2024, end_year=2026, random_seed=456)

    pd.testing.assert_frame_equal(first, second)
    assert not first["p50"].equals(different["p50"])


def test_acs_labor_force_status_uses_civilian_18_64_denominator():
    """
    B23001 publishes each age block as: total, in-labor-force, in-Armed-Forces,
    civilian, civilian-employed, civilian-unemployed, not-in-labor-force. So
    within a block the CIVILIAN labour force sits two variables after the total
    and the ARMED FORCES one sits immediately before it — verified against
    api.census.gov/data/2023/acs/acs5/groups/B23001.json:

        _005E / _091E  ->  "In labor force: In Armed Forces"
        _006E / _092E  ->  "In labor force: Civilian:"
        _008E / _094E  ->  "In labor force: Civilian: Unemployed"

    This test previously supplied the ARMED FORCES variables where it meant
    civilian labour force, and civilian-UNEMPLOYED where it meant armed forces.
    It therefore never supplied a civilian-LF column at all, and asserted 195
    against a sum of nothing. fetch_acs.B23001_18_64_WEIGHTS was correct
    throughout; only the fixture was wrong.
    """
    df = pd.DataFrame(
        {
            # Male/female 16-19 are weighted at 0.5 to approximate 18-19.
            "B23001_003E": [100],   # male 16-19 total
            "B23001_005E": [10],    # male 16-19 in Armed Forces
            "B23001_006E": [40],    # male 16-19 civilian labour force
            "B23001_089E": [100],   # female 16-19 total
            "B23001_091E": [0],     # female 16-19 in Armed Forces
            "B23001_092E": [50],    # female 16-19 civilian labour force
            # Male/female 20-21 are fully included.
            "B23001_010E": [100],   # male 20-21 total
            "B23001_012E": [0],     # male 20-21 in Armed Forces
            "B23001_013E": [80],    # male 20-21 civilian labour force
            "B23001_096E": [100],   # female 20-21 total
            "B23001_098E": [0],     # female 20-21 in Armed Forces
            "B23001_099E": [70],    # female 20-21 civilian labour force
        }
    )

    out = _add_labor_force_status(df)

    assert out.loc[0, "acs_lf_status_pop_18_64"] == 300
    assert out.loc[0, "acs_civilian_labor_force_18_64"] == 195
    assert out.loc[0, "acs_armed_forces_18_64"] == 5
    assert math.isclose(out.loc[0, "acs_lfpr_pct"], round(195 / 295 * 100, 2))


def test_migration_estimator_uses_age_structured_residual_when_cohorts_exist():
    prev = {
        "year": 2015,
        "acs_period_start_year": 2011,
        "acs_period_end_year": 2015,
    }
    prev.update({f"pop_{g}": 0 for g in MODEL_COHORTS})
    prev.update(
        {
            "pop_15_17": 300,
            "pop_18_24": 700,
            "pop_25_29": 500,
            "pop_30_34": 500,
            "pop_35_39": 500,
            "pop_40_44": 500,
            "pop_45_49": 500,
            "pop_50_54": 500,
            "pop_55_59": 500,
            "pop_60_64": 500,
        }
    )
    prev["pop_working_age"] = sum(prev[f"pop_{g}"] for g in WORKFORCE_GROUPS)

    no_migration_model = CountyCohortModel({}, [], n_sim=1)
    cohorts = {g: float(prev[f"pop_{g}"]) for g in MODEL_COHORTS}
    for _ in range(4):
        cohorts = no_migration_model._step(cohorts, 0.0)
    expected_without_migration = sum(cohorts[g] for g in WORKFORCE_GROUPS)

    curr = {
        "year": 2019,
        "pop_working_age": expected_without_migration * (1.02 ** 4),
        "acs_period_start_year": 2015,
        "acs_period_end_year": 2019,
    }
    curr.update({f"pop_{g}": 0 for g in MODEL_COHORTS})
    model = CountyCohortModel({}, [prev, curr], n_sim=1)

    assert math.isclose(model.mig_mean, 0.02, rel_tol=1e-12)


def test_participation_model_prefers_acs_lfpr_over_laus_proxy():
    acs = pd.DataFrame(
        {
            "state_fips": ["20"],
            "county_fips": ["001"],
            "year": [2023],
            "pop_working_age": [1000],
            "acs_lfpr_pct": [72.5],
            "acs_lf_status_pop_18_64": [980],
            "acs_civilian_labor_force_18_64": [710],
            "acs_armed_forces_18_64": [0],
        }
    )
    laus = pd.DataFrame(
        {
            "county_fips": ["001"],
            "year": [2023],
            "labor_force": [950],
            "lfpr_pct": [95.0],
        }
    )

    part = build_participation_table(acs, laus_df=laus)

    assert part.loc[0, "lfpr_pct"] == 72.5
    assert part.loc[0, "effective_labor_force"] == 725
    assert part.loc[0, "lfpr_source"] == "ACS_B23001_civilian_18_64"
    assert "ACS_LFPR" in part.loc[0, "layers_used"]
    assert "LAUS_CONTEXT" in part.loc[0, "layers_used"]


def test_ksde_override_recalculates_youth_and_total_population():
    acs = pd.DataFrame(
        {
            "county_fips": ["001"],
            "year": [2023],
            "pop_under_5": [50],
            "pop_5_9": [100],
            "pop_10_14": [100],
            "pop_15_17": [60],
            "pop_youth": [310],
            "pop_working_age": [1000],
            "pop_retirement": [200],
            "pop_total": [1510],
        }
    )
    ksde = pd.DataFrame(
        {
            "county_fips": ["001", "001", "001"],
            "year": [2023, 2023, 2023],
            "grade_group": ["k_5", "6_8", "9_12"],
            "enrollment": [120, 80, 70],
        }
    )

    out = apply_ksde_override(acs, ksde, baseline_year=2023)

    assert out.loc[0, "pop_youth"] == 320
    assert out.loc[0, "pop_total"] == 1520
    assert out.loc[0, "ksde_override"]


def test_state_aggregate_uses_percentile_of_aggregate_simulations():
    proj = pd.DataFrame(
        {
            "year": [2026, 2035],
            "workforce_base": [10, 10],
            "state_fips": ["20", "20"],
        }
    )
    sims = {
        "wf": np.array([[100, 110], [200, 210], [300, 310]], dtype=float),
        "retirements": np.array([[10, 11], [20, 21], [30, 31]], dtype=float),
        "entries": np.array([[5, 6], [15, 16], [25, 26]], dtype=float),
    }

    state = _build_state_aggregate(proj, sims)

    assert state.loc[0, "aggregate_method"] == "percentile_of_aggregate_simulations"
    assert state.loc[1, "p50"] == 210
    assert state.loc[1, "retirements_p50"] == 21
    assert state.loc[1, "entries_p50"] == 16


def test_broad_sector_display_labels_are_explicit():
    assert "Professional Services" in SECTOR_DISPLAY_NAMES["IT/Computer Services"]
    assert "Repair Services" in SECTOR_DISPLAY_NAMES["Skilled Trades"]


def _laus_series(county_fips: str, measure_code: str, value: str) -> dict:
    """Minimal BLS-shaped annual-average series for one county measure."""
    return {
        "seriesID": f"LAUCN20{county_fips}00000000{measure_code}",
        "data": [{"year": "2025", "period": "M13", "value": value}],
    }


def test_laus_parse_merges_county_whose_measures_straddle_a_batch_boundary():
    """
    A county contributes four series and the BLS request batch size is not a
    multiple of four, so a county's measures routinely land in two different
    batches. The accumulator must therefore be shared across batches.

    Regression test for the defect found 2026-09-04: _parse_series was called
    once per batch and the partial dicts concatenated, publishing two
    half-populated rows per straddling county -- labor_force/employed on one,
    unemployed/unemployment_rate on the other -- in every year 2015-2025 across
    all five deployed states.
    """
    batch_a = [
        _laus_series("177", "06", "93752"),   # labor_force
        _laus_series("177", "05", "90149"),   # employed
    ]
    batch_b = [
        _laus_series("177", "04", "3603"),    # unemployed
        _laus_series("177", "03", "3.8"),     # unemployment_rate
    ]

    rows: dict = {}
    _parse_series(batch_a, "20", rows)
    _parse_series(batch_b, "20", rows)

    assert len(rows) == 1, "straddling county must collapse to a single row"
    row = rows[("177", 2025)]
    assert row["labor_force"] == 93752
    assert row["employed"] == 90149
    assert row["unemployed"] == 3603
    assert row["unemployment_rate"] == 3.8
    # Every measure present: a half-populated row is the defect's signature.
    assert not set(MEASURES.values()) - set(row)


def test_laus_grain_validator_flags_duplicate_county_years(tmp_path):
    """The validator must fail on a duplicated (county_fips, year)."""
    pd.DataFrame(
        {
            "state_fips": ["20", "20", "20"],
            "county_fips": ["177", "177", "173"],
            "year": [2025, 2025, 2025],
            "labor_force": [93752, None, 277374],
            "unemployment_rate": [None, 3.8, 4.1],
        }
    ).to_parquet(tmp_path / "laus_s20.parquet")

    failures = _failures_for_laus_grain(tmp_path, state="20")

    assert len(failures) == 1
    assert "177" in failures[0]
    # The clean county must not be implicated.
    assert "173" not in failures[0]


def test_laus_grain_validator_passes_on_unique_county_years(tmp_path):
    pd.DataFrame(
        {
            "state_fips": ["20", "20"],
            "county_fips": ["177", "173"],
            "year": [2025, 2025],
            "labor_force": [93752, 277374],
            "unemployment_rate": [3.8, 4.1],
        }
    ).to_parquet(tmp_path / "laus_s20.parquet")

    assert _failures_for_laus_grain(tmp_path, state="20") == []
