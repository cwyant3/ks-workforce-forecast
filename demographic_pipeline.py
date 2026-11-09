"""Deterministic aging of resident cohorts, with explicit scenario assumptions.

The 18-year age-18+ horizon needs no post-baseline births. It is not a prediction
interval model and does not extrapolate LAUS. Arrays use sex (male,female), age.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd


SEXES = ("male", "female")
AGE_BANDS = [(0, 4), (5, 9), (10, 14), (15, 17), (18, 19), (20, 20),
             (21, 21), (22, 24), (25, 29), (30, 34), (35, 39), (40, 44),
             (45, 49), (50, 54), (55, 59), (60, 61), (62, 64), (65, 66),
             (67, 69), (70, 74), (75, 79), (80, 84), (85, 119)]
DISPLAY_BANDS = {"18–24": (18, 24), "25–54": (25, 54),
                 "55–64": (55, 64), "65+": (65, 119)}


def _count(value):
    number = float(value)
    if not np.isfinite(number) or number < 0:
        raise ValueError("Missing or suppressed demographic input; projection withheld.")
    return number


def single_age_population(record, survivors):
    """Allocate ACS age-sex estimates. Single ages within a band are modeled."""
    survivors = np.asarray(survivors, dtype=float)
    if survivors.shape != (2, 120) or not np.isfinite(survivors).all() or (survivors < 0).any():
        raise ValueError("Valid survival-table age weights are required.")
    result = np.zeros((2, 120))
    for sex, first in enumerate((3, 27)):
        for index, (low, high) in enumerate(AGE_BANDS):
            count = _count(record[f"B01001_{first+index:03d}E"])
            weights = np.ones(high-low+1) if low < 85 else survivors[sex, low:high+1]
            if weights.sum() <= 0:
                raise ValueError("Invalid 85+ age allocation weights.")
            result[sex, low:high+1] = count * weights / weights.sum()
    if not np.isclose(result.sum(), _count(record["B01001_001E"])):
        raise ValueError("Age-sex counts do not reconcile to the county population.")
    return result


def _age_range(label):
    numbers = [int(number) for number in re.findall(r"\d+", label)]
    if "years and over" in label and len(numbers) == 1:
        return numbers[0], 119
    if ("to" in label or "and" in label) and len(numbers) == 2:
        return numbers[0], numbers[1]
    raise ValueError(f"Unsupported ACS participation age band: {label}")


def participation_shares(record, labels, fallback=None, return_proxies=False):
    """Civilian LF / all residents in each sex-age band, not official LFPR.

    This effective share allows multiplication by B01001 resident counts while
    retaining baseline exclusions (military/institutional) in the share. Below
    65, B23001 has a civilian child; older bands have no Armed Forces branch.
    """
    groups = {}
    for variable, metadata in labels.items():
        if not variable.startswith("B23001_") or not variable.endswith("E"):
            continue
        label = metadata["label"] if isinstance(metadata, dict) else metadata
        parts = [part.strip().rstrip(":").lower() for part in label.split("!!")]
        if len(parts) < 4 or parts[2] not in SEXES or "years" not in parts[3]:
            continue
        low, high = _age_range(parts[3])
        group = groups.setdefault((SEXES.index(parts[2]), low, high), {})
        if len(parts) == 4:
            group["total"] = _count(record[variable])
        elif parts[4:] == ["in labor force", "civilian"] or (low >= 65 and parts[4:] == ["in labor force"]):
            group["lf"] = _count(record[variable])
    shares = np.zeros((2, 120))
    proxies = []
    fallback_shares = participation_shares(fallback, labels) if fallback is not None else None
    coverage = np.zeros((2, 120), dtype=int)
    for (sex, low, high), values in groups.items():
        if set(values) != {"total", "lf"} or values["lf"] > values["total"]:
            raise ValueError("Incomplete or inconsistent age-specific participation inputs.")
        if values["total"] == 0:
            if fallback_shares is None:
                raise ValueError("An age-sex participation cell has no resident sample estimate; projection withheld.")
            shares[sex, low:high+1] = fallback_shares[sex, low:high+1]
            proxies.append(f"{SEXES[sex]} ages {low}–{high if high < 119 else 'older'}")
        else:
            shares[sex, low:high+1] = values["lf"] / values["total"]
        coverage[sex, low:high+1] += 1
    if not (coverage[:, 16:] == 1).all():
        raise ValueError("Participation inputs do not cover all ages 16+ exactly once.")
    return (shares, proxies) if return_proxies else shares


def project_cohorts(population, shares, survival, base_year=2024, horizon=18,
                    annual_net_migrants=0.0, participation_pp=0.0, return_age_states=False,
                    births_by_year=None, birth_sex_share=None, fertility_by_year=None):
    """Age one year per step; add/subtract age-18–64 migrants after aging.

    In-migrants follow the baseline 18–64 age-sex distribution. Out-migrants
    follow the current 18–64 distribution, capped at available residents.
    Participation changes phase in linearly across the horizon, bounded 0–1.
    Optional births enter at average age0.5, using half-year survival. These
    cohorts are tracked separately so subsequent transitions use midpoint-age
    survival, avoiding repeated full infant mortality. Birth timing/hazards are
    approximations, not directly observed infant survival.
    """
    pop, rates, survival = (np.asarray(x, dtype=float).copy() for x in (population, shares, survival))
    if any(x.shape != (2, 120) or not np.isfinite(x).all() for x in (pop, rates, survival)):
        raise ValueError("Complete finite age-sex arrays are required.")
    if (pop < 0).any() or ((rates < 0) | (rates > 1)).any() or ((survival < 0) | (survival > 1)).any():
        raise ValueError("Population or probability outside valid range.")
    if not isinstance(horizon, int) or not 1 <= horizon <= 18:
        raise ValueError("The observed-cohort horizon must be 1–18 years.")
    if not np.isfinite(annual_net_migrants) or not np.isfinite(participation_pp):
        raise ValueError("Scenario assumptions must be finite.")
    newborns = np.zeros_like(pop)
    midpoint_survival = np.sqrt(survival * np.c_[survival[:, 1:], survival[:, -1]])
    expected_years = set(range(base_year+1, base_year+horizon+1))
    if fertility_by_year is not None:
        if set(fertility_by_year) != expected_years:
            raise ValueError("Fertility schedules must cover every projection year.")
        for schedule in fertility_by_year.values():
            schedule = np.asarray(schedule, dtype=float)
            if schedule.shape != (120,) or not np.isfinite(schedule).all() or ((schedule < 0) | (schedule > 1)).any():
                raise ValueError("Fertility rates must be finite, between zero and one birth per woman-year by age.")
    if births_by_year is not None or fertility_by_year is not None:
        if births_by_year is not None:
            if (not set(births_by_year).issubset(expected_years) or
                (fertility_by_year is None and set(births_by_year) != expected_years) or
                any(not np.isfinite(v) or v < 0 for v in births_by_year.values())):
                raise ValueError("Birth inputs must cover every projection year without fertility schedules, and be finite/nonnegative.")
        if birth_sex_share is None:
            young = pop[:, :5].sum(axis=1)
            if young.sum() <= 0:
                raise ValueError("A newborn sex-share assumption is required without an under-five baseline.")
            birth_sex_share = young/young.sum()
        birth_sex_share = np.asarray(birth_sex_share, dtype=float)
        if birth_sex_share.shape != (2,) or not np.isfinite(birth_sex_share).all() or (birth_sex_share < 0).any() or not np.isclose(birth_sex_share.sum(), 1):
            raise ValueError("Newborn sex shares must be nonnegative and sum to one.")
    migration_weights = np.zeros_like(pop)
    migration_weights[:, 18:65] = pop[:, 18:65]
    if migration_weights.sum() <= 0:
        raise ValueError("No working-age residents in this baseline.")
    migration_weights /= migration_weights.sum()
    rows, age_states = [], []
    for step in range(horizon+1):
        entrants = crossing65 = migration_applied = births = newborn_survivors = 0.0
        female_exposure = modeled_births = np.nan
        if step:
            start_female = pop[1].copy()
            born_living = newborns * midpoint_survival
            living = (pop-newborns) * survival + born_living
            entrants = living[:, 17].sum()
            crossing65 = living[:, 64].sum()
            pop = np.concatenate([np.zeros((2, 1)), living[:, :-1]], axis=1)
            # The published table ends at119 with positive survival. Keep all
            # survivors in a119+ open cell, using the terminal mortality rate.
            pop[:, 119] += living[:, 119]
            newborns = np.concatenate([np.zeros((2, 1)), born_living[:, :-1]], axis=1)
            newborns[:, 119] += born_living[:, 119]
            if annual_net_migrants >= 0:
                migration = annual_net_migrants * migration_weights
            else:
                working_age = np.zeros_like(pop)
                working_age[:, 18:65] = pop[:, 18:65]
                total = working_age.sum()
                migration = working_age * max(annual_net_migrants / total, -1) if total else working_age
            pop += migration
            migration_applied = migration.sum()
            # Migrants arrive at year-end, so contribute to births from next year.
            # Mean start/end female exposure approximates age transitions and survival.
            if fertility_by_year is not None:
                exposure = (start_female + pop[1] - migration[1])/2
                births = float(exposure @ fertility_by_year[base_year+step])
                female_exposure = exposure[14:55].sum()
                modeled_births = births
            if births_by_year is not None and base_year+step in births_by_year:
                births = float(births_by_year[base_year+step])
            if births_by_year is not None or fertility_by_year is not None:
                arriving = births * birth_sex_share * np.sqrt(survival[:, 0])
                pop[:, 0] += arriving
                newborns[:, 0] += arriving
                newborn_survivors = arriving.sum()
        adjusted = np.clip(rates + participation_pp/100 * step/horizon, 0, 1)
        workforce = pop * adjusted
        row = dict(year=base_year+step, residents_all_ages=pop.sum(), residents_under_18=pop[:, :18].sum(),
                   births=births if step else np.nan, newborn_survivors=newborn_survivors if step else np.nan,
                   female_exposure_14_54=female_exposure, births_before_observed_override=modeled_births,
                   residents_18_plus=pop[:, 18:].sum(),
                   labor_force_18_plus=workforce[:, 18:].sum(),
                   reaching_18=entrants if step else np.nan,
                   reaching_65=crossing65 if step else np.nan,
                   net_migrants_applied=migration_applied,
                   annual_net_migrants_assumption=annual_net_migrants,
                   participation_change_pp=participation_pp * step/horizon)
        for name, (low, high) in DISPLAY_BANDS.items():
            row[f"residents_{name}"] = pop[:, low:high+1].sum()
            row[f"labor_force_{name}"] = workforce[:, low:high+1].sum()
        rows.append(row)
        if return_age_states:
            age_states.append(pop.copy())
    result = pd.DataFrame(rows)
    return (result, np.stack(age_states)) if return_age_states else result
