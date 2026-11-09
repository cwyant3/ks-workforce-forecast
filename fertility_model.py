"""County-calibrated age-specific fertility with Census national rate changes.

This is a county adaptation, not an official Census county projection. Rates
are births per woman-year, and low/high paths are analyst sensitivities.
"""
import numpy as np
import pandas as pd

from demographic_pipeline import project_cohorts

PATHS = {"Low": -.2, "Central": 0., "High": .2}
PRIOR_WOMAN_YEARS = 1000.


def national_schedules(records):
    frame = pd.DataFrame(records)
    schedules = {}
    for year in range(2023, 2043):
        rows = frame[frame.year.eq(year)].sort_values("age")
        if rows.age.tolist() != list(range(14, 55)):
            raise ValueError("Census fertility schedules must cover ages 14–54 once in every year.")
        values = rows.rate.to_numpy(dtype=float)
        if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
            raise ValueError("Invalid Census fertility rate.")
        schedule = np.zeros(120)
        schedule[14:55] = values
        schedules[year] = schedule
    return schedules


def calibrated_schedule(county, pooled, population, state_population, national,
                        prior_woman_years=PRIOR_WOMAN_YEARS):
    """Pool 2020–24 births against ACS2024 five-year female estimates×5.

    Census calendar2024 proxy = mean of year-ended2024/2025 schedules.
    Within native maternal-age bands its shape distributes aggregate births.
    State-rate pseudo-exposure stabilizes small cells; missing cells use state.
    """
    frame = pd.DataFrame(pooled)
    frame = frame[frame.measure.eq("births_by_maternal_age")]
    if not (frame.start_year.eq(2020) & frame.end_year.eq(2024)).all():
        raise ValueError("Local fertility calibration requires the 2020–2024 pooled period.")
    local = frame[frame.county_fips.eq(county)]
    state = frame[frame.county_fips.eq("20")]
    if local.empty or state.empty:
        raise ValueError("County and direct Kansas pooled fertility records are required.")
    reference = (national[2024]+national[2025])/2
    result = np.zeros(120)
    covered = np.zeros(120, dtype=int)
    details = []
    for row in state.itertuples():
        low, high = int(row.age_low), int(row.age_high)
        if not 10 <= low <= high <= 54:
            raise ValueError("Unsupported maternal-age band.")
        # Census places the under15 tail at age14; local35+ uses ages35–54.
        ages = np.arange(max(14,low), high+1)
        shape = reference[ages]
        women = population[1, ages].sum()
        state_women = state_population[1, ages].sum()
        expected_state = state_population[1, ages] @ shape
        if not np.isfinite(row.births) or row.births < 0 or expected_state <= 0:
            raise ValueError("Kansas calibration counts or female exposures are unavailable.")
        state_schedule = shape * (float(row.births)/5) / expected_state
        match = local[local.age_low.eq(low) & local.age_high.eq(high)]
        if len(match) != 1:
            raise ValueError("County maternal-age coverage is incomplete or duplicated.")
        local_count = float(match.births.iloc[0])
        reason = "Pooled county estimate, stabilized toward Kansas"
        if women <= 0 or not np.isfinite(local_count):
            selected = state_schedule
            reason = "Kansas proxy: zero ACS female exposure" if women <= 0 else "Kansas proxy: suppressed county count"
        else:
            if local_count < 0:
                raise ValueError("Negative births cannot enter fertility calibration.")
            state_rate = (population[1, ages] @ state_schedule)/women
            rate = (local_count + prior_woman_years*state_rate)/(5*women+prior_woman_years)
            expected_local = population[1, ages] @ shape
            if expected_local <= 0:
                raise ValueError("No within-band Census age pattern for local exposure.")
            selected = shape * rate*women/expected_local
        result[ages] = selected
        covered[ages] += 1
        details.append({"Published maternal ages": row.age_label,
                        "Modeled exposure ages": str(ages[0]) if len(ages)==1 else f"{ages[0]}–{ages[-1]}",
                        "Pooled births 2020–2024": local_count,
                        "Female exposure proxy per year": women, "Method": reason,
                        "Modeled rate per 1,000 women, before 2025 bridge": float(population[1, ages] @ selected / women*1000) if women else np.nan})
    if not (covered[14:55] == 1).all():
        raise ValueError("Pooled maternal-age bands must cover modeled ages14–54 once.")
    if not np.isfinite(result).all() or ((result < 0) | (result > 1)).any():
        raise ValueError("Calibrated fertility rates outside supported range.")
    return result, pd.DataFrame(details)


def fertility_schedules(local, national, level_adjustment=1.):
    reference = (national[2024]+national[2025])/2
    output = {}
    for path, terminal_change in PATHS.items():
        output[path] = {}
        for year in range(2025, 2043):
            # Zero reference cells may stay zero, but cannot silently gain births.
            if ((reference == 0) & (national[year] > 0)).any():
                raise ValueError("Census fertility ratio undefined at a newly positive age.")
            ratio = np.divide(national[year], reference, out=np.ones(120), where=reference > 0)
            sensitivity = 1 + terminal_change*(year-2025)/17
            output[path][year] = local * ratio * level_adjustment * sensitivity
    return output


def project_fertility_paths(population, shares, survival, local, national, observed_births_2025,
                            annual_net_migrants=0., participation_pp=0.,
                            all_age_net_migrants=None, working_age_share=None):
    """Endogenous births respond to female aging, prior births and migration.

    All paths share observed2025 births. A disclosed level bridge reconciles
    pooled calendar-year calibration with the latest Census year-ended count.
    Migration begins at year-end, affecting next year's maternal exposure.
    """
    if not np.isfinite(observed_births_2025) or observed_births_2025 < 0:
        raise ValueError("The published2025 birth anchor is required.")
    raw = fertility_schedules(local, national)
    anchor = project_cohorts(population, shares, survival, horizon=1,
                            fertility_by_year={2025:raw["Central"][2025]})
    expected = float(anchor.births.iloc[-1])
    if expected <= 0:
        raise ValueError("Female exposure cannot support birth calibration.")
    adjustment = observed_births_2025/expected
    schedules = fertility_schedules(local, national, adjustment)
    paths = {}
    for name in PATHS:
        summary, ages = project_cohorts(population, shares, survival,
                        annual_net_migrants=annual_net_migrants, participation_pp=participation_pp,
                        all_age_net_migrants=all_age_net_migrants, working_age_share=working_age_share,
                        fertility_by_year=schedules[name], births_by_year={2025:observed_births_2025},
                        return_age_states=True)
        summary["fertility_path"] = name
        summary["birth_model"] = "Local age-specific fertility × Census national age-rate changes"
        summary["birth_level_bridge_2025"] = adjustment
        summary["fertility_sensitivity_multiplier"] = 1+PATHS[name]*np.maximum(summary.year-2025,0)/17
        paths[name] = (summary, ages)
    return paths, schedules, adjustment
