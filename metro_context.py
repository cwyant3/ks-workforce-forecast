"""Kansas City metro context: all 14 counties of OEWS area 28140 from matching federal sources.

The table compares Kansas and Missouri counties with the same observed
sources and periods (ACS 2020-2024 5-year, BLS LAUS monthly, QCEW quarterly
private jobs). Model forecasts are deliberately excluded: both states run the
same cohort model (re-runs on 2026-11-10 reproduced both forecasts), but only
Kansas updates youth cohorts with KSDE single-grade enrollment.
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


def build_metro_table(evidence_dir: Path) -> pd.DataFrame:
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
