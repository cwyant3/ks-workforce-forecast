"""Regression tests for participation-layer data transformations."""

from __future__ import annotations

import pandas as pd
import pytest

from participation_model import (
    build_participation_table,
    participation_summary,
    project_effective_workforce,
)


def _acs_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "state_fips": ["20", "20"],
            "county_fips": ["001", "001"],
            "year": [2023, 2024],
            "acs_period_midpoint_year": [2021, 2022],
            "pop_working_age": [1000, 1100],
            "acs_lfpr_pct": [70.0, 70.0],
        }
    )


def _ssa_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "county_fips": ["001", "001", "001"],
            "year": [2021, 2022, 2023],
            "ssdi_18_64": [80, 90, 100],
            "ssi_18_64": [20, 20, 20],
            "total_disabled_18_64": [100, 110, 120],
        }
    )


def test_ssa_alignment_is_one_to_one_and_uses_acs_period_midpoint() -> None:
    part = build_participation_table(_acs_fixture(), ssa_df=_ssa_fixture())

    assert len(part) == 2
    assert not part.duplicated(["county_fips", "year"]).any()

    by_year = part.set_index("year")
    assert by_year.loc[2023, "ssa_source_year"] == 2021
    assert by_year.loc[2024, "ssa_source_year"] == 2022
    assert by_year.loc[2024, "total_disabled_18_64"] == 110
    assert by_year.loc[2024, "disability_adjusted_pop"] == 990
    assert by_year.loc[2024, "disability_rate_pct"] == 10.0
    assert by_year.loc[2024, "effective_labor_force"] == 693


def test_baseline_only_still_uses_period_aligned_ssa_record() -> None:
    part = build_participation_table(
        _acs_fixture(),
        ssa_df=_ssa_fixture(),
        baseline_year_only=True,
    )

    assert len(part) == 1
    assert part.loc[0, "year"] == 2024
    assert part.loc[0, "ssa_source_year"] == 2022
    assert part.loc[0, "disability_adjusted_pop"] == 990


def test_participation_summary_rejects_duplicate_latest_counties() -> None:
    duplicate = pd.DataFrame(
        {
            "county_fips": ["001", "001"],
            "year": [2024, 2024],
            "working_age_pop": [1000, 1000],
            "effective_labor_force": [700, 710],
        }
    )

    with pytest.raises(ValueError, match="duplicate county rows"):
        participation_summary(duplicate)


def test_effective_projection_uses_latest_unique_county_factor() -> None:
    part = build_participation_table(_acs_fixture(), ssa_df=_ssa_fixture())
    projection = pd.DataFrame(
        {
            "county_fips": ["001"],
            "year": [2026],
            "p25": [900.0],
            "p50": [1000.0],
            "p75": [1100.0],
            "p90": [1200.0],
            "mean": [1010.0],
        }
    )

    result = project_effective_workforce(part, projection)

    assert result.loc[0, "participation_adj_factor"] == pytest.approx(0.63)
    assert result.loc[0, "eff_p50"] == 630
