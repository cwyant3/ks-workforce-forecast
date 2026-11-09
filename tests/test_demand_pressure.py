import pandas as pd
import pytest

from data_contracts import validate_observations
from demand_pressure import (complete_group_total, four_quarter_totals,
                             matched_hiring_intensity, quarterly_changes)
from fetch_qcew import _aggregate, _parse_df, parse_quarterly_qcew
from fetch_qwi import fetch_qwi, latest_valid_by_measure, parse_qwi_response
from sector_model import usable_sector_history


def qwi(period="2025-Q4", employment="100", hires="10", sep=None, earnings=None):
    headers = ["Emp", "HirA", "Sep", "EarnS", "sEmp", "sHirA", "sSep", "sEarnS",
               "state", "county", "time", "industry", "ownercode", "sex", "agegrp",
               "seasonadj", "firmsize", "firmage"]
    return [headers, [employment, hires, sep, earnings, "1", "1", "-1" if sep is None else "1",
                      "-1" if earnings is None else "1", "20", "173", period, "31-33", "A05",
                      "0", "A00", "U", "0", "0"]]


def test_qwi_latest_is_per_measure_and_missing_counties_survive():
    frames = [parse_qwi_response(qwi("2025-Q3", sep="9", earnings="5000"), ["20173", "20071"], period="2025-Q3"),
              parse_qwi_response(qwi(), ["20173", "20071"], period="2025-Q4")]
    data = pd.concat(frames, ignore_index=True)
    latest = latest_valid_by_measure(data)
    sedgwick = latest[latest.geography_id == "20173"].set_index("measure_id")
    assert sedgwick.loc["qwi_employment", "reference_label"] == "2025-Q4"
    assert sedgwick.loc["qwi_separations", "reference_label"] == "2025-Q3"
    assert sedgwick.loc["qwi_stable_earnings", "reference_label"] == "2025-Q3"
    assert latest[latest.geography_id == "20071"].availability_status.eq("not_reported").all()
    assert data[data.availability_status != "published"].estimate.isna().all()
    assert validate_observations(data) == []


def test_qwi_suppressed_number_is_never_exposed():
    payload = qwi()
    payload[1][4] = "5"
    row = parse_qwi_response(payload, ["20173"], period="2025-Q4").iloc[0]
    assert row.availability_status == "suppressed"
    assert pd.isna(row.estimate)
    assert row.source_status == "5"


def test_qwi_rejects_dimension_change_and_duplicate_county():
    payload = qwi()
    payload[1][11] = "62"
    with pytest.raises(ValueError, match="dimension"):
        parse_qwi_response(payload, ["20173"], period="2025-Q4")
    payload = qwi()
    payload.append(payload[1])
    with pytest.raises(ValueError, match="multiple"):
        parse_qwi_response(payload, ["20173"], period="2025-Q4")


def test_census_http200_html_failure_does_not_leak_key():
    class Response:
        status_code = 200
        def json(self):
            raise ValueError("secret-key URL returned HTML")
    class Session:
        def get(self, *args, **kwargs):
            return Response()
    with pytest.raises(RuntimeError) as error:
        fetch_qwi(["20173"], period="2025-Q4", api_key="secret-key", session=Session())
    assert "secret-key" not in str(error.value)


def test_matched_quarters_not_row_offsets_and_full_window_required():
    frames = [parse_qwi_response(qwi(period, employment=emp), ["20173"], period=period)
              for period, emp in [("2024-Q4", "100"), ("2025-Q2", "110"), ("2025-Q3", "115"), ("2025-Q4", "120")]]
    data = pd.concat(frames, ignore_index=True)
    growth = quarterly_changes(data)
    row = growth[(growth.measure_id == "qwi_employment_yoy_pct") & (growth.reference_label == "2025-Q4")].iloc[0]
    assert row.estimate == pytest.approx(20)
    # Four rows span five quarters; Q1 is absent and cannot be silently skipped.
    annual = four_quarter_totals(data)
    assert annual.estimate.isna().all()
    last = annual[annual.period_end == "2025-12-31"].iloc[0]
    assert last.reference_label == "2025Q1 through 2025Q4 (four quarters)"
    hires = matched_hiring_intensity(data)
    assert hires.iloc[0].estimate == 10


def annual_row(code, employment, suppressed=False, wage=100):
    return dict(area_fips="20173", year=2025, naics=code, sector="Skilled Trades", employment=employment,
                avg_annual_pay=wage, avg_weekly_wage=2, suppressed=suppressed)


def test_annual_sector_requires_every_component_without_suppression():
    rows = [annual_row("22", 100), annual_row("23", 200), annual_row("81", None, True)]
    sectors, _ = _aggregate(rows, True)
    trades = sectors[sectors.sector == "Skilled Trades"].iloc[0]
    assert pd.isna(trades.employment)
    assert trades.availability_status == "suppressed"
    rows = rows[:2]
    sectors, _ = _aggregate(rows, True)
    trades = sectors[sectors.sector == "Skilled Trades"].iloc[0]
    assert trades.availability_status == "incomplete"
    assert trades.available_components == 2
    assert pd.isna(trades.employment)


def test_complete_sector_does_not_fill_missing_wage_with_zero():
    rows = [annual_row("22", 100, wage=None), annual_row("23", 200), annual_row("81", 0)]
    sectors, _ = _aggregate(rows, True)
    trades = sectors[sectors.sector == "Skilled Trades"].iloc[0]
    assert trades.employment == 300
    assert pd.isna(trades.avg_annual_pay)


def test_annual_source_blank_is_missing_and_cached_suppression_is_masked():
    frame = pd.DataFrame([dict(own_code="5", industry_code="62", size_code="0", disclosure_code="",
                               annual_avg_emplvl="", avg_annual_pay="", annual_avg_wkly_wage="")])
    row = _parse_df(frame, "20173", 2025)[0]
    assert pd.isna(row["employment"])
    repaired = usable_sector_history(pd.DataFrame([dict(employment=800, avg_annual_pay=50000,
                                                         suppressed=True)]))
    assert pd.isna(repaired.iloc[0].employment)


def test_quarterly_suppression_and_incomplete_regional_total():
    raw = pd.DataFrame([dict(area_fips="20035", own_code="5", industry_code="62", size_code="0",
                             disclosure_code="N", year="2026", qtr="1", month1_emplvl="0",
                             month2_emplvl="0", month3_emplvl="0", avg_wkly_wage="0", qtrly_estabs="25")])
    data = parse_quarterly_qcew(raw, county_fips="20035", year=2026, quarter=1, industries=["62", "23"])
    assert data[data.industry_code == "62"].availability_status.eq("suppressed").all()
    assert data[data.industry_code == "23"].availability_status.eq("not_reported").all()
    assert data.estimate.isna().all()
    assert validate_observations(data) == []
    subset = data[(data.measure_id == "qcew_employment") & (data.industry_code == "62")]
    assert complete_group_total(subset, ["20035", "20173"]) == (None, "incomplete")


def test_suppressed_and_duplicate_annual_total_are_never_summed():
    row = {**annual_row("10", 900, True), "sector": None}
    _, totals = _aggregate([row], True)
    assert totals.total_employment.isna().all()
    with pytest.raises(ValueError, match="Duplicate"):
        _aggregate([row, row], True)


def test_offline_sector_rebuild_fails_before_model_for_missing_cache(tmp_path, monkeypatch):
    import fetch_qcew
    from demand_pressure import recompute_sector_from_cache
    outputs, cache = tmp_path / "outputs", tmp_path / "cache"
    outputs.mkdir()
    cache.mkdir()
    pd.DataFrame([dict(county_fips="173", year=2030, p50=200)]).to_parquet(outputs / "projections_s20.parquet")
    monkeypatch.setattr(fetch_qcew, "QCEW_YEARS", [2024, 2025])
    with pytest.raises(FileNotFoundError, match="s20_2024.parquet"):
        recompute_sector_from_cache(outputs, cache)
    assert list(cache.iterdir()) == []
