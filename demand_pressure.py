"""Observed demand context and conservative derived indicators.

No composite shortage score or inferred worker gap is produced. Source measures
keep their individual periods and quality states; derivations require matched
periods and compatible dimensions.
"""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

import pandas as pd
import numpy as np

from fetch_qcew import parse_quarterly_qcew
from fetch_qwi import DIMENSIONS, latest_valid_by_measure, parse_qwi_response

PILOT_COUNTIES = ["20015", "20035", "20077", "20079", "20095", "20173", "20191"]


def _valid(row) -> bool:
    return row is not None and row["availability_status"] == "published" and pd.notna(row["estimate"])


def quarterly_changes(frame: pd.DataFrame, *, window: int = 4) -> pd.DataFrame:
    """Same-quarter year-over-year growth, with unavailable comparisons retained.

    Calendar matching, not shift(4), prevents a missing quarter from moving the
    comparison to the wrong year. This does not compare overlapping ACS windows.
    """
    if frame.empty:
        return frame.copy()
    if window != 4:
        raise ValueError("This indicator compares the same quarter one year earlier")
    keys = [c for c in ["source_id", "measure_id", *DIMENSIONS] if c in frame]
    rows = []
    for _, group in frame.groupby(keys, dropna=False):
        quarters = pd.to_datetime(group["period_start"]).dt.to_period("Q")
        if quarters.duplicated().any():
            raise ValueError("Duplicate quarter within demand measure dimensions")
        indexed = {q: row for q, (_, row) in zip(quarters, group.iterrows())}
        for q, current in indexed.items():
            prior = indexed.get(q - 4)
            row = current.copy()
            row["measure_id"] += "_yoy_pct"
            row["unit"] = "percent change"
            row["method_type"] = "derived: same-quarter year-over-year"
            valid = _valid(current) and _valid(prior) and prior["estimate"] > 0
            row["estimate"] = (100 * (current["estimate"] / prior["estimate"] - 1)) if valid else None
            row["availability_status"] = "published" if valid else "incomplete"
            row["source_status"] = "matched_quarters" if valid else "missing_unusable_or_zero_base"
            row["comparison_period_start"] = str((q - 4).start_time.date())
            rows.append(row)
    return pd.DataFrame(rows).reset_index(drop=True)


def four_quarter_totals(frame: pd.DataFrame) -> pd.DataFrame:
    """Trailing sums for QWI flows only; all four consecutive quarters required."""
    frame = frame[frame.measure_id.isin(["qwi_hires", "qwi_separations"])].copy()
    keys = [c for c in ["source_id", "measure_id", *DIMENSIONS] if c in frame]
    rows = []
    for _, group in frame.groupby(keys, dropna=False):
        periods = pd.to_datetime(group.period_start).dt.to_period("Q")
        if periods.duplicated().any():
            raise ValueError("Duplicate quarters cannot be added")
        indexed = {q: row for q, (_, row) in zip(periods, group.iterrows())}
        for q, current in indexed.items():
            window = [indexed.get(q - i) for i in range(4)]
            row = current.copy()
            row["measure_id"] += "_trailing4q"
            row["method_type"] = "derived: four consecutive quarters"
            row["frequency"] = "trailing_four_quarters"
            row["period_start"] = str((q - 3).start_time.date())
            row["reference_label"] = f"{q - 3} through {q} (four quarters)"
            valid = all(_valid(item) for item in window)
            row["estimate"] = sum(item["estimate"] for item in window) if valid else None
            row["availability_status"] = "published" if valid else "incomplete"
            row["source_status"] = "complete_four_quarters" if valid else "incomplete_four_quarters"
            rows.append(row)
    return pd.DataFrame(rows).reset_index(drop=True) if rows else frame.iloc[:0].copy()


def matched_hiring_intensity(frame: pd.DataFrame) -> pd.DataFrame:
    """All hires / beginning employment for the same slice and quarter.

    This custom ratio is not QWI's separately defined official hiring rate.
    """
    data = frame[frame.measure_id.isin(["qwi_hires", "qwi_employment"])]
    keys = [c for c in [*DIMENSIONS, "period_start", "period_end"] if c in data]
    rows = []
    for _, group in data.groupby(keys, dropna=False):
        if group.measure_id.duplicated().any():
            raise ValueError("Multiple observations for a hiring-intensity component")
        indexed = {r.measure_id: r for _, r in group.iterrows()}
        hires, employment = indexed.get("qwi_hires"), indexed.get("qwi_employment")
        row = (hires if hires is not None else employment).copy()
        valid = _valid(hires) and _valid(employment) and employment.estimate > 0
        row["measure_id"] = "qwi_hires_per_100_beginning_jobs"
        row["estimate"] = 100 * hires.estimate / employment.estimate if valid else None
        row["unit"] = "hires per 100 beginning-quarter jobs"
        row["method_type"] = "derived: matched-quarter hiring intensity"
        row["availability_status"] = "published" if valid else "incomplete"
        row["source_status"] = "matched_components" if valid else "missing_unusable_or_zero_denominator"
        rows.append(row)
    return pd.DataFrame(rows).reset_index(drop=True) if rows else data.iloc[:0].copy()


def complete_group_total(frame: pd.DataFrame, expected_geographies: list[str]) -> tuple[float | None, str]:
    """Total a single period/measure slice only when every member is published."""
    if frame.geography_id.duplicated().any():
        raise ValueError("Totals require one observation per geography")
    if set(frame.geography_id) != set(expected_geographies) or not all(_valid(r) for _, r in frame.iterrows()):
        return None, "incomplete"
    dimensions = [c for c in ["source_id", "measure_id", "period_start", "period_end", "unit",
                              *[d for d in DIMENSIONS if d != "geography_id"]] if c in frame]
    if any(frame[c].nunique(dropna=False) > 1 for c in dimensions):
        raise ValueError("Cannot total incompatible observation slices")
    if not set(frame.unit).issubset({"jobs", "hires", "separations", "establishments"}):
        raise ValueError("Only additive counts may be totaled across counties")
    return float(frame.estimate.sum()), "published"


def build_demand_indicators(evidence_dir: Path, counties: list[str] | None = None) -> pd.DataFrame:
    """Build an offline pilot from reviewed public evidence, without a refresh.

    Default scope is the seven-county South Central pilot. Pass an explicit full
    county list to retain not-reported scaffolding in QWI (including absent rows).
    """
    evidence_dir = Path(evidence_dir)
    counties = PILOT_COUNTIES if counties is None else counties
    pieces = []
    for path in sorted(evidence_dir.glob("qcew-*-*q*.csv")):
        match = re.fullmatch(r"qcew-(\d{5})-(\d{4})q([1-4])\.csv", path.name)
        if match and match[1] in counties:
            pieces.append(parse_quarterly_qcew(pd.read_csv(path, dtype=str), county_fips=match[1],
                                               year=int(match[2]), quarter=int(match[3])))
    seen = set()
    for path in sorted(evidence_dir.glob("qwi-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list) or len(payload) < 2:
            raise ValueError(f"QWI evidence has no rows: {path.name}")
        period = payload[1][payload[0].index("time")]
        industry = payload[1][payload[0].index("industry")]
        if (period, industry) in seen:
            raise ValueError(f"Duplicate QWI evidence for {industry} {period}: {path.name}")
        seen.add((period, industry))
        pieces.append(parse_qwi_response(payload, counties, period=period, industry=industry, release_id="R2026Q3"))
    if not pieces:
        raise ValueError("No reviewed QCEW/QWI evidence found")
    observed = pd.concat(pieces, ignore_index=True)
    derived = [quarterly_changes(observed), four_quarter_totals(observed), matched_hiring_intensity(observed)]
    return pd.concat([observed, *derived], ignore_index=True)


def recompute_sector_from_cache(outputs: Path, cache_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    """Rebuild Kansas sector forecasts offline, leaving all existing files intact.

    Require every configured annual raw cache before starting. The population
    projection is an immutable input; callers choose where to stage returned
    tables. Impact compares against existing sector outputs when present.
    """
    from fetch_qcew import QCEW_YEARS, _aggregate
    from sector_model import run_all_sectors

    outputs, cache_dir = Path(outputs), Path(cache_dir)
    population_path = outputs / "projections_s20.parquet"
    population_hash = hashlib.sha256(population_path.read_bytes()).hexdigest()
    population = pd.read_parquet(population_path)
    required = [cache_dir / f"s20_{year}.parquet" for year in QCEW_YEARS]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Offline sector rebuild requires cached years: " + ", ".join(missing))
    frames = []
    for year, path in zip(QCEW_YEARS, required):
        frame = pd.read_parquet(path)
        columns = {"area_fips", "year", "naics", "sector", "employment", "suppressed",
                   "avg_annual_pay", "avg_weekly_wage"}
        if frame.empty or not columns.issubset(frame) or set(frame.year) != {year}:
            raise ValueError(f"Invalid annual raw cache: {path.name}")
        if frame.duplicated(["area_fips", "year", "naics"]).any():
            raise ValueError(f"Duplicate annual source components: {path.name}")
        frames.append(frame)
    raw = pd.concat(frames, ignore_index=True)
    counties = set(population.county_fips.astype(str).str.zfill(3))
    county_raw = raw[raw.area_fips.str[-3:].isin(counties) & raw.area_fips.str.startswith("20")]
    state_raw = raw[raw.area_fips == "20000"]
    if state_raw.empty or set(county_raw.area_fips.str[-3:]) != counties:
        raise ValueError("Annual cache does not cover the population projection geography")
    county, _ = _aggregate(county_raw.to_dict("records"), is_county=True)
    state, totals = _aggregate(state_raw.to_dict("records"), is_county=False)
    totals = totals.groupby("year").total_employment.sum(min_count=1).reset_index()
    county_output, state_output = run_all_sectors(county, state, totals, population, state_fips="20")
    tables = {"sector_projections_s20": county_output, "state_sector_projection_s20": state_output}
    impact = {"cache_years": list(QCEW_YEARS), "population_sha256_before": population_hash,
              "county_history_status_counts": {str(k): int(v) for k, v in county.availability_status.value_counts().items()},
              "unavailable_county_sector_pairs": int(county[county.availability_status != "published"]
                                                       [["county_fips", "sector"]].drop_duplicates().shape[0]),
              "tables": {}}
    for name, rebuilt in tables.items():
        for column in ["emp_proj", "emp_ci_lo", "emp_ci_hi", "wage_proj"]:
            values = rebuilt[column].dropna()
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError(f"Invalid nonnegative finite forecast values: {name}/{column}")
        intervals = rebuilt[["emp_ci_lo", "emp_proj", "emp_ci_hi"]].dropna()
        if ((intervals.emp_ci_lo > intervals.emp_proj) | (intervals.emp_proj > intervals.emp_ci_hi)).any():
            raise ValueError(f"Invalid forecast interval ordering: {name}")
        prior_path = outputs / f"{name}.parquet"
        report = {"rows": len(rebuilt), "comparison_available": prior_path.exists(),
                  "quality_counts": {str(k): int(v) for k, v in rebuilt.model_quality.value_counts().items()}}
        if prior_path.exists():
            prior = pd.read_parquet(prior_path)
            keys = ["county_fips", "sector", "year"] if "county_fips" in rebuilt else ["sector", "year"]
            merged = rebuilt.merge(prior, on=keys, suffixes=("_new", "_old"), how="outer", validate="one_to_one", indicator=True)
            changed = merged._merge != "both"
            for column in ["emp_proj", "emp_ci_lo", "emp_ci_hi", "wage_proj", "emp_base"]:
                if f"{column}_old" in merged:
                    changed |= ~np.isclose(merged[f"{column}_new"], merged[f"{column}_old"], equal_nan=True)
            if "method_old" in merged:
                changed |= merged.method_new.fillna("") != merged.method_old.fillna("")
            pair_keys = [k for k in keys if k != "year"]
            changed_pairs = merged.loc[changed, pair_keys].drop_duplicates()
            delta = (merged.emp_proj_new - merged.emp_proj_old).abs()
            report.update(changed_rows=int(changed.sum()), changed_pairs=len(changed_pairs),
                          changed_counties=int(changed_pairs.county_fips.nunique()) if "county_fips" in changed_pairs else None,
                          max_absolute_employment_change=float(delta.max()) if delta.notna().any() else None,
                          affected_pairs=changed_pairs.to_dict("records"))
        impact["tables"][name] = report
    impact["population_sha256_after"] = hashlib.sha256(population_path.read_bytes()).hexdigest()
    impact["population_unchanged"] = impact["population_sha256_after"] == population_hash
    impact["numeric_invariants"] = "Finite nonnegative nonmissing forecasts; lower CI <= projection <= upper CI"
    if not impact["population_unchanged"]:
        raise RuntimeError("Population input changed during sector rebuild")
    return tables, impact
