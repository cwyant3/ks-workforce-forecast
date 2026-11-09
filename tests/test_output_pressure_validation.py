"""Validator regressions for native regional scope and adopted release isolation."""
import json

import pandas as pd
import pytest

from data_contracts import normalize_observations
from pressure_release import stage_release, adopt_release, resolve_output
from scripts.validate_outputs import _failures_for_ks_demand_flags, validate


def statewide():
    return pd.DataFrame({"soc_code": ["29-1141", "51-3022"],
                         "in_demand": [1, 0], "demand_rank": [1, 2]})


@pytest.mark.parametrize("include_missing_column", [False, True])
def test_statewide_does_not_require_union_of_regional_flags(tmp_path, include_missing_column):
    frame = statewide()
    if include_missing_column:
        frame["regional_in_demand"] = pd.NA
    frame.to_parquet(tmp_path / "ks_occ_proj_state_s20.parquet")
    assert _failures_for_ks_demand_flags(tmp_path) == []


@pytest.mark.parametrize("values", [[0, 0], [0, 1]])
def test_regional_values_without_explicit_scope_are_rejected(tmp_path, values):
    frame = statewide().assign(regional_in_demand=values)
    frame.to_parquet(tmp_path / "ks_occ_proj_state_s20.parquet")
    assert "single-region metadata" in " ".join(_failures_for_ks_demand_flags(tmp_path))


def test_explicit_single_region_all_zero_is_valid(tmp_path):
    frame = statewide().assign(regional_in_demand=0, regional_demand_region_id="KS-SC")
    frame.to_parquet(tmp_path / "ks_occ_proj_state_s20.parquet")
    assert _failures_for_ks_demand_flags(tmp_path) == []
    frame.loc[1, "regional_demand_region_id"] = "KS-SE"
    frame.to_parquet(tmp_path / "ks_occ_proj_state_s20.parquet")
    assert "single-region metadata" in " ".join(_failures_for_ks_demand_flags(tmp_path))


def test_invalid_statewide_flag_is_rejected(tmp_path):
    statewide().assign(in_demand=[1, 2]).to_parquet(tmp_path / "ks_occ_proj_state_s20.parquet")
    assert "only 0, 1 or NA" in " ".join(_failures_for_ks_demand_flags(tmp_path))


def test_native_regions_are_independent_and_preserve_missing_flags(tmp_path):
    path = tmp_path / "ks_occ_demand_region_s20.parquet"
    frame = pd.DataFrame({"region_id": ["KS-SC", "KS-SE"], "soc_code": ["29-1141"] * 2,
                          "regional_in_demand": [0, None],
                          "availability_status": ["published", "not_reported"]})
    frame.to_parquet(path)
    assert _failures_for_ks_demand_flags(tmp_path) == []
    pd.concat([frame, frame.iloc[:1]]).to_parquet(path)
    assert "duplicated region_id" in " ".join(_failures_for_ks_demand_flags(tmp_path))
    frame.loc[1, "availability_status"] = "published"
    frame.to_parquet(path)
    assert "disagrees with value" in " ".join(_failures_for_ks_demand_flags(tmp_path))


def test_pressure_validation_ignores_superseded_legacy_files_and_detects_tampering(tmp_path):
    outputs = tmp_path / "outputs"
    observations = normalize_observations(pd.DataFrame([{
        "source_id": "test", "measure_id": "employment", "geography_id": "20173",
        "period_start": "2025-01-01", "period_end": "2025-03-31",
        "estimate": 100, "unit": "jobs"}]))
    manifest = stage_release(outputs, {"pressure_observations.parquet": observations},
                             sources=[], scope={"state": "20"})
    adopt_release(outputs, manifest)
    # These old physical files are not part of the selected release contract.
    (outputs / "ipeds_s20.parquet").write_bytes(b"obsolete corrupt file")
    (outputs / "ks_occ_proj_state_s20.parquet").write_bytes(b"obsolete corrupt file")
    assert validate(outputs, state="20", pressure=True) == []
    assert "requested 08" in " ".join(validate(outputs, state="08", pressure=True))
    resolve_output(outputs, "pressure_observations.parquet").write_bytes(b"tampered")
    assert "Hash mismatch" in " ".join(validate(outputs, pressure=True))


def test_pressure_absent_or_malformed_pointer_is_a_validation_failure(tmp_path):
    assert "no active release" in " ".join(validate(tmp_path, pressure=True))
    pointer = tmp_path / "pressure-current.json"
    pointer.write_text("not json")
    assert "could not validate" in " ".join(validate(tmp_path, pressure=True))
    pointer.write_text(json.dumps({"release_id": "../escape"}))
    assert "Invalid adopted" in " ".join(validate(tmp_path, pressure=True))
