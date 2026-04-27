import math

import numpy as np
import pandas as pd

from cohort_model import ANNUAL_SURVIVAL, CountyCohortModel
from fetch_acs import _add_acs_metadata
from fetch_qcew import SECTOR_DISPLAY_NAMES
from run_forecast import _build_state_aggregate


def test_acs_metadata_adds_state_and_period_fields():
    df = _add_acs_metadata(pd.DataFrame({"county_fips": ["001"], "year": [2023]}), 2023, "8")

    assert df.loc[0, "state_fips"] == "08"
    assert df.loc[0, "acs_period_start_year"] == 2019
    assert df.loc[0, "acs_period_end_year"] == 2023
    assert df.loc[0, "acs_period_midpoint_year"] == 2021
    assert df.loc[0, "estimate_type"] == "ACS 5-year"


def test_migration_estimator_skips_heavily_overlapping_acs_vintages():
    history = [
        {"year": 2015, "pop_working_age": 1000, "acs_period_start_year": 2011, "acs_period_end_year": 2015},
        {"year": 2019, "pop_working_age": 1100, "acs_period_start_year": 2015, "acs_period_end_year": 2019},
        {"year": 2021, "pop_working_age": 2000, "acs_period_start_year": 2017, "acs_period_end_year": 2021},
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
