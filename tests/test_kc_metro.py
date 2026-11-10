from pathlib import Path
import pandas as pd
import pytest
from dashboard.pressure_charts import _metro_total
from metro_context import METRO_COUNTIES, MISSOURI_KC_COUNTIES, build_metro_table
from geography import KANSAS_CITY_OEWS_COUNTIES

EVIDENCE = Path(__file__).resolve().parent.parent / "data/pressure_sources/2026-11-10-kc-metro"


def test_metro_is_the_fourteen_counties_of_oews_area_28140():
    assert len(METRO_COUNTIES) == 14 and len(set(METRO_COUNTIES)) == 14
    assert set(KANSAS_CITY_OEWS_COUNTIES) | set(MISSOURI_KC_COUNTIES) == set(METRO_COUNTIES)
    assert all(c.startswith("29") for c in MISSOURI_KC_COUNTIES)


def test_metro_totals_require_every_county_published_in_one_period():
    rows = pd.DataFrame({"estimate": [10.0, 20.0], "availability_status": ["published", "published"],
                         "period_start": ["2026-01-01"] * 2})
    assert _metro_total(rows, 2) == 30
    assert _metro_total(rows, 3) is None
    assert _metro_total(rows.assign(availability_status=["published", "suppressed"]), 2) is None
    assert _metro_total(rows.assign(period_start=["2026-01-01", "2025-01-01"]), 2) is None


@pytest.mark.skipif(not EVIDENCE.exists(), reason="local evidence folder not present")
def test_metro_table_builds_from_staged_evidence():
    table = build_metro_table(EVIDENCE)
    assert set(table.geography_id) == set(METRO_COUNTIES)
    assert set(table.source_id) == {"acs_b23025", "acs_s2301", "laus", "qcew"}
    with_forecasts = build_metro_table(EVIDENCE, EVIDENCE.parent.parent / "outputs")
    fc = with_forecasts[with_forecasts.source_id.eq("cohort_forecast")]
    assert fc.groupby("measure_id").geography_id.nunique().eq(14).all()
    withheld = table[~table.availability_status.eq("published")]
    assert withheld.estimate.isna().all()


def test_forecasts_from_different_runs_are_refused(tmp_path):
    from metro_context import forecast_rows
    for state, seed in (("20", 1), ("29", 2)):
        pd.DataFrame({"county_fips": ["091" if state == "20" else "095"] * 2, "year": [2026, 2035], "p50": [100.0, 110.0],
                      "random_seed": [seed] * 2, "base_year": [2024] * 2}).to_parquet(tmp_path / f"projections_s{state}.parquet")
    with pytest.raises(ValueError, match="random_seed"):
        forecast_rows(tmp_path)
