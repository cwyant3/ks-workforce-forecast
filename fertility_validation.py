"""Retrospective diagnostic of frozen maternal-age fertility and county aging.

This is not a backtest of the current operational forecast: historical ACS
rolling estimates are used as exposure proxies, survival is held at one, net
migration is zero, and the published 35+ birth group is spread across 35–54.
No later mortality schedule or later ACS vintage enters an origin's prediction.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from birth_forecast import MODELS, DEFAULT_DAMPING, model_prediction

INPUT_DIR = Path(__file__).resolve().parent / "data/demographic_inputs"
MATERNAL_BANDS = ((10, 14), (15, 17), (18, 19), (20, 24), (25, 29), (30, 34), (35, 54))
COHORT_ID = "frozen_asfr_cohort"
MODEL_LABELS = {**MODELS, COHORT_ID: "Frozen maternal-age rates · zero-migration female cohort"}
COUNTIES = {f"20{n:03d}" for n in range(1, 210, 2)}
NOTES = [
    "Diagnostic of a cohort mechanism, not an exact backtest of the current production model.",
    "ACS origin-year five-year female counts ×5 approximate exposure for the five calendar birth years through that origin.",
    "Rolling ACS windows overlap; estimates were retrieved retrospectively, not as of historical publication dates.",
    "Single ages are uniform within native ACS bands. Annual birth exposure averages women before and after aging one year.",
    "Fertility rates are frozen within maternal-age bands, with unit survival and zero migration; no future mortality schedule is used.",
    "Published maternal age 35+ births are assigned to ages 35–54; constant fertility across this broad tail may misrepresent older maternal ages.",
    "Any missing/suppressed county age-year, or zero local exposure, triggers the full statewide pooled rate for that band, never reconstruction of a hidden cell.",
    "Statewide female exposure is the sum of all 105 county ACS estimates at the same origin, not an independently published statewide estimate.",
    "Unknown maternal-age births are excluded from rates; observed total-birth targets include them. This creates a documented target mismatch.",
    "All six models share exactly the same eligible origins and horizons. Missing county total births exclude comparisons rather than becoming zero.",
    "Scores weight 1-, 3- and 5-year MAEs equally. They are model-selection diagnostics, not independent accuracy guarantees or long-horizon validation.",
    "Mean signed bias is prediction minus actual, averaged within each horizon and then equally across horizons; positive means overprediction.",
    "WAPE is 100 × sum absolute errors / sum actual births within each horizon, then equally averaged across horizons; it is not MAPE and is unavailable if any horizon has a zero actual total.",
]


def _annual_births(frame):
    required = {"county_fips", "year", "age_low", "age_high", "measure", "births"}
    if not required.issubset(frame):
        raise ValueError("Annual birth source lacks required identity/count fields")
    rows = frame.copy()
    rows["county_fips"] = rows.county_fips.astype(str)
    for col in ("year", "age_low", "age_high", "births"):
        rows[col] = pd.to_numeric(rows[col], errors="coerce")
    annual = rows.year.notna() & rows.year.mod(1).eq(0)
    for col in ("start_year", "end_year"):
        if col in rows:
            annual &= pd.to_numeric(rows[col], errors="coerce").eq(rows.year)
    rows = rows.loc[annual].copy()
    key = ["county_fips", "year", "measure", "age_low", "age_high"]
    if rows.duplicated(key).any():
        raise ValueError("Duplicate annual county/maternal-age birth observations")
    status = "availability_status" if "availability_status" in rows else "status" if "status" in rows else None
    if status:
        rows.loc[~rows[status].eq("published"), "births"] = np.nan
    rows.loc[~np.isfinite(rows.births) | rows.births.lt(0), "births"] = np.nan
    rows["year"] = rows.year.astype(int)
    return rows


def _female_at_origin(native, county, origin):
    rows = native[(native.county_fips.astype(str) == county) & (native.year == origin) & native.sex.eq("female")].copy()
    if rows.empty:
        raise ValueError("Native female age distribution is absent")
    ages = np.zeros(120)
    covered = np.zeros(120, dtype=int)
    for row in rows.itertuples():
        low = int(row.age_low)
        high = 119 if pd.isna(row.age_high) else int(row.age_high)
        value = float(row.population) if pd.notna(row.population) else np.nan
        status = getattr(row, "availability_status", "published")
        if not (0 <= low <= high <= 119) or not np.isfinite(value) or value < 0 or status != "published":
            raise ValueError("Unusable female age distribution")
        ages[low:high+1] += value / (high-low+1)
        covered[low:high+1] += 1
    if not (covered == 1).all():
        raise ValueError("Female age distribution has overlapping or missing native bands")
    return ages


def _state_female(native, origin):
    source = native[native.year.eq(origin) & native.sex.eq("female")]
    if set(source.county_fips.astype(str)) != COUNTIES:
        raise ValueError("State fallback exposure requires all 105 counties at the origin")
    return np.sum([_female_at_origin(source, county, origin) for county in sorted(COUNTIES)], axis=0)


def _pool_births(births, county, origin, measure, band=None):
    rows = births[births.county_fips.eq(county) & births.measure.eq(measure)
                  & births.year.between(origin-4, origin)]
    if band:
        rows = rows[rows.age_low.eq(band[0]) & rows.age_high.eq(band[1])]
    if len(rows) != 5 or set(rows.year) != set(range(origin-4, origin+1)) or rows.births.isna().any():
        return None
    return float(rows.births.sum())


def frozen_origin_rates(births, native, county, origin):
    """Build one origin's rates and disclosure audit using no later observation."""
    female = _female_at_origin(native, county, origin)
    state = None
    rates = np.zeros(120)
    audit = []
    for low, high in MATERNAL_BANDS:
        count = _pool_births(births, county, origin, "births_by_maternal_age", (low, high))
        local_exposure = float(female[low:high+1].sum() * 5)
        fallback = count is None or local_exposure <= 0
        denominator = local_exposure
        reason = ""
        if fallback:
            reason = "missing_or_suppressed_age_year" if count is None else "zero_county_exposure"
            count = _pool_births(births, "20", origin, "births_by_maternal_age", (low, high))
            if state is None:
                state = _state_female(native, origin)
            denominator = float(state[low:high+1].sum() * 5)
        if count is None or denominator <= 0:
            raise ValueError(f"No usable pooled rate for maternal ages {low}–{high}")
        rate = count / denominator
        rates[low:high+1] = rate
        audit.append(dict(origin=origin, age_low=low, age_high=high, rate=rate,
                          rate_basis="statewide_pooled" if fallback else "county_pooled",
                          fallback_reason=reason, pooled_births=count,
                          female_person_year_proxy=denominator, county_person_year_proxy=local_exposure))
    return female, rates, audit


def cohort_birth_path(female, rates, horizon=5):
    """Annual births from average before/after-aging exposure, no deaths/migration."""
    population = np.asarray(female, dtype=float).copy()
    rates = np.asarray(rates, dtype=float)
    if population.shape != (120,) or rates.shape != (120,) or not np.isfinite([population, rates]).all():
        raise ValueError("Complete finite female population and fertility arrays required")
    if (population < 0).any() or (rates < 0).any():
        raise ValueError("Population and fertility cannot be negative")
    result = {}
    for step in range(1, horizon+1):
        advanced = np.r_[0., population[:-1]]
        advanced[-1] += population[-1]
        result[step] = float(np.sum((population+advanced)/2 * rates))
        population = advanced
    return result


def compare_fertility_models(county, births=None, native_history=None, input_dir=INPUT_DIR,
                              *, origins=None, horizons=(1, 3, 5), damping=DEFAULT_DAMPING,
                              totals_history=None):
    """Return (ranking DataFrame, matched errors DataFrame, coverage dict).

    Inputs are DataFrames or loaded from the reviewed local JSON directory.
    Separately published all-age totals may replace only aggregate outcomes;
    they are never used to reconstruct suppressed maternal-age cells.
    Missing sources/inputs remain an explicit coverage limitation. The benchmark
    requires ten consecutive published total-birth years ending at each origin;
    longer contiguous history is used when available for the full-history trend.
    """
    county = str(county)
    if county not in COUNTIES:
        raise ValueError("Select one five-digit Kansas county FIPS")
    if not 0 < damping < 1 or set(horizons) != {1, 3, 5}:
        raise ValueError("Use fixed damping between 0 and 1 and horizons 1, 3 and 5")
    directory = Path(input_dir)
    load_defaults = births is None
    if load_defaults:
        births = pd.DataFrame(json.loads((directory/"fertility_county_births_2006_2024.json").read_text()))
        totals_file = directory/"fertility_birth_totals_2006_2024.json"
        if totals_history is None and totals_file.exists():
            totals_history = pd.DataFrame(json.loads(totals_file.read_text()))
    if native_history is None:
        native_history = pd.DataFrame(json.loads((directory/"female_native_age_sex_history_2010_2024.json").read_text()))
    annual = _annual_births(births)
    totals_basis = "Total-birth records supplied with maternal-age inputs"
    if totals_history is not None:
        independent = totals_history.copy()
        for column in ("age_low", "age_high"):
            if column not in independent:
                independent[column] = np.nan
        independent = _annual_births(independent)
        independent = independent[independent.measure.eq("births_total")]
        annual = pd.concat([annual[~annual.measure.eq("births_total")], independent], ignore_index=True)
        totals_basis = "Separately published all-age KDHE annual totals; never used to reconstruct maternal-age cells"
    required = {"county_fips", "year", "sex", "age_low", "age_high", "population"}
    if not required.issubset(native_history):
        raise ValueError("Native female history lacks required fields")
    native = native_history.copy()
    native["year"] = pd.to_numeric(native.year, errors="coerce")
    totals = annual[annual.county_fips.eq(county) & annual.measure.eq("births_total")].set_index("year").births
    coverage = dict(county_fips=county, notes=NOTES.copy(), skipped=[], origins=[], rate_audit=[],
                    damping=damping, totals_basis=totals_basis,
                    target_source="KDHE calendar-year resident total births, including unknown maternal age")
    checks = []
    for origin in (range(2015, 2024) if origins is None else origins):
        if not isinstance(origin, (int, np.integer)) or not 2015 <= origin <= 2023:
            raise ValueError("Validation origins must lie within 2015–2023")
        valid_years = set(totals.dropna().index)
        if not set(range(origin-9, origin+1)).issubset(valid_years):
            coverage["skipped"].append(dict(origin=origin, reason="Fewer than ten consecutive usable county annual totals"))
            continue
        start = origin-9
        while start-1 in valid_years:
            start -= 1
        training = totals.reindex(range(start, origin+1)).rename("births").rename_axis("year").reset_index()
        try:
            female, rates, audit = frozen_origin_rates(annual, native, county, origin)
        except ValueError as exc:
            coverage["skipped"].append(dict(origin=origin, reason=str(exc)))
            continue
        path = cohort_birth_path(female, rates, max(horizons))
        fallback_count = sum(row["rate_basis"] == "statewide_pooled" for row in audit)
        origin_coverage = dict(origin=origin, training_start=start, training_observations=len(training),
                               state_fallback_bands=fallback_count, total_rate_bands=len(MATERNAL_BANDS),
                               unknown_age_births_pooled=_pool_births(annual, county, origin, "births_unknown_age"))
        used = []
        for horizon in sorted(set(horizons)):
            target = origin+horizon
            if target > 2024:
                continue
            if target not in valid_years:
                coverage["skipped"].append(dict(origin=origin, horizon=horizon, reason="Target total births unavailable"))
                continue
            predictions = {model:model_prediction(training, target, model, damping) for model in MODELS}
            predictions[COHORT_ID] = path[horizon]
            if not np.isfinite(list(predictions.values())).all():
                raise ValueError("A comparison model produced nonfinite births")
            actual = float(totals.loc[target])
            for model, prediction in predictions.items():
                checks.append(dict(county_fips=county, model=model, origin=origin, horizon=horizon,
                                   target_year=target, actual=actual, prediction=float(prediction),
                                   error=abs(prediction-actual), signed_error=prediction-actual,
                                   state_fallback_bands=fallback_count if model == COHORT_ID else 0,
                                   training_start=start, training_observations=len(training)))
            used.append(horizon)
        if used:
            origin_coverage["horizons"] = used
            coverage["origins"].append(origin_coverage)
            coverage["rate_audit"].extend(audit)
    errors = pd.DataFrame(checks, columns=["county_fips", "model", "origin", "horizon", "target_year", "actual",
        "prediction", "error", "signed_error", "state_fallback_bands", "training_start", "training_observations"])
    coverage["matched_comparisons_per_model"] = len(errors)//len(MODEL_LABELS)
    coverage["available_horizons"] = sorted(errors.horizon.unique().tolist())
    summaries = []
    if not errors.empty and set(errors.horizon) == {1, 3, 5}:
        for model, label in MODEL_LABELS.items():
            subset = errors[errors.model.eq(model)]
            means = subset.groupby("horizon").error.mean()
            bias = subset.groupby("horizon").signed_error.mean()
            actual_totals = subset.groupby("horizon").actual.sum()
            wape = 100 * subset.groupby("horizon").error.sum() / actual_totals.replace(0, np.nan)
            summaries.append(dict(model=model, Method=label,
                **{"Mean absolute error":float(means.mean()), "1-year MAE":float(means[1]),
                   "3-year MAE":float(means[3]), "5-year MAE":float(means[5]),
                   "Mean signed bias":float(bias.mean()),
                   "WAPE (%)":float(wape.mean()) if wape.notna().all() else np.nan,
                   "Held-out comparisons":len(subset)}))
    else:
        coverage["notes"].append("Insufficient matched comparisons across all three horizons; no overall ranking is issued.")
    ranking = pd.DataFrame(summaries, columns=["model", "Method", "Mean absolute error",
                                              "1-year MAE", "3-year MAE", "5-year MAE", "Mean signed bias",
                                              "WAPE (%)", "Held-out comparisons"])
    if not ranking.empty:
        ranking = ranking.sort_values("Mean absolute error", kind="stable").reset_index(drop=True)
    return ranking, errors, coverage
