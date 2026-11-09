"""Normalize official Census NP2023_A1 GROUP 0 schedules; never fit county rates.

Run shared vault preprocess.py on the source CSV before using this script.
Rates remain on the original July-June period basis and births-per-woman scale.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path

SOURCE_URL = "https://www2.census.gov/programs-surveys/popproj/datasets/2023/2023-popproj/np2023_a1.csv"
LAYOUT_URL = "https://www2.census.gov/programs-surveys/popproj/technical-documentation/file-layouts/2023/np2023_a1.pdf"
METHOD_URL = "https://www2.census.gov/programs-surveys/popproj/technical-documentation/methodology/methodstatement23.pdf"
YEARS = tuple(range(2023, 2101))
AGES = tuple(range(14, 55))


def normalize_rates(body: bytes) -> list[dict]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    required = {"GROUP", "YEAR", *(f"ASFR_{age}" for age in AGES)}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("NP2023_A1 missing required fields")
    selected = {}
    for row in reader:
        if row["GROUP"].strip() != "0":
            continue
        year = int(row["YEAR"])
        if year in selected:
            raise ValueError(f"Duplicate total-group year: {year}")
        values = [float(row[f"ASFR_{age}"]) for age in AGES]
        if not all(0 <= value <= 1 for value in values):
            raise ValueError(f"Invalid births-per-woman rate: {year}")
        selected[year] = values
    if set(selected) != set(YEARS):
        raise ValueError("Total group must cover exactly 2023-2100")
    return [{"year": year, "age": age, "rate": rate}
            for year in YEARS for age, rate in zip(AGES, selected[year])]


def build(source: Path, destination: Path, retrieved: str) -> dict:
    body = source.read_bytes()
    rates = normalize_rates(body)
    destination.mkdir(parents=True, exist_ok=True)
    raw = destination / "census_np2023_a1.csv"
    normalized = destination / "census_fertility_rates.json"
    raw.write_bytes(body)
    normalized.write_text(json.dumps(rates, indent=2)+"\n", encoding="utf-8")
    manifest = {
        "schema_version": 1, "source_id": "census_np2023_a1",
        "source_url": SOURCE_URL, "layout_url": LAYOUT_URL,
        "methodology_url": METHOD_URL, "retrieved": retrieved,
        "release_label": "2023 National Population Projections, November 2023",
        "source_file": raw.name, "source_sha256": hashlib.sha256(body).hexdigest(),
        "normalized_file": normalized.name,
        "normalized_sha256": hashlib.sha256(normalized.read_bytes()).hexdigest(),
        "group": 0, "group_label": "Total", "geography": "United States",
        "unit": "births per woman-year", "rate_multiplier": 1,
        "year_start": 2023, "year_end": 2100, "age_start": 14, "age_end": 54,
        "year_count": len(YEARS), "age_count": len(AGES), "record_count": len(rates),
        "period_basis": "YEAR y means July 1 of y-1 through June 30 of y",
        "calendar_alignment": "If using calendar-year KDHE births, a half-weight average of source years y and y+1 is an analyst approximation. Preserve original periods here.",
        "validation": {"complete_year_age_grid": len(rates)==78*41,
                       "duplicate_total_group_years": 0, "rates_between_zero_and_one": True},
        "method": "Select published total group only; unpivot ASFR_14 through ASFR_54 without rounding, interpolation, rescaling or race/nativity weighting.",
        "limitations": [
            "National proxy, not Kansas or county official fertility forecast.",
            "Census main/high/low/zero immigration variants use the same fertility inputs; analyst fertility sensitivities are not Census scenarios or confidence intervals.",
            "Published schedules use rounded rates. Zero-rate ages require explicit denominator handling if forming relative changes.",
            "The schedule was released in November 2023 and is future information for earlier forecast origins.",
            "ACS five-year female age counts are period estimates, not annual midyear exposure; local calibration must label this approximation."
        ],
    }
    (destination/"census_fertility_manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1]/"data/demographic_inputs")
    parser.add_argument("--retrieved", default=date.today().isoformat())
    args = parser.parse_args()
    result = build(args.source, args.output_dir, args.retrieved)
    print(json.dumps({key: result[key] for key in ["source_sha256", "normalized_sha256", "record_count", "year_count", "age_count", "validation"]}))
