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
def test_metro_table_builds_from_staged_evidence_without_forecasts():
    table = build_metro_table(EVIDENCE)
    assert set(table.geography_id) == set(METRO_COUNTIES)
    assert set(table.source_id) == {"acs_b23025", "acs_s2301", "laus", "qcew"}
    withheld = table[~table.availability_status.eq("published")]
    assert withheld.estimate.isna().all()
