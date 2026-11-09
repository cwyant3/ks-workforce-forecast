"""Acquire vintage-specific ACS age/sex inputs for fertility-model comparisons.

Only independent public county aggregates are fetched. Census keys are local,
never included in provenance URLs or exception messages. Each ending year is a
rolling five-year estimate, not an exact annual exposure or real-time backtest.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.build_demographic_inputs import age_limits, provenance, safe_get, write_json

DESTINATION = ROOT / "data/demographic_inputs"
YEARS = range(2010, 2025)
TARGET_BANDS = [(age, age + 4) for age in range(10, 55, 5)]
NATIVE_BANDS = [(0, 4), (5, 9), (10, 14), (15, 17), (18, 19), (20, 20),
                (21, 21), (22, 24), (25, 29), (30, 34), (35, 39), (40, 44),
                (45, 49), (50, 54), (55, 59), (60, 61), (62, 64), (65, 66),
                (67, 69), (70, 74), (75, 79), (80, 84), (85, None)]
EXPOSURE_BASIS = "Rolling ACS five-year resident estimate; not exact annual female exposure"


def _credential():
    key = os.getenv("CENSUS_API_KEY")
    config = ROOT / ".env"
    if not key and config.is_file():
        for line in config.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("CENSUS_API_KEY="):
                key = line.strip().split("=", 1)[1].strip()
                if len(key) >= 2 and key[0] == key[-1] and key[0] in {"'", '"'}:
                    key = key[1:-1]
                break
    return key


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_labels(payload):
    """Verify every vintage against its own official labels, not future metadata."""
    variables = payload.get("variables", {})
    mapping = {}
    for sex, start in (("male", 3), ("female", 27)):
        for offset, bounds in enumerate(NATIVE_BANDS):
            name = f"B01001_{start + offset:03d}E"
            metadata = variables.get(name, {})
            parts = [part.strip().rstrip(":") for part in metadata.get("label", "").split("!!")]
            # Earlier API labels omit the leading Estimate component.
            if parts and parts[0].lower() == "estimate":
                parts = parts[1:]
            if len(parts) != 3 or parts[0].lower() != "total" or parts[1].lower() != sex:
                raise ValueError(f"Unexpected B01001 age-sex label for {name}")
            if age_limits(parts[2]) != bounds:
                raise ValueError(f"B01001 age bounds changed for {name}")
            mapping[(sex, *bounds)] = name
    return mapping


def _number(value):
    if value in (None, "", "null"):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def normalize_year(payload, label_payload, year):
    mapping = verify_labels(label_payload)
    if not isinstance(payload, list) or len(payload) != 106 or len(set(payload[0])) != len(payload[0]):
        raise ValueError(f"Expected 105 uniquely headed county records for ACS {year}")
    records = [dict(zip(payload[0], row, strict=True)) for row in payload[1:]]
    codes = [row["state"] + row["county"] for row in records]
    expected = {f"20{n:03d}" for n in range(1, 210, 2)}
    if set(codes) != expected or len(set(codes)) != 105:
        raise ValueError(f"ACS {year} county coverage differs from the 105 Kansas counties")
    normalized, native = [], []
    for record, county in sorted(zip(records, codes), key=lambda pair: pair[1]):
        sex_totals = []
        for sex, total_variable in (("male", "B01001_002E"), ("female", "B01001_026E")):
            counts = []
            for low, high in NATIVE_BANDS:
                variable = mapping[(sex, low, high)]
                number = _number(record.get(variable))
                counts.append(number)
                native.append(dict(county_fips=county, year=year, sex=sex, age_low=low,
                                   age_high=high, population=number, source_variable=variable,
                                   availability_status="published" if number is not None else "not_reported"))
            total = _number(record.get(total_variable))
            if all(value is not None for value in counts) and total is not None and sum(counts) != total:
                raise ValueError(f"ACS {year} age bands do not reconcile to {sex} total in {county}")
            sex_totals.append(total)
        if all(value is not None for value in sex_totals) and sum(sex_totals) != _number(record.get("B01001_001E")):
            raise ValueError(f"ACS {year} sex totals do not reconcile in {county}")
        for low, high in TARGET_BANDS:
            components = [(a, b, mapping[("female", a, b)]) for a, b in NATIVE_BANDS
                          if b is not None and a >= low and b <= high]
            if sum(b-a+1 for a, b, _ in components) != high-low+1:
                raise ValueError("Target female bands require unsupported age splitting")
            values = [_number(record.get(variable)) for _, _, variable in components]
            estimate = sum(values) if all(value is not None for value in values) else None
            normalized.append(dict(county_fips=county, year=year, age_low=low, age_high=high,
                female_population=estimate, availability_status="published" if estimate is not None else "not_reported",
                period_start=f"{year-4}-01-01", period_end=f"{year}-12-31",
                source_variables=[variable for _, _, variable in components],
                source_url=f"https://api.census.gov/data/{year}/acs/acs5",
                exposure_basis=EXPOSURE_BASIS))
    return normalized, native


def build(destination=DESTINATION, *, download=True):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "female_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"sources": {}}
    manifest.update(schema_version=1, acs_product="ACS 5-year B01001", geography="105 Kansas counties",
        year_meaning="Ending year of the ACS five-year period", years=list(YEARS),
        exposure_basis=EXPOSURE_BASIS,
        caveats=["Adjacent vintages overlap four years; these are not independent annual population measurements.",
                 "Historical vintages were retrieved now; this is not a real-time availability or publication-date backtest.",
                 "For pooled 2020–2024 births, ACS2024 female estimate ×5 is an approximate period-aligned person-year denominator.",
                 "Summing ACS2020…2024 female estimates blends overlapping 2016–2024 windows, not exact annual exposure.",
                 "Age grouping sums exact source bands; no age splitting or future-vintage labels are used.",
                 "Sampling uncertainty is not propagated into the derived exposure counts; original MOEs remain in source responses."],
        items_requiring_verification=["[VERIFY] Suitability of rolling ACS exposure proxies for county age-specific fertility estimation remains a model assumption."])
    key = _credential() if download else None
    normalized, native = [], []
    with requests.Session() as session:
        for year in YEARS:
            files = {}
            for kind in ("labels", "response"):
                name = f"female_acs{year}_B01001_{kind}.json"
                path = destination / name
                url = f"https://api.census.gov/data/{year}/acs/acs5"
                params = {"get": "group(B01001)", "for": "county:*", "in": "state:20"}
                if kind == "labels":
                    url += "/groups/B01001.json"
                    params = {}
                public_url = url + ("?" + urlencode(params) if params else "")
                prior = manifest["sources"].get(name, {})
                if path.exists():
                    if not prior or _hash(path) != prior.get("sha256"):
                        raise ValueError(f"Unverified cached input or changed hash: {name}")
                elif year == 2024:
                    original = destination / f"acs2024_5yr_B01001_{kind}.json"
                    original_manifest = json.loads((destination / "source_manifest.json").read_text())
                    original_source = original_manifest["sources"][original.name]
                    if _hash(original) != original_source["sha256"]:
                        raise ValueError("Previously verified ACS2024 input changed")
                    path.write_bytes(original.read_bytes())
                    manifest["sources"][name] = {**original_source, "reused_from": original.name}
                else:
                    if not download:
                        raise ValueError(f"Missing original input: {name}")
                    query = dict(params)
                    if key and kind == "response":
                        query["key"] = key
                    response = safe_get(session, url, query, json_expected=True)
                    path.write_bytes(response.content)
                    manifest["sources"][name] = provenance(response.content, public_url)
                files[kind] = json.loads(path.read_text(encoding="utf-8"))
                write_json(manifest_path, manifest)
            result, native_result = normalize_year(files["response"], files["labels"], year)
            normalized.extend(result)
            native.extend(native_result)
            print(f"ACS {year}: verified 105 counties, native age-sex labels and nine female bands", flush=True)
    if len(normalized) != 105 * 15 * 9 or len(native) != 105 * 15 * 46:
        raise ValueError("Normalized history does not have complete county/year/band coverage")
    outputs = {"female_history_2010_2024.json": normalized,
               "female_native_age_sex_history_2010_2024.json": native}
    for name, rows in outputs.items():
        write_json(destination / name, rows)
    manifest["outputs"] = {name: {"sha256": _hash(destination/name), "rows": len(rows)} for name, rows in outputs.items()}
    manifest["normalized_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["validation"] = {"county_count":105, "years":15, "female_bands_per_county_year":9,
        "native_age_sex_bands_per_county_year":46,
        "unavailable_female_cells":sum(row["female_population"] is None for row in normalized),
        "unavailable_native_cells":sum(row["population"] is None for row in native),
        "zero_female_cells":sum(row["female_population"] == 0 for row in normalized),
        "checks":["Same-vintage official age/sex labels", "All 105 counties each year", "Exact source-band sums",
                  "Native age sums reconcile to sex totals", "Sex totals reconcile to county population",
                  "Cached original SHA256 verified before reuse"]}
    write_json(manifest_path, manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalize-only", action="store_true")
    args = parser.parse_args()
    result = build(download=not args.normalize_only)
    print(json.dumps(result["validation"]))
