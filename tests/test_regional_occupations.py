import pandas as pd
import pytest

from data_contracts import validate_observations
from fetch_oes import area_series_ids, parse_area_api_response
from scripts.parse_manual_ks_occproj import (build_regional_context, load_demand_flags,
                                             load_regional_demand_flags, parse_regional_published)


def write_demand(path):
    with pd.ExcelWriter(path) as book:
        for region, rows in {"Kansas": [("47-2111", "Yes"), ("51-4121", "No")],
                             "South Central": [("47-2111", "No"), ("51-4121", None)],
                             "Southwest": [("47-2111", "Yes"), ("51-4121", "Yes")]}.items():
            data = [[region, None, None], [None, None, None], ["SOC", "High Demand", "Rank"]]
            data.extend([[soc, flag, 1] for soc, flag in rows])
            pd.DataFrame(data).to_excel(book, sheet_name=region, header=False, index=False)


def test_regional_flag_is_never_a_union_or_missing_false(tmp_path):
    path = tmp_path / "2025 Occupational Employment Demand (Kansas and Regions).xlsx"
    write_demand(path)
    flags, _ = load_demand_flags(path)
    assert flags.regional_in_demand.isna().all()
    local, _ = load_demand_flags(path, region_id="KS-SC")
    local = local.set_index("soc_code")
    assert local.loc["47-2111", "regional_in_demand"] == 0
    assert pd.isna(local.loc["51-4121", "regional_in_demand"])
    relation = load_regional_demand_flags(path)
    assert len(relation) == 4
    assert not relation.duplicated(["region_id", "soc_code"]).any()


def regional_workbook(path, annual_header="Openings: Annual"):
    data = [[None] * 22 for _ in range(7)]
    data[0][0] = "South Central Projection Region"
    data[1][0] = "Long-term Occupational Projections"
    data[2][0] = "2022 to 2032"
    for pos, text in {2: "Base Year 2022", 3: "Projected Year 2032", 8: "Exits: Annual",
                      10: "Transfers: Annual", 12: "Numerical Change: Annual", 14: annual_header}.items():
        data[4][pos] = text
    data[5] = ["47-2111", "Electricians", "1000", "1100", "100", ".1", ".01", "100", "10",
               "200", "20", "100", "10", "400", "40", "60000", None, "58000", None, None, None, None]
    data[6] = ["51-4121", "Welders", "***", "***", None, None, None, None, "***",
               None, "***", None, "***", None, "***", None, None, None, None, None, None, None]
    with pd.ExcelWriter(path) as book:
        pd.DataFrame(data).to_excel(book, sheet_name="SC", header=False, index=False)


def test_regional_annual_columns_and_suppression_survive(tmp_path):
    path = tmp_path / "regional.xlsx"
    regional_workbook(path)
    raw = parse_regional_published(path)
    assert raw.iloc[0].annual_openings == 40  # not the ten-year total 400
    assert raw.iloc[0].annual_exits == 10
    assert raw.iloc[1].annual_openings_status == "suppressed"
    observations = build_regional_context(path)
    assert validate_observations(observations) == []
    assert observations.geography_id.eq("KS-SC").all()
    assert observations[observations.availability_status == "suppressed"].estimate.isna().all()


def test_regional_changed_header_fails_instead_of_mixing_totals(tmp_path):
    path = tmp_path / "regional.xlsx"
    regional_workbook(path, annual_header="Openings Total")
    with pytest.raises(ValueError, match="annual/decade"):
        parse_regional_published(path)


def test_oews_missing_and_uncertainty_fields_are_not_imputed():
    ids = area_series_ids("48620", ["47-2111", "51-4121"])
    values = {"01": "1610", "02": "5.2", "04": "68250", "05": "1.7", "13": "65670"}
    payload = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": sid, "data": [{"year": "2025", "period": "A01", "value": values[sid[-2:]], "footnotes": [{}]}]}
        for sid in ids[:5]]}}
    frame = parse_area_api_response(payload, ids)
    assert validate_observations(frame) == []
    assert frame.geography_id.eq("48620").all()
    electricians = frame[frame.occupation_code == "47-2111"].set_index("measure_id")
    assert electricians.loc["oews_employment", "rse"] == 5.2
    assert electricians.loc["oews_mean_annual_wage", "rse"] == 1.7
    assert pd.isna(electricians.loc["oews_median_annual_wage", "rse"])
    assert frame[frame.occupation_code == "51-4121"].availability_status.eq("not_reported").all()


def test_oews_national_type_cannot_be_called_nonmetro():
    with pytest.raises(ValueError, match="national"):
        area_series_ids("48620", ["47-2111"], "N")
