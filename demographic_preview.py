"""Opt-in July2025 population preview; adopted inputs/forecasts stay unchanged."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from demographic_pipeline import single_age_population, participation_shares, project_cohorts
from fertility_model import national_schedules, calibrated_schedule, fertility_schedules, PATHS

ROOT = Path(__file__).resolve().parent
INPUT = ROOT/"data/demographic_inputs"
PREVIEW = ROOT/"data/demographic_preview"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def records(path):
    table = read(path)
    return [dict(zip(table[0], row)) for row in table[1:]]


def pep_population(county, survivors, directory=PREVIEW):
    manifest = read(directory/"source_manifest.json")
    if manifest["status"] != "preview-not-adopted" or manifest["reference_date"] != "2025-07-01":
        raise ValueError("Expected the reviewed July2025 preview manifest.")
    for file_key, hash_key in [("source_file", "source_sha256"), ("normalized_file", "normalized_sha256")]:
        name = manifest[file_key]
        if Path(name).name != name or hashlib.sha256((directory/name).read_bytes()).hexdigest() != manifest[hash_key]:
            raise ValueError("Preview source or normalized population hash mismatch.")
    data = read(directory/manifest["normalized_file"])
    expected = {r["state"]+r["county"] for r in records(INPUT/"acs2024_5yr_B01001_response.json")}
    if (data["reference_date"] != "2025-07-01" or data["year_code"] != 7 or
        data["sex"] != ["male", "female"] or data["ages"] != list(range(85))+["85+"] or
        set(data["counties"]) != expected or len(expected) != 105 or county not in expected):
        raise ValueError("Preview population geography, date or age grid is invalid.")
    native = np.asarray(data["counties"][county]["population"], dtype=float)
    survivors = np.asarray(survivors, dtype=float)
    if native.shape != (2, 86) or not np.isfinite(native).all() or (native < 0).any():
        raise ValueError("Invalid published age/sex population.")
    if survivors.shape != (2, 120) or not np.isfinite(survivors).all() or (survivors < 0).any() or (survivors[:, 85:].sum(axis=1) <= 0).any():
        raise ValueError("Invalid survivor weights for the modeled85+ split.")
    result = np.zeros((2, 120))
    result[:, :85] = native[:, :85]
    result[:, 85:] = native[:, 85, None]*survivors[:, 85:]/survivors[:, 85:].sum(axis=1)[:, None]
    if not np.allclose(result.sum(axis=1), native.sum(axis=1)):
        raise ValueError("Modeled85+ split does not preserve sex totals.")
    return result


def load_context(county):
    acs = {r["state"]+r["county"]: r for r in records(INPUT/"acs2024_5yr_B01001_response.json")}
    labor = {r["state"]+r["county"]: r for r in records(INPUT/"acs2024_5yr_B23001_response.json")}
    if county not in acs:
        raise ValueError("Preview supports the105 reviewed Kansas counties only.")
    life = pd.DataFrame(read(INPUT/"survival_age_sex.json")).sort_values("age")
    if life.age.tolist() != list(range(120)):
        raise ValueError("Complete survival ages0–119 are required.")
    survivors = life[["male_lx", "female_lx"]].to_numpy().T
    survival = 1-life[["male_qx", "female_qx"]].to_numpy().T
    original = single_age_population(acs[county], survivors)
    population = pep_population(county, survivors)
    state = records(INPUT/"acs2024_5yr_B23001_state_response.json")[0]
    labels = read(INPUT/"acs2024_5yr_B23001_labels.json")["variables"]
    shares, proxies = participation_shares({**acs[county], **labor[county]}, labels, fallback=state, return_proxies=True)
    national = national_schedules(read(INPUT/"census_fertility_rates.json"), end_year=2043)
    # Preserve matched-period ACS2020–24 exposure; PEP2025 is not its denominator.
    state_population = sum(single_age_population(row, survivors) for row in acs.values())
    local, calibration = calibrated_schedule(county, read(INPUT/"fertility_pooled_births_2020_2024.json"), original, state_population, national)
    history = pd.DataFrame(read(INPUT/"births_county_2006_2025.json"))
    anchor_rows = history[history.county_fips.eq(county) & history.year.eq(2025)]
    if len(anchor_rows) != 1 or not np.isfinite(anchor_rows.births.iloc[0]) or anchor_rows.births.iloc[0] < 0:
        raise ValueError("Unique nonnegative published2025 birth anchor is required.")
    raw = fertility_schedules(local, national)
    anchor = project_cohorts(original, shares, survival, horizon=1, fertility_by_year={2025: raw["Central"][2025]})
    expected = float(anchor.births.iloc[-1])
    if expected <= 0:
        raise ValueError("Original calibration exposure cannot support the2025 level bridge.")
    adjustment = float(anchor_rows.births.iloc[0])/expected
    return population, shares, survival, proxies, local, national, calibration, adjustment, county


def preview_paths(context, annual_net_migrants=0., participation_pp=0.):
    population, shares, survival, _, local, national, _, adjustment, county = context
    schedules = fertility_schedules(local, national, adjustment, base_year=2025, horizon=18)
    paths = {}
    for name in PATHS:
        summary, ages = project_cohorts(population, shares, survival, base_year=2025, horizon=18,
                        fertility_by_year=schedules[name], return_age_states=True,
                        annual_net_migrants=annual_net_migrants, participation_pp=participation_pp)
        summary["fertility_path"] = name
        summary["population_basis"] = "Preview: Census PEP July2025"
        summary["population_reference_date"] = "2025-07-01"
        summary["county_fips"] = county
        summary["fertility_level_from_original_2025_bridge"] = adjustment
        paths[name] = summary, ages
    return paths, schedules
