"""Regression checks for source universes, uncertainty and participation migration."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_acs import parse_acs_supply, _sum_moe, _add_labor_force_status, B23001_VARS
from fetch_laus import parse_laus_monthly, fetch_laus_monthly
from participation_model import build_participation_table, project_effective_workforce
from supply_pressure import build_supply_indicators
from data_contracts import validate_observations


def _acs(estimate="64.1", moe="2.9"):
    return [["NAME", "S2301_C02_001E", "S2301_C02_001M", "state", "county"],
            ["Example County, Kansas", estimate, moe, "20", "001"]]


def _series(measure="06", observations=None, county="001"):
    return {"seriesID": f"LAUCN20{county}00000000{measure}", "data": observations or [
        {"year": "2026", "period": "M08", "value": "1,000", "footnotes": [{"code": "P", "text": "Preliminary."}]},
        {"year": "2025", "period": "M13", "value": "999", "footnotes": []}]}


def test_acs_preserves_window_moe_and_exact_subject_universe():
    frame = parse_acs_supply(_acs(), 2024)
    row = frame.iloc[0]
    assert row.geography_id == "20001"
    assert row.period_start == "2020-01-01" and row.period_end == "2024-12-31"
    assert row.estimate == 64.1 and row.moe == 2.9
    assert row.age_group == "16+" and row.moe_confidence_level == .90
    assert row.rse == pytest.approx(2.9 / 1.645 / 64.1 * 100)


@pytest.mark.parametrize("estimate", ["-666666666", "-999999999", None, "null", "101", "NaN"])
def test_acs_sentinel_and_invalid_rate_are_unavailable(estimate):
    row = parse_acs_supply(_acs(estimate), 2024).iloc[0]
    assert pd.isna(row.estimate) and row.availability_status == "not_reported"


def test_acs_zero_is_observed_and_unknown_moe_is_not_zero():
    row = parse_acs_supply(_acs("0", "-555555555"), 2024).iloc[0]
    assert row.estimate == 0 and row.availability_status == "published"
    assert pd.isna(row.moe) and pd.isna(row.rse)


def test_acs_schema_and_duplicate_county_fail_loudly():
    with pytest.raises(ValueError, match="missing fields"):
        parse_acs_supply([["NAME", "county"], ["Example", "001"]], 2024)
    with pytest.raises(ValueError, match="Duplicate"):
        parse_acs_supply(_acs() + [_acs()[1]], 2024)


def test_published_16_plus_civilian_count_not_inferred_from_s2301():
    columns = [f"B23025_{i:03d}{suffix}" for i in range(1, 8) for suffix in ("E", "M")]
    values = [1000, 10, 700, 10, 680, 9, 650, 9, 30, 4, 20, 3, 300, 8]
    payload = [["state", "county"] + columns, ["20", "001"] + values]
    result = parse_acs_supply(payload, 2024, "B23025").set_index("measure_id")
    assert result.loc["civilian_labor_force_16_plus", "estimate"] == 680
    assert result.loc["civilian_labor_force_16_plus", "moe"] == 9
    assert "labor_force_participation_rate_16_plus" not in result.index


def test_sum_moe_preserves_missing_component():
    frame = pd.DataFrame({"a": [3, 3], "b": [4, None]})
    result = _sum_moe(frame, [("a", 1), ("b", 1)])
    assert result.iloc[0] == 5 and pd.isna(result.iloc[1])


def test_b23001_sentinel_does_not_enter_weighted_sum():
    frame = pd.DataFrame([{field: 0 for field in B23001_VARS}])
    frame["B23001_006E"] = -666666666
    result = _add_labor_force_status(frame)
    assert pd.isna(result.acs_civilian_labor_force_18_64.iloc[0])


def test_disability_context_does_not_reduce_acs_labor_force_again():
    acs = pd.DataFrame({"state_fips": ["20"], "county_fips": ["001"], "year": [2024],
                        "pop_working_age": [1000], "acs_lfpr_pct": [70],
                        "acs_civilian_labor_force_18_64": [690]})
    ssa = pd.DataFrame({"county_fips": ["001"], "year": [2024], "disability_adjusted_pop": [800],
                        "disability_rate_pct": [20], "total_disabled_18_64": [200]})
    result = build_participation_table(acs, ssa_df=ssa)
    assert result.effective_labor_force.iloc[0] == 690
    assert result.disability_adjusted_pop.iloc[0] == 800  # retained only for migration/context
    assert result.ssa_role.iloc[0] == "context_only_no_participation_deduction"
    assert "approximate" in result.participation_universe.iloc[0]


def test_missing_participation_does_not_become_full_labor_force():
    acs = pd.DataFrame({"state_fips": ["20"], "county_fips": ["001"], "year": [2024], "pop_working_age": [1000]})
    participation = build_participation_table(acs)
    assert pd.isna(participation.effective_labor_force.iloc[0])
    original = pd.DataFrame({"county_fips": ["001"], "year": [2026], "p50": [1100]})
    result = project_effective_workforce(participation, original)
    assert pd.isna(result.eff_p50.iloc[0])
    assert original.p50.iloc[0] == 1100 and "eff_p50" not in original


def test_monthly_laus_keeps_preliminary_and_excludes_annual_average():
    result = parse_laus_monthly([_series()])
    assert len(result) == 1
    row = result.iloc[0]
    assert row.estimate == 1000 and row.preliminary
    assert row.period_end == "2026-08-31" and "Preliminary." in row.source_status
    assert row.age_group == "16+" and row.location_basis == "residence"


def test_monthly_laus_checks_scope_and_conflicting_duplicates():
    wrong = _series(); wrong["seriesID"] = "LAUCN080010000000006"
    with pytest.raises(ValueError, match="different state"):
        parse_laus_monthly([wrong])
    changed = _series(); changed["data"][0]["value"] = "1001"
    with pytest.raises(ValueError, match="Conflicting"):
        parse_laus_monthly([_series(), changed])


def test_live_monthly_batch_boundary_preserves_all_measure_rows(monkeypatch):
    import fetch_laus
    calls = []
    def post(ids, start, end, key):
        calls.append(ids)
        return [{"seriesID": sid, "data": [{"year": "2026", "period": "M08", "value": "2", "footnotes": []}]} for sid in ids]
    monkeypatch.setattr(fetch_laus, "_post_bls", post)
    monkeypatch.setattr(fetch_laus.time, "sleep", lambda _: None)
    result = fetch_laus_monthly("20", [f"{n:03d}" for n in range(1, 8)], [2026])
    assert [len(batch) for batch in calls] == [25, 3]
    assert len(result) == 28 and result.groupby("geography_id").measure_id.nunique().eq(4).all()


def test_saved_evidence_merges_overlap_without_inventing_missing_counties(tmp_path):
    import json
    (tmp_path / "acs-2024-s2301-ks.json").write_text(json.dumps(_acs()), encoding="utf-8")
    payload = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [_series()]}}
    for name in ["laus-one.json", "laus-two.json"]:
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    result = build_supply_indicators(tmp_path)
    assert len(result) == 2 and set(result.geography_id) == {"20001"}
    assert validate_observations(result) == []
    # Mixed sources with/without numeric uncertainty must survive publication.
    result.to_parquet(tmp_path / "observations.parquet", index=False)
    assert len(pd.read_parquet(tmp_path / "observations.parquet")) == 2


def test_effective_projection_scales_all_interval_bounds_without_mutating_population():
    acs = pd.DataFrame({"state_fips": ["20"], "county_fips": ["001"], "year": [2024],
                        "pop_working_age": [1000], "acs_civilian_labor_force_18_64": [700]})
    part = build_participation_table(acs)
    original = pd.DataFrame({"county_fips": ["001"], "year": [2026], "p10": [1000], "p50": [1100], "p90": [1200]})
    projected = project_effective_workforce(part, original)
    assert list(projected[["eff_p10", "eff_p50", "eff_p90"]].iloc[0]) == [700, 770, 840]
    assert original.equals(projected[original.columns])
