import numpy as np
import pandas as pd
import pytest
from scipy.stats import t

from labor_force_forecast import annual_history, forecast_annual, forecast_county, rolling_checks
from dashboard.labor_force_chart import labor_force_figure


def history_frame():
    changes = np.array([.01, -.02, .03, .01, -.01, .04, .01, -.02, .02, .01])
    return pd.DataFrame({"year": range(2015, 2026),
                         "labor_force": 10000*np.exp(np.r_[0, changes.cumsum()])})


def test_student_t_interval_includes_future_and_drift_uncertainty():
    history = history_frame()
    forecast = forecast_annual(history, 2036)
    log_changes = np.diff(np.log(history.labor_force))
    h = 11
    half = t.ppf(.9, 9)*log_changes.std(ddof=1)*np.sqrt(h+h*h/10)
    location = np.log(history.labor_force.iloc[-1])+h*log_changes.mean()
    last = forecast.iloc[-1]
    assert last["median"] == pytest.approx(np.exp(location))
    assert last.lower80 == pytest.approx(np.exp(location-half))
    assert last.upper80 == pytest.approx(np.exp(location+half))
    assert np.all(np.diff(np.log(forecast.upper80/forecast.lower80)) > 0)


def test_history_selects_county_and_state_without_mixing():
    history = history_frame().assign(state_fips="20", county_fips="173")
    other = history.assign(state_fips="29", labor_force=999)
    result = annual_history(pd.concat([history, other]), "20173")
    pd.testing.assert_frame_equal(result, history[["year", "labor_force"]])
    selected, future = forecast_county(history, "20173", 2026)
    assert future.year.tolist() == list(range(2026, 2037))
    assert len(future[future.year.gt(2026)]) == 10


@pytest.mark.parametrize("defect", ["gap", "duplicate", "zero", "missing", "fractional_year"])
def test_invalid_history_withholds_forecast(defect):
    data = history_frame().assign(state_fips="20", county_fips="173")
    if defect == "gap": data = data.drop(index=3)
    elif defect == "duplicate": data = pd.concat([data, data.iloc[[0]]])
    elif defect == "zero": data.loc[3, "labor_force"] = 0
    elif defect == "missing": data.loc[3, "labor_force"] = np.nan
    else:
        data["year"] = data.year.astype(float)
        data.loc[3, "year"] += .5
    with pytest.raises(ValueError): annual_history(data, "20173")


def test_short_stale_and_zero_variation_histories_are_withheld():
    with pytest.raises(ValueError, match="At least"):
        forecast_annual(history_frame().iloc[:5], 2036)
    with pytest.raises(ValueError, match="variation"):
        forecast_annual(history_frame().assign(labor_force=100), 2036)
    data = history_frame().assign(state_fips="20", county_fips="173")
    with pytest.raises(ValueError, match="too old"):
        forecast_county(data, "20173", 2030)


def test_backtests_disclose_absent_ten_year_evidence():
    checks = rolling_checks(history_frame())
    assert checks['Held-out forecasts'].tolist() == [5, 3, 1, 0]
    assert pd.isna(checks.iloc[-1]['Observed interval coverage (%)'])


def test_chart_does_not_splice_monthly_into_annual_forecast():
    history = history_frame()
    forecast = forecast_annual(history, 2036)
    monthly = pd.Series(dict(availability_status="published", estimate=55555,
                             period_end="2026-08-31"))
    fig = labor_force_figure(history, forecast, monthly)
    projected = next(trace for trace in fig.data if trace.name == "Median annual projection")
    observed_month = next(trace for trace in fig.data if trace.name == "Latest monthly observation")
    assert projected.line.dash == "dot"
    assert projected.y[0] == history.labor_force.iloc[-1]
    assert projected.y[-1] == forecast["median"].iloc[-1]
    assert observed_month.mode == "markers" and list(observed_month.y) == [55555]
