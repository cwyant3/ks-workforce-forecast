"""County birth-count models compared using rolling calendar-year holdouts.

Trend candidates fit OLS on log(1+births) with fixed geometric damping. These
are not Holt/ETS fits; historical selection does not validate an18-year outlook.
"""
import numpy as np
import pandas as pd

DEFAULT_DAMPING = .8
MODELS = {"last": "Last observed year", "mean5": "Recent five-year mean",
          "trend5": "Damped log trend · 5 years", "trend10": "Damped log trend · 10 years",
          "trend_all": "Damped log trend · full history"}


def _fit_predict(values, horizon, damping, years=None):
    values = np.asarray(values, dtype=float)
    x = np.arange(len(values), dtype=float) if years is None else np.asarray(years, dtype=float)-min(years)
    slope, intercept = np.polyfit(x, np.log1p(values), 1)
    log_level = intercept+slope*x[-1]
    damped_steps = np.cumsum(damping**np.arange(1, horizon+1))
    predictions = np.maximum(np.expm1(log_level+slope*damped_steps), 0)
    if not np.isfinite(predictions).all():
        raise ValueError("Birth forecast is nonfinite; source review required.")
    return predictions, float(slope), float(log_level)


def model_prediction(training, target_year, model, damping=DEFAULT_DAMPING):
    training = training.sort_values("year")
    if model == "last":
        return float(training.births.iloc[-1])
    if model == "mean5":
        return float(training.loc[training.year >= training.year.max()-4, "births"].mean())
    if model not in MODELS:
        raise ValueError("Unknown birth forecasting model.")
    window = {"trend5": 5, "trend10": 10, "trend_all": None}[model]
    if window:
        training = training[training.year >= training.year.max()-window+1]
    return float(_fit_predict(training.births, int(target_year-training.year.max()), damping, training.year)[0][-1])


def compare_models(history, damping=DEFAULT_DAMPING):
    """Identical expanding origins for every model; equal weights by horizon."""
    checks = []
    for n in range(10, len(history)):
        training = history.iloc[:n]
        origin = int(training.year.iloc[-1])
        for horizon in (1, 3, 5):
            heldout = history[history.year.eq(origin+horizon)]
            if heldout.empty:
                continue
            actual = float(heldout.births.iloc[0])
            for model in MODELS:
                prediction = model_prediction(training, origin+horizon, model, damping)
                checks.append(dict(model=model, origin=origin, horizon=horizon,
                                   actual=actual, prediction=prediction, error=abs(prediction-actual)))
    errors = pd.DataFrame(checks)
    if errors.empty or set(errors.horizon) != {1, 3, 5}:
        raise ValueError("Birth history cannot support 1-, 3- and 5-year model comparisons.")
    summaries = []
    for model in MODELS:
        selected = errors[errors.model.eq(model)]
        means = selected.groupby("horizon").error.mean()
        summaries.append({"model": model, "Method": MODELS[model],
                          "Mean absolute error": float(means.mean()),
                          "1-year MAE": means[1], "3-year MAE": means[3], "5-year MAE": means[5],
                          "Held-out comparisons": len(selected)})
    ranking = pd.DataFrame(summaries).sort_values("Mean absolute error", kind="stable").reset_index(drop=True)
    return ranking, errors


def project_births(history, end_year=2042, damping=DEFAULT_DAMPING):
    """Compare simple models using the available twenty-year history window."""
    if not 0 < damping < 1:
        raise ValueError("Birth trend damping must be between zero and one.")
    rows = history[["year", "births"]].copy()
    for column in rows:
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    if not np.isfinite(rows.to_numpy()).all() or (rows.births < 0).any() or (rows.year % 1 != 0).any():
        raise ValueError("Birth observations must have whole years and finite nonnegative counts.")
    if rows.year.duplicated().any():
        raise ValueError("Duplicate annual birth observations.")
    rows = rows.sort_values("year").reset_index(drop=True)
    rows = rows[rows.year >= rows.year.max()-19].reset_index(drop=True)
    if len(rows) != 20 or not (np.diff(rows.year) == 1).all():
        raise ValueError("Twenty consecutive annual birth estimates are required.")
    last = int(rows.year.iloc[-1])
    if not isinstance(end_year, int) or end_year <= last:
        raise ValueError("Birth forecast endpoint must follow the history.")
    ranking, checks = compare_models(rows, damping)
    selected = ranking.iloc[0].model
    values = [model_prediction(rows, year, selected, damping) for year in range(last+1, end_year+1)]
    rows["status"] = "Census estimate"
    future = pd.DataFrame({"year": range(last+1, end_year+1), "births": values, "status": "Modeled births"})
    output = pd.concat([rows, future], ignore_index=True)
    output["year"] = output.year.astype(int)
    output.attrs.update(damping=damping, model=selected, model_label=MODELS[selected],
                        history_start=int(rows.year.min()), history_end=last, observations=len(rows),
                        missing_years=sorted(set(range(last-19,last+1))-set(rows.year)),
                        ranking=ranking.to_dict("records"), checks=checks.to_dict("records"))
    return output
