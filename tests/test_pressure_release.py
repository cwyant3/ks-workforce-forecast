from pathlib import Path
import pandas as pd
import pytest

from data_contracts import normalize_observations, validate_observations, latest_observations
from pressure_release import (stage_release, adopt_release, read_active, resolve_output,
                              output_cache_token, rollback_release)


def observed(value=100, **overrides):
    return normalize_observations(pd.DataFrame([{
        "source_id": "test", "measure_id": "employment", "geography_id": "20173",
        "period_start": "2025-01-01", "period_end": "2025-03-31",
        "estimate": value, "unit": "jobs", **overrides}]))


def test_bad_staging_does_not_replace_good_release(tmp_path):
    outputs = tmp_path / "outputs"
    first = stage_release(outputs, {"pressure_observations.parquet": observed()}, sources=[], scope={})
    adopt_release(outputs, first)
    bad = observed()
    bad.loc[0, "estimate"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        stage_release(outputs, {"pressure_observations.parquet": bad}, sources=[], scope={})
    assert read_active(outputs) == first


def test_adoption_and_rollback_change_same_session_cache_token(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    legacy = outputs / "legacy.txt"
    legacy.write_text("untouched")
    first = stage_release(outputs, {"pressure_observations.parquet": observed()}, sources=[], scope={})
    adopt_release(outputs, first)
    token1 = output_cache_token(outputs)
    second = stage_release(outputs, {"pressure_observations.parquet": observed(200)}, sources=[], scope={})
    adopt_release(outputs, second)
    assert token1 != output_cache_token(outputs)
    assert pd.read_parquet(resolve_output(outputs, "pressure_observations.parquet")).estimate.iloc[0] == 200
    token2 = output_cache_token(outputs)
    rollback_release(outputs, first["release_id"])
    assert token2 != output_cache_token(outputs)
    assert pd.read_parquet(resolve_output(outputs, "pressure_observations.parquet")).estimate.iloc[0] == 100
    assert resolve_output(outputs, "legacy.txt") == legacy
    assert legacy.read_text() == "untouched"
    rollback_release(outputs, "legacy")
    assert read_active(outputs) == {}
    assert legacy.read_text() == "untouched"


def test_tampered_stage_and_stale_adoption_are_rejected(tmp_path):
    outputs = tmp_path / "outputs"
    first = stage_release(outputs, {"pressure_observations.parquet": observed()}, sources=[], scope={})
    competing = stage_release(outputs, {"pressure_observations.parquet": observed(20)}, sources=[], scope={})
    adopt_release(outputs, first)
    with pytest.raises(RuntimeError, match="changed"):
        adopt_release(outputs, competing)
    third = stage_release(outputs, {"pressure_observations.parquet": observed(30)}, sources=[], scope={})
    path = resolve_output(outputs, "pressure_observations.parquet", third)
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Hash"):
        adopt_release(outputs, third)
    assert read_active(outputs)["release_id"] == first["release_id"]


def test_unavailable_and_duplicate_universes():
    suppressed = observed(999, availability_status="suppressed")
    assert pd.isna(suppressed.estimate.iloc[0])
    assert validate_observations(suppressed) == []
    assert "Duplicate observation identity" in validate_observations(pd.concat([observed(), observed()]))
    assert validate_observations(observed(geography_id="173"))


def test_latest_is_per_measure_and_universe_without_suppression_backfill():
    old = observed(100)
    new = observed(999, period_start="2025-04-01", period_end="2025-06-30", availability_status="suppressed")
    frame = pd.concat([old, new], ignore_index=True)
    assert latest_observations(frame).availability_status.iloc[0] == "suppressed"
    assert latest_observations(frame, usable_only=True).period_end.iloc[0] == "2025-03-31"


def test_sector_override_schema_and_qwi_dimensions(tmp_path):
    outputs = tmp_path / "outputs"
    with pytest.raises(ValueError, match="missing required"):
        stage_release(outputs, {"pressure_observations.parquet": observed(),
                      "sector_projections_s20.parquet": pd.DataFrame({"junk": [1]})}, sources=[], scope={})
    frame = pd.concat([observed(seasonal_adjustment="U"), observed(seasonal_adjustment="S")], ignore_index=True)
    assert validate_observations(frame) == []
    assert len(latest_observations(frame)) == 2


def test_adoption_lock_rejects_concurrent_writer(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    release = stage_release(outputs, {"pressure_observations.parquet": observed()}, sources=[], scope={})
    (outputs / ".pressure-adoption.lock").write_text("busy")
    with pytest.raises(RuntimeError, match="holds the lock"):
        adopt_release(outputs, release)
    assert read_active(outputs) == {}
