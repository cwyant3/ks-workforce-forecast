import math

import numpy as np
import pandas as pd

from cohort_model import ANNUAL_SURVIVAL, MODEL_COHORTS, WORKFORCE_GROUPS, CountyCohortModel
import pytest
from pathlib import Path

from fetch_acs import B23001_18_64_WEIGHTS, B23001_VARS, _add_acs_metadata, _add_labor_force_status
from fetch_ksde import apply_ksde_override
from fetch_laus import MEASURES, _parse_series
from fetch_qcew import SECTOR_DISPLAY_NAMES
from participation_model import build_participation_table
from run_forecast import _build_state_aggregate
from scripts.parse_manual_ks_occproj import load_demand_flags, select_demand_workbooks
from scripts.parse_manual_ssa import data_year_from_title, parse_state
from scripts.validate_outputs import _failures_for_ks_demand_flags, _failures_for_laus_grain


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
    # Every B23001 variable must be present (a partial set now raises — see
    # test_acs_labor_force_status_raises_on_partial_b23001_schema); the bands
    # not exercised here are supplied as zeros so the intended sums hold.
    fixture = {v: [0] for v in B23001_VARS}
    fixture.update(
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
    df = pd.DataFrame(fixture)

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


# ── ACS B23001 partial-schema guard (2026-09-15) ─────────────────────────────

def test_acs_labor_force_status_noops_when_no_b23001_columns():
    """Legacy cached frames predate B23001 entirely and must still load."""
    df = pd.DataFrame({"county_fips": ["173"], "total_pop": [500000]})
    out = _add_labor_force_status(df)
    assert "acs_lfpr_pct" not in out.columns
    assert out.equals(df)


def test_acs_labor_force_status_raises_on_partial_b23001_schema():
    """
    _weighted_sum skips absent variables silently, so a frame carrying SOME of
    B23001_VARS would publish an under-counted LFPR with no error. That is the
    shape a Census schema change would take. Some-but-not-all must raise.
    """
    cols = {v: [10] for v in B23001_VARS}
    del cols["B23001_006E"]              # drop one civilian-LF variable
    with pytest.raises(ValueError, match="B23001_006E"):
        _add_labor_force_status(pd.DataFrame(cols))


def test_acs_labor_force_status_accepts_complete_b23001_schema():
    armed = {v for g in B23001_18_64_WEIGHTS.values() for v in g["armed_forces"]}
    cols = {v: [0 if v in armed else 10] for v in B23001_VARS}
    out = _add_labor_force_status(pd.DataFrame(cols))
    assert "acs_lfpr_pct" in out.columns
    assert out.loc[0, "acs_armed_forces_18_64"] == 0


# ── KDOL demand-book two-file split (2026-09-15) ─────────────────────────────

def _write_demand_book(path, sheets, layout="2025", titles=None):
    """sheets: {sheet_name: [(soc, high_demand_yes_no, rank), ...]}.

    Mirrors KDOL's layout: row 0 title, row 1 subtitle, row 2 header, data from
    row 3. layout="2025" uses the "SOC" / "SOC Title" headers of the combined
    book; layout="2026" uses the "Occupation Code" / "Occupation Title" headers
    the 2026 statewide book introduced. `titles` overrides the row-0 title per
    sheet (the 2026 book names its sheet "Occupational Employment Demand" and
    puts "Kansas - 2026 ..." in the title row instead).
    """
    code_h, title_h = (("SOC", "SOC Title") if layout == "2025"
                       else ("Occupation \nCode", "Occupation Title"))
    titles = titles or {}
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, rows in sheets.items():
            body = [[r, soc, f"Title {soc}", hd, "No", "No"] for (soc, hd, r) in rows]
            title = titles.get(name, f"{name} - Occupational Employment Demand")
            frame = pd.DataFrame(
                [[title, None, None, None, None, None],
                 ["Indicators for High Demand, Emerging Demand, High Wage", None, None, None, None, None],
                 ["Rank", code_h, title_h, "High\nDemand", "Emerging\nDemand", "High\nWage"]]
                + body
            )
            frame.to_excel(xw, sheet_name=name, header=False, index=False)


def test_demand_flags_from_legacy_combined_book(tmp_path):
    combined = tmp_path / "2025 Occupational Employment Demand (Kansas and Regions).xlsx"
    _write_demand_book(combined, {
        "Kansas":    [("29-1141", "Yes", 1), ("47-2111", "No", 2), ("51-3022", "No", 3)],
        "Southwest": [("51-3022", "Yes", 1), ("29-1141", "No", 2)],
        "Northeast": [("47-2111", "Yes", 1)],
    })
    flags, vintage = load_demand_flags(combined)
    assert vintage == "2025"
    by_soc = flags.set_index("soc_code")
    assert int(by_soc.loc["29-1141", "in_demand"]) == 1
    assert int(by_soc.loc["47-2111", "in_demand"]) == 0
    assert int(by_soc.loc["51-3022", "regional_in_demand"]) == 1
    assert int(by_soc.loc["47-2111", "regional_in_demand"]) == 1
    assert int(by_soc.loc["29-1141", "regional_in_demand"]) == 0


def test_demand_flags_from_split_statewide_and_regional_books(tmp_path):
    statewide = tmp_path / "2026 Occupational Employment Demand (Kansas).xlsx"
    regional  = tmp_path / "2025 Occupational Employment Demand (Kansas Regions).xlsx"
    _write_demand_book(statewide, {
        "Kansas": [("29-1141", "Yes", 1), ("51-3022", "No", 2)],
    })
    _write_demand_book(regional, {
        "Southwest": [("51-3022", "Yes", 1)],
        "Northeast": [("29-1141", "No", 1)],
    })
    flags, vintage = load_demand_flags(statewide, regional)
    assert vintage == "2026"
    by_soc = flags.set_index("soc_code")
    assert int(by_soc.loc["29-1141", "in_demand"]) == 1
    assert int(by_soc.loc["51-3022", "regional_in_demand"]) == 1
    assert int(by_soc["regional_in_demand"].sum()) == 1


def test_demand_flags_read_the_real_2026_statewide_layout(tmp_path):
    """The 2026 book is not just split: its sheet is 'Occupational Employment
    Demand' with 'Kansas - 2026 ...' in the title row, and the SOC column is
    headed 'Occupation Code'. Found live on 2026-09-15 when the guard refused
    the file. Paired with the legacy combined book as the regional source."""
    statewide = tmp_path / "2026 Occupational Employment Demand (Kansas).xlsx"
    combined  = tmp_path / "2025 Occupational Employment Demand (Kansas and Regions).xlsx"
    _write_demand_book(
        statewide,
        {"About the Data": [], "Occupational Employment Demand": [("35-3023", "Yes", 1), ("51-3022", "No", 2)]},
        layout="2026",
        titles={"Occupational Employment Demand": "Kansas - 2026 Occupational Employment Demand",
                "About the Data": "Occupational Employment Demand - Technical Notes"},
    )
    _write_demand_book(combined, {
        "Kansas":    [("35-3023", "No", 1), ("51-3022", "Yes", 2)],
        "Southwest": [("51-3022", "Yes", 1)],
    })
    flags, vintage = load_demand_flags(statewide, combined)
    by_soc = flags.set_index("soc_code")
    assert vintage == "2026"
    assert int(by_soc.loc["35-3023", "in_demand"]) == 1
    assert int(by_soc.loc["51-3022", "in_demand"]) == 0
    assert int(by_soc.loc["51-3022", "regional_in_demand"]) == 1


def test_demand_flags_regional_book_with_2026_headers(tmp_path):
    """A future '(Kansas Regions)' book will most likely carry the 2026 headers."""
    statewide = tmp_path / "2026 Occupational Employment Demand (Kansas).xlsx"
    regional  = tmp_path / "2026 Occupational Employment Demand (Kansas Regions).xlsx"
    _write_demand_book(statewide, {"Kansas": [("35-3023", "Yes", 1), ("51-3022", "No", 2)]})
    _write_demand_book(regional, {
        "Map - Kansas Regions": [],
        "Southwest": [("51-3022", "Yes", 1)],
        "Northeast": [("35-3023", "No", 1)],
    }, layout="2026")
    flags, _ = load_demand_flags(statewide, regional)
    by_soc = flags.set_index("soc_code")
    assert int(by_soc.loc["51-3022", "regional_in_demand"]) == 1
    assert int(by_soc["regional_in_demand"].sum()) == 1


def test_demand_flags_statewide_only_book_refuses_to_zero_regional(tmp_path):
    """The 2026-09-15 finding: 384 regional flags -> 0 with no error. Now an error."""
    statewide = tmp_path / "2026 Occupational Employment Demand (Kansas).xlsx"
    _write_demand_book(statewide, {"Kansas": [("29-1141", "Yes", 1)]})
    with pytest.raises(RuntimeError, match="Kansas Regions"):
        load_demand_flags(statewide)
    flags, _ = load_demand_flags(statewide, allow_no_regional=True)
    assert int(flags["regional_in_demand"].sum()) == 0


def test_demand_flags_regional_book_ignores_its_own_kansas_sheet(tmp_path):
    """The legacy combined book may serve as the regional source beside a newer
    statewide book; its Kansas sheet must not override the newer statewide flags."""
    statewide = tmp_path / "2026 Occupational Employment Demand (Kansas).xlsx"
    combined  = tmp_path / "2025 Occupational Employment Demand (Kansas and Regions).xlsx"
    _write_demand_book(statewide, {"Kansas": [("29-1141", "Yes", 1), ("51-3022", "No", 2)]})
    _write_demand_book(combined, {
        "Kansas":    [("29-1141", "No", 1), ("51-3022", "Yes", 2)],   # stale, must be ignored
        "Southwest": [("51-3022", "Yes", 1)],
    })
    flags, vintage = load_demand_flags(statewide, combined)
    by_soc = flags.set_index("soc_code")
    assert vintage == "2026"
    assert int(by_soc.loc["29-1141", "in_demand"]) == 1
    assert int(by_soc.loc["51-3022", "in_demand"]) == 0
    assert int(by_soc.loc["51-3022", "regional_in_demand"]) == 1


def test_select_demand_workbooks_pairs_newest_statewide_with_newest_regional():
    files = [Path(n) for n in (
        "2025 Occupational Employment Demand (Kansas and Regions).xlsx",
        "2026 Occupational Employment Demand (Kansas).xlsx",
        "2025 Occupational Employment Demand (Kansas Regions).xlsx",
    )]
    sw, rg = select_demand_workbooks(files)
    assert sw.name.startswith("2026") and "(Kansas)" in sw.name
    assert rg.name == "2025 Occupational Employment Demand (Kansas Regions).xlsx"


def test_select_demand_workbooks_legacy_combined_book_alone_is_single_file():
    files = [Path("2025 Occupational Employment Demand (Kansas and Regions).xlsx")]
    sw, rg = select_demand_workbooks(files)
    assert sw == files[0]
    assert rg is None


def test_select_demand_workbooks_newer_statewide_falls_back_to_combined_regions():
    """Until the split regional book is downloaded, the combined 2025 book is the
    only regional source and must be paired with the 2026 statewide book."""
    files = [Path(n) for n in (
        "2025 Occupational Employment Demand (Kansas and Regions).xlsx",
        "2026 Occupational Employment Demand (Kansas).xlsx",
    )]
    sw, rg = select_demand_workbooks(files)
    assert sw.name.startswith("2026")
    assert rg.name.startswith("2025") and "Regions" in rg.name


def test_demand_flags_validator_fails_when_regional_flags_are_all_zero(tmp_path):
    pd.DataFrame({
        "soc_code": ["29-1141", "51-3022"],
        "in_demand": [1, 0],
        "regional_in_demand": [0, 0],
        "demand_rank": [1, 2],
    }).to_parquet(tmp_path / "ks_occ_proj_state_s20.parquet")
    failures = _failures_for_ks_demand_flags(tmp_path, state="20")
    assert len(failures) == 1
    assert "regional_in_demand" in failures[0]


def test_demand_flags_validator_passes_when_populated_and_skips_other_states(tmp_path):
    pd.DataFrame({
        "soc_code": ["29-1141", "51-3022"],
        "in_demand": [1, 0],
        "regional_in_demand": [0, 1],
        "demand_rank": [1, 2],
    }).to_parquet(tmp_path / "ks_occ_proj_state_s20.parquet")
    assert _failures_for_ks_demand_flags(tmp_path, state="20") == []
    assert _failures_for_ks_demand_flags(tmp_path, state="08") == []
    assert _failures_for_ks_demand_flags(tmp_path / "nowhere", state="20") == []


# ── SSA edition year (2026-09-15) ────────────────────────────────────────────

def _write_ssa_book(path, sheet_title_year, state="Kansas", fips="20"):
    """Minimal Table 4 sheet in SSA's layout: title rows, headers, state total,
    then county rows with the ANSI code in col 2 and disabled workers in col 9.
    SSA uses a non-breaking space between 'December' and the year."""
    title = f"Table 4.\nNumber of beneficiaries ... aged 65 or older, December\u00a0{sheet_title_year}"
    header = ["County", None, "ANSI code", "Total", "Retirement", None, None, None,
              None, "Disabled workers", "Spouses", "Children"]
    rows = [
        [state, None, None, None, None, None, None, None, None, None, None, None],
        [title, None, None, None, None, None, None, None, None, None, None, None],
        header, header,
        [f"Total, {state}", None, fips, "1000", None, None, None, None, None, "100", None, None],
        ["Allen", None, f"{fips}001", "500", None, None, None, None, None, "40", None, None],
        ["Sedgwick", None, f"{fips}173", "500", None, None, None, None, None, "60", None, None],
    ]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name=f"Table 4 - {state}",
                                    header=False, index=False)


def test_ssa_data_year_is_read_from_the_december_title():
    raw = pd.DataFrame([["Kansas"], ["Table 4. ... December\u00a02025"], ["County"]])
    assert data_year_from_title(raw) == 2025
    assert data_year_from_title(pd.DataFrame([["no year here"]])) is None


def test_ssa_parse_stamps_the_edition_year_not_the_year_before(tmp_path):
    """oasdi_sc25 reports December 2025. Until 2026-09-15 it was stamped 2024."""
    book = tmp_path / "oasdi_sc25.xlsx"
    _write_ssa_book(book, 2025)
    df = parse_state(book, "20", pub_year=2025)
    assert sorted(df["county_fips"]) == ["001", "173"]
    assert set(df["year"]) == {2025}
    assert int(df.loc[df["county_fips"] == "173", "ssdi_18_64"].iloc[0]) == 60


def test_ssa_parse_refuses_a_workbook_whose_title_disagrees_with_its_name(tmp_path):
    book = tmp_path / "oasdi_sc25.xlsx"
    _write_ssa_book(book, 2024)
    with pytest.raises(ValueError, match="December 2024"):
        parse_state(book, "20", pub_year=2025)

