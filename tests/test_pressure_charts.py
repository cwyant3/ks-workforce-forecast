import pandas as pd
import pytest

from data_contracts import normalize_observations
from dashboard.pressure_charts import current, available, peers, series, comparison_figure, number, confidence_label


def observation(**changes):
    return dict(source_id="qcew",measure_id="jobs",geography_id="20173",
                period_start="2026-01-01",period_end="2026-03-31",estimate=100,
                unit="jobs",industry_code="31-33",ownership="5",**changes)


def test_peer_comparison_never_mixes_periods_or_universes():
    base=observation()
    rows=[base, {**base,"geography_id":"20015","estimate":200},
          {**base,"geography_id":"20035","estimate":300,"ownership":"0"},
          {**base,"geography_id":"20077","estimate":400,"period_end":"2025-03-31","period_start":"2025-01-01"},
          {**base,"geography_id":"20079","estimate":500,"availability_status":"suppressed"},
          {**base,"geography_id":"20095","estimate":600,"seasonal_adjustment":"S"}]
    frame=normalize_observations(pd.DataFrame(rows))
    result=peers(frame,frame.iloc[0])
    assert result.geography_id.tolist()==["20173","20015"]
    assert result.estimate.median()==150


def test_latest_missing_measure_does_not_backfill_a_chart_card():
    base=observation()
    frame=normalize_observations(pd.DataFrame([base,{**base,"period_start":"2026-04-01","period_end":"2026-06-30","availability_status":"suppressed"}]))
    row=current(frame)
    assert row.period_end=="2026-06-30"
    assert not available(row)
    assert number(row.estimate)=="Unavailable"


def test_card_rejects_multiple_universes_and_preserves_zero():
    base=observation()
    frame=normalize_observations(pd.DataFrame([base,{**base,"ownership":"0"}]))
    with pytest.raises(ValueError,match="one geography"):
        current(frame)
    assert number(0)=="0"
    assert number(-2,"percent change")=="-2.0%"


def test_comparison_shows_real_units_and_unweighted_quartiles():
    fig=comparison_figure([10,20,30,40,50],40)
    assert list(fig.data[1].x)==[20,40]
    assert list(fig.data[2].x)==[30]
    assert list(fig.data[3].x)==[40]


def test_industry_filter_cannot_leak_other_industries():
    base=observation()
    frame=normalize_observations(pd.DataFrame([base,{**base,"industry_code":"62"}]))
    assert len(series(frame,"qcew","jobs","20173","31-33"))==1


def test_fractional_and_percent_confidence_levels_render_correctly():
    assert confidence_label(.9)=="90% confidence"
    assert confidence_label(90)=="90% confidence"
