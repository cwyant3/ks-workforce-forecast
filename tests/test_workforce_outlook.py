import pandas as pd

from data_contracts import normalize_observations
from dashboard.workforce_outlook import current_reading
from dashboard.outlook_forecast import outlook_summary


def growth(**changes):
    return dict(source_id="qcew", measure_id="qcew_employment_yoy_pct",
                geography_id="20173", industry_code="31-33", ownership="5",
                period_start="2026-01-01", period_end="2026-03-31", estimate=2.4,
                unit="percent change", reference_label="2026 Q1", **changes)


def test_narrative_uses_selected_county_and_industry():
    row = growth()
    frame = normalize_observations(pd.DataFrame([
        row, {**row, "geography_id": "20015", "estimate": -5},
        {**row, "industry_code": "62", "estimate": -3},
    ]))
    _, text = current_reading(frame, "20173", "31-33")
    assert "increased by 2.4%" in text
    assert "2026 Q1" in text
    assert "decreased by 3.0%" in current_reading(frame, "20173", "62")[1]
    assert "decreased by 5.0%" in current_reading(frame, "20015", "31-33")[1]


def test_narrative_does_not_infer_current_growth_from_older_data():
    row = growth()
    frame = normalize_observations(pd.DataFrame([
        row, {**row, "period_start": "2026-04-01", "period_end": "2026-06-30",
              "availability_status": "suppressed", "estimate": None},
    ]))
    assert "unavailable" in current_reading(frame, "20173", "31-33")[1]
    assert "increased" not in current_reading(frame, "20173", "31-33")[1]


def test_narrative_preserves_zero_growth():
    row = {**growth(), "estimate": 0}
    frame = normalize_observations(pd.DataFrame([row]))
    assert "was unchanged" in current_reading(frame, "20173", "31-33")[1]


def test_outlook_narrative_compares_only_shared_model_years():
    view = {"participation": pd.DataFrame({"year": [2025, 2026, 2035], "value": [50, 100, 110]}),
            "jobs": pd.DataFrame({"year": [2026, 2035, 2040], "value": [100, 90, 10]}),
            "sector": "Manufacturing"}
    text = outlook_summary(view)
    assert "2026 to 2035" in text
    assert "+10.0%" in text and "-10.0%" in text
    assert "2040" not in text
    view["jobs"] = view["jobs"].iloc[:0]
    assert "requires a compatible" in outlook_summary(view)
