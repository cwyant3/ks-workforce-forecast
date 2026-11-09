"""Exploratory annual LAUS labor-force forecasts, separate from age-cohort models.

Log annual changes are assumed independent normal draws with constant drift and
variance. Student-t prediction limits include future increments and estimation
of drift/variance. They are pointwise model intervals, not validated coverage.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import t


def annual_history(frame, county):
    required = {"state_fips", "county_fips", "year", "labor_force"}
    if frame is None or frame.empty or not required.issubset(frame):
        raise ValueError("Annual county LAUS history is unavailable.")
    if len(str(county)) != 5 or not str(county).isdigit():
        raise ValueError("A five-digit county FIPS is required.")
    states = pd.to_numeric(frame.state_fips, errors="coerce")
    counties = pd.to_numeric(frame.county_fips, errors="coerce")
    rows = frame.loc[states.eq(int(county[:2])) & counties.eq(int(county[-3:])),
                     ["year", "labor_force"]].copy()
    if rows.empty:
        raise ValueError("Annual county LAUS history is unavailable.")
    for col in rows:
        rows[col] = pd.to_numeric(rows[col], errors="coerce")
    if not np.isfinite(rows.to_numpy(dtype=float)).all() or (rows.labor_force <= 0).any():
        raise ValueError("Annual history contains missing or invalid counts; refresh it before forecasting.")
    if rows.year.duplicated().any() or not rows.year.eq(rows.year.astype(int)).all():
        raise ValueError("Annual history must contain one record per county and year.")
    rows = rows.sort_values("year").reset_index(drop=True)
    rows["year"] = rows.year.astype(int)
    if not rows.year.diff().dropna().eq(1).all():
        raise ValueError("Annual history has missing years; a forecast is withheld.")
    return rows


def forecast_annual(history, end_year, min_years=10):
    """Forecast annual averages using a log random walk with estimated drift."""
    if len(history) < min_years:
        raise ValueError(f"At least {min_years} complete annual observations are required.")
    years = history.year.to_numpy(dtype=int)
    values = history.labor_force.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any() or not np.all(np.diff(years) == 1):
        raise ValueError("Forecast history must be positive, finite and consecutive.")
    if int(end_year) != end_year or end_year <= years[-1]:
        raise ValueError("The forecast end year must follow the annual baseline.")
    changes = np.diff(np.log(values))
    n = len(changes)
    drift = changes.mean()
    residual_sd = changes.std(ddof=1)
    if residual_sd <= np.finfo(float).eps:
        raise ValueError("Annual variation is insufficient to estimate a prediction interval.")
    future = np.arange(years[-1]+1, int(end_year)+1)
    horizon = future - years[-1]
    location = np.log(values[-1]) + horizon*drift
    scale = residual_sd*np.sqrt(horizon + horizon**2/n)
    half_width = t.ppf(.9, df=n-1)*scale
    result = pd.DataFrame({"year": future, "median": np.exp(location),
                           "lower80": np.exp(location-half_width), "upper80": np.exp(location+half_width)})
    if not np.isfinite(result.to_numpy(dtype=float)).all():
        raise ValueError("The forecast exceeds a usable numeric range.")
    result.attrs.update({"method": "Log random walk with drift", "annual_log_drift": float(drift),
                         "annual_log_sd": float(residual_sd), "increments": n, "degrees_of_freedom": n-1,
                         "history_start": int(years[0]), "history_end": int(years[-1]),
                         "interval": "80% pointwise prediction interval conditional on model assumptions"})
    return result


def forecast_county(frame, county, as_of_year, horizon=10):
    history = annual_history(frame, county)
    if history.year.iloc[-1] >= as_of_year:
        raise ValueError("Use completed annual averages before the current calendar year.")
    if as_of_year-history.year.iloc[-1] > 2:
        raise ValueError("Annual history is too old for a current ten-year forecast. Refresh LAUS first.")
    return history, forecast_annual(history, as_of_year+horizon)


def rolling_checks(history):
    """Small-sample diagnostics, never presented as ten-year calibration."""
    records = []
    for horizon in (1, 3, 5, 10):
        errors, covered, widths, benchmark_errors = [], [], [], []
        for size in range(6, len(history)-horizon+1):
            training = history.iloc[:size]
            actual = history.iloc[size+horizon-1]
            try:
                prediction = forecast_annual(training, int(actual.year), min_years=6).iloc[-1]
            except ValueError:
                continue
            errors.append(abs(prediction["median"]-actual.labor_force)/actual.labor_force*100)
            covered.append(prediction.lower80 <= actual.labor_force <= prediction.upper80)
            widths.append(prediction.upper80-prediction.lower80)
            benchmark_errors.append(abs(training.labor_force.iloc[-1]-actual.labor_force)/actual.labor_force*100)
        records.append({"Horizon (years)": horizon, "Held-out forecasts": len(errors),
                        "Mean absolute percentage error": np.mean(errors) if errors else np.nan,
                        "No-drift benchmark MAPE": np.mean(benchmark_errors) if errors else np.nan,
                        "Observed interval coverage (%)": np.mean(covered)*100 if covered else np.nan,
                        "Mean interval width (residents)": np.mean(widths) if errors else np.nan})
    return pd.DataFrame(records)
