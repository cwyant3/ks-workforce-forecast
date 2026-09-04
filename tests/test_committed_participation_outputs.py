"""Integrity checks for committed participation-model dashboard outputs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "outputs"
PARTICIPATION_FILES = sorted(OUTPUT_DIR.glob("participation_s*.parquet"))


@pytest.mark.parametrize("path", PARTICIPATION_FILES, ids=lambda path: path.stem)
def test_committed_participation_output_is_structurally_consistent(path: Path) -> None:
    frame = pd.read_parquet(path)
    required = {
        "county_fips",
        "year",
        "working_age_pop",
        "disability_adjusted_pop",
        "effective_labor_force",
    }
    assert required.issubset(frame.columns), (
        f"{path.name} is missing {sorted(required - set(frame.columns))}"
    )

    normalized = frame.copy()
    normalized["county_fips"] = normalized["county_fips"].astype(str).str.zfill(3)
    normalized["year"] = pd.to_numeric(normalized["year"], errors="raise").astype(int)

    duplicate_keys = normalized.duplicated(["county_fips", "year"], keep=False)
    assert not duplicate_keys.any(), (
        f"{path.name} contains duplicate county-year records: "
        f"{normalized.loc[duplicate_keys, ['county_fips', 'year']].drop_duplicates().head(10).to_dict('records')}"
    )

    latest = normalized[normalized["year"] == normalized["year"].max()]
    assert latest["county_fips"].is_unique, (
        f"{path.name} contains more than one latest-year record for a county"
    )

    working_age = pd.to_numeric(normalized["working_age_pop"], errors="coerce")
    disability_adjusted = pd.to_numeric(
        normalized["disability_adjusted_pop"], errors="coerce"
    )
    effective = pd.to_numeric(normalized["effective_labor_force"], errors="coerce")

    assert (working_age.dropna() >= 0).all()
    assert (disability_adjusted.dropna() >= 0).all()
    assert (effective.dropna() >= 0).all()
    assert (disability_adjusted.dropna() <= working_age[disability_adjusted.notna()] + 1).all()
    assert (effective.dropna() <= disability_adjusted[effective.notna()] + 1).all()


def test_repository_commits_participation_outputs_for_dashboard_states() -> None:
    assert len(PARTICIPATION_FILES) == 5
