"""Kansas City metro context: all 14 counties of OEWS area 28140 from matching federal sources.

The table compares Kansas and Missouri counties with the same observed
sources and periods (ACS 2020-2024 5-year, BLS LAUS monthly, QCEW quarterly
private jobs). Working-age (18-64) population forecasts are included because
both states now run the same cohort model with the same NCES CCD single-grade
youth update (Missouri added 2026-11-10); the run seed and base year must match.
Kansas rows are re-parsed from the same raw evidence the Kansas tables use;
Missouri raw files carry a ``metro-`` prefix so Kansas parsers never read them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from data_contracts import normalize_observations, validate_observations
from demand_pressure import quarterly_changes
from fetch_acs import parse_acs_supply
from fetch_laus import parse_laus_monthly
from fetch_qcew import _SECTOR_NAICS, parse_quarterly_qcew
from geography import KANSAS_CITY_OEWS_COUNTIES

MISSOURI_KC_COUNTIES = ("29013", "29025", "29037", "29047", "29049", "29095", "29107", "29165", "29177")
METRO_COUNTIES = tuple(KANSAS_CITY_OEWS_COUNTIES) + MISSOURI_KC_COUNTIES
METRO_INDUSTRIES = sorted(_SECTOR_NAICS) + ["10"]


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _names(payloads) -> dict[str, str]:
    names = {}
    for payload in payloads:
        head = payload[0]
        for row in payload[1:]:
            names[row[head.index("state")] + row[head.index("county")]] = row[head.index("NAME")]
    return names


FORECAST_YEARS = (2026, 2035)


def forecast_rows(outputs: Path) -> pd.DataFrame:
    """Median working-age (18-64) forecasts for the metro counties from both states' runs."""
    frames = []
    for state in ("20", "29"):
        proj = pd.read_parquet(Path(outputs) / f"projections_s{state}.parquet")
        proj["geography_id"] = state + proj.county_fips.astype(str).str.zfill(3)
        frames.append(proj[proj.geography_id.isin(METRO_COUNTIES)])
    proj = pd.concat(frames, ignore_index=True)
    for col in ("random_seed", "base_year"):
        if proj[col].nunique() != 1:
            raise ValueError(f"Kansas and Missouri forecasts differ in {col}; re-run both before comparing")
    rows = []
    start, end = FORECAST_YEARS
    for county, group in proj.groupby("geography_id"):
        by_year = group.set_index("year")
        for year in FORECAST_YEARS:
            rows.append(dict(geography_id=county, measure_id="working_age_forecast_p50", estimate=float(by_year.loc[year, "p50"]),
                             unit="people", period_start=f"{year}-01-01", period_end=f"{year}-12-31", reference_label=f"{year} median forecast"))
        rows.append(dict(geography_id=county, measure_id="working_age_forecast_change_pct",
                         estimate=100 * (by_year.loc[end, "p50"] / by_year.loc[start, "p50"] - 1), unit="percent",
                         period_start=f"{start}-01-01", period_end=f"{end}-12-31", reference_label=f"{start} to {end}, median forecasts"))
    seed, base = int(proj.random_seed.iloc[0]), int(proj.base_year.iloc[0])
    return normalize_observations(pd.DataFrame(rows), source_id="cohort_forecast", defaults=dict(
        location_basis="residence", age_group="18-64", frequency="annual", method_type="model forecast (median of simulations)",
        release_id=f"cohort-model seed {seed}, ACS base {base}",
        coverage_note="Cohort-component model, ACS 2020-2024 base, youth cohorts updated with NCES CCD single-grade enrollment 2022 to 2024 in both states."))


def build_metro_table(evidence_dir: Path, outputs: Path | None = None) -> pd.DataFrame:
    evidence_dir = Path(evidence_dir)
    acs = {("ks", "b23025"): "acs-2024-b23025-ks.json", ("ks", "s2301"): "acs-2024-s2301-ks.json",
           ("mo", "b23025"): "metro-acs-2024-b23025-mo.json", ("mo", "s2301"): "metro-acs-2024-s2301-mo.json"}
    missing = [name for name in acs.values() if not (evidence_dir / name).exists()]
    if missing:
        raise FileNotFoundError("Kansas City metro evidence missing: " + ", ".join(missing))
    payloads = {key: _read(evidence_dir / name) for key, name in acs.items()}
    pieces = []
    for (state, table), payload in payloads.items():
        frame = parse_acs_supply(payload, 2024, table.upper())
        pieces.append(normalize_observations(frame, source_id=frame.source_id.iloc[0]))
    for path, state in ((evidence_dir / "laus-kansas-2026.json", "20"), (evidence_dir / "metro-laus-mo-2026.json", "29")):
        frame = parse_laus_monthly(_read(path), state)
        pieces.append(normalize_observations(frame, source_id="laus"))
    qcew = []
    for county in METRO_COUNTIES:
        prefix = "qcew" if county.startswith("20") else "metro-qcew"
        for year in (2025, 2026):
            path = evidence_dir / f"{prefix}-{county}-{year}q1.csv"
            if not path.exists():
                raise FileNotFoundError(f"Missing QCEW slice {path.name}")
            qcew.append(parse_quarterly_qcew(pd.read_csv(path, dtype=str), county_fips=county, year=year,
                                             quarter=1, industries=METRO_INDUSTRIES))
    qcew = pd.concat(qcew, ignore_index=True)
    pieces += [qcew, quarterly_changes(qcew)]
    if outputs is not None:
        pieces.append(forecast_rows(outputs))
    table = pd.concat(pieces, ignore_index=True)
    table = table[table.geography_id.isin(METRO_COUNTIES)].copy()
    names = _names([payloads[("ks", "b23025")], payloads[("mo", "b23025")]])
    table["county_name"] = table.geography_id.map(names).fillna("")
    table["state_fips"] = table.geography_id.str[:2]
    table["metro_area"] = "Kansas City, MO-KS (OEWS 28140)"
    for col in table.select_dtypes(include="object"):
        table[col] = table[col].astype("string")
    if set(table.geography_id) != set(METRO_COUNTIES):
        raise ValueError("Metro table does not cover all 14 counties")
    errors = validate_observations(table)
    if errors:
        raise ValueError("Metro table: " + "; ".join(errors))
    return table.reset_index(drop=True)
