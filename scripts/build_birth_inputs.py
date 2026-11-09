"""Build Kansas county birth inputs from Census Vintage 2025, without modeling.

Download original public sources, run the vault's shared preprocess.py on both,
then normalize a streaming STATE=20/SUMLEV=050 slice. No other outputs change.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import requests

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "data" / "demographic_inputs"
CSV_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv"
LAYOUT_URL = "https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2020-2025/CO-EST2025-ALLDATA.pdf"
CSV_NAME = "births_co-est2025-alldata.csv"
UTF8_NAME = "births_co-est2025-alldata.utf8.csv"
LAYOUT_NAME = "births_co-est2025-layout.pdf"
MANIFEST_NAME = "births_manifest.json"
OUTPUT_NAME = "births_county_2021_2025.json"
YEARS = tuple(range(2021, 2026))
HISTORICAL_SOURCES = {
    2010: {"csv": "https://www2.census.gov/programs-surveys/popest/datasets/2010/2010-eval-estimates/co-est2010-alldata.csv",
           "layout": "https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2000-2010/co-est2010-alldata.pdf",
           "years": tuple(range(2006, 2011)), "series_type": "Vintage 2010 evaluation estimates; current corrected archive"},
    2020: {"csv": "https://www2.census.gov/programs-surveys/popest/datasets/2010-2020/counties/totals/co-est2020-alldata.csv",
           "layout": "https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2010-2020/co-est2020-alldata.pdf",
           "years": tuple(range(2011, 2021)), "series_type": "Vintage 2020 population estimates"},
    2025: {"csv": CSV_URL, "layout": LAYOUT_URL, "years": YEARS, "series_type": "Vintage 2025 population estimates"},
}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_inputs(destination: Path = DESTINATION) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "source_agency": "U.S. Census Bureau, Population Division",
                "source_product": "CO-EST2025-ALLDATA", "vintage": 2025, "release_month": "2026-03",
                "source_kind": "Census estimated births, not unprocessed birth-registry records", "sources": {}}
    for name, url in ((CSV_NAME, CSV_URL), (LAYOUT_NAME, LAYOUT_URL)):
        try:
            response = requests.get(url, timeout=120)
        except requests.RequestException:
            raise RuntimeError("Official Census source download failed") from None
        if response.status_code != 200:
            raise RuntimeError(f"Official Census source returned HTTP {response.status_code}")
        if name.endswith(".pdf") and not response.content.startswith(b"%PDF"):
            raise ValueError("Layout source is not a PDF response")
        if name.endswith(".csv") and b"SUMLEV" not in response.content[:200]:
            raise ValueError("Birth source response lacks its expected CSV header")
        (destination / name).write_bytes(response.content)
        if name == CSV_NAME:
            # The raw Census file contains Latin-1 county names. A reversible
            # encoding copy lets the shared preprocessor handle them reliably.
            (destination / UTF8_NAME).write_text(response.content.decode("latin-1"), encoding="utf-8-sig", newline="")
        manifest["sources"][name] = {"source_url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                                     "sha256": sha256(destination / name), "bytes": len(response.content),
                                     "http_last_modified": response.headers.get("Last-Modified")}
        write_json(destination / MANIFEST_NAME, manifest)
        print(f"Saved {name}")
    return manifest


def verify_layout(companion: Path) -> None:
    text = companion.read_text(encoding="utf-8-sig")
    if LAYOUT_NAME not in text or "BIRTHS2025" not in text:
        raise ValueError("Supply the shared preprocessor's companion for the downloaded layout")
    compact = re.sub(r"\s+", " ", text)
    for year in YEARS:
        pattern = rf"BIRTHS{year}.{{0,100}}7/1/{year-1}.{{0,40}}6/30/{year}"
        if not re.search(pattern, compact):
            raise ValueError(f"Official layout did not confirm the annual interval for BIRTHS{year}")
    if not re.search(r"BIRTHS2020.{0,100}4/1/2020.{0,40}6/30/2020", compact):
        raise ValueError("Official layout did not confirm partial-year BIRTHS2020 exclusion")


def normalize_inputs(csv_companion: Path, layout_companion: Path,
                     destination: Path = DESTINATION) -> dict:
    manifest = json.loads((destination / MANIFEST_NAME).read_text(encoding="utf-8"))
    for name in (CSV_NAME, LAYOUT_NAME):
        if sha256(destination / name) != manifest["sources"][name]["sha256"]:
            raise ValueError("Original source hash mismatch: " + name)
    preprocessing_header = csv_companion.read_text(encoding="utf-8-sig")[:4000]
    if not any(name in preprocessing_header for name in (CSV_NAME, UTF8_NAME)) or "extraction_status: failed" in preprocessing_header:
        raise ValueError("Supply the shared preprocessor's profile/companion for the original CSV")
    if UTF8_NAME in preprocessing_header and (destination / UTF8_NAME).read_bytes().decode("utf-8-sig").encode("latin-1") != (destination / CSV_NAME).read_bytes():
        raise ValueError("UTF8 preprocessing copy does not match original Census bytes")
    verify_layout(layout_companion)
    rows = []
    county_ids = set()
    # Census CSV includes accented names outside the requested Kansas slice.
    # Latin-1 decoding is lossless; selected Kansas names are ASCII.
    with (destination / CSV_NAME).open(encoding="latin-1", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        if len(set(headers)) != len(headers):
            raise ValueError("Duplicate source column names")
        required = {"SUMLEV", "STATE", "COUNTY", "CTYNAME", *(f"BIRTHS{year}" for year in YEARS)}
        if not required.issubset(headers):
            raise ValueError("Census county births schema changed")
        available_years = sorted(int(match[1]) for name in headers if (match := re.fullmatch(r"BIRTHS(\d{4})", name)))
        if available_years[-5:] != list(YEARS):
            raise ValueError("Reviewed years are no longer the latest five annual birth fields")
        for row in reader:
            if row["STATE"].zfill(2) != "20" or row["SUMLEV"].zfill(3) != "050":
                continue
            fips = row["STATE"].zfill(2) + row["COUNTY"].zfill(3)
            if fips in county_ids:
                raise ValueError("Duplicate Kansas county source row")
            county_ids.add(fips)
            for year in YEARS:
                value = row[f"BIRTHS{year}"].strip()
                if not re.fullmatch(r"\d+", value):
                    raise ValueError(f"Missing or invalid estimated births for {fips}/{year}")
                rows.append({"county_fips": fips, "county_name": row["CTYNAME"], "year": year,
                             "births": int(value), "period_start": f"{year-1}-07-01", "period_end": f"{year}-06-30",
                             "source_variable": f"BIRTHS{year}", "vintage": 2025,
                             "availability_status": "published", "unit": "estimated resident births"})
    if len(county_ids) != 105 or any(not re.fullmatch(r"20\d{3}", fips) for fips in county_ids) or len(rows) != 525:
        raise ValueError("Expected 105 Kansas counties and 525 complete county-year estimates")
    rows.sort(key=lambda row: (row["county_fips"], row["year"]))
    write_json(destination / OUTPUT_NAME, rows)
    manifest.update({"normalized_file": OUTPUT_NAME, "normalized_sha256": sha256(destination / OUTPUT_NAME),
                     "preprocessing": {"tool": "vault shared preprocess.py / MarkItDown",
                                       "csv_companion_sha256": sha256(csv_companion), "layout_companion_sha256": sha256(layout_companion),
                                       "encoding_copy": UTF8_NAME, "encoding_copy_sha256": sha256(destination / UTF8_NAME),
                                       "note": "Original Latin-1 CSV conversion failed with ASCII decoding. Reversible UTF8-BOM copy converted successfully; raw bytes retained. Companion truncation is not a data slice; normalization streams the full original CSV."},
                     "periods": {str(year): {"source_variable": f"BIRTHS{year}", "start": f"{year-1}-07-01", "end": f"{year}-06-30"} for year in YEARS},
                     "excluded_period": {"source_variable": "BIRTHS2020", "start": "2020-04-01", "end": "2020-06-30", "reason": "Partial three-month interval"},
                     "selection": "Streaming CSV; STATE=20 and SUMLEV=050 only; birth counts BIRTHS2021 through BIRTHS2025",
                     "validation": {"counties": 105, "county_year_rows": len(rows), "years": list(YEARS),
                                    "missing_values": 0, "negative_values": 0, "duplicate_county_years": 0,
                                    "kansas_county_sums": {str(year): sum(r["births"] for r in rows if r["year"] == year) for year in YEARS},
                                    "zero_birth_cells": sum(r["births"] == 0 for r in rows)},
                     "interpretation": ["Years label July–June annual intervals ending in that year, not calendar years.",
                                        "Values are Census population-estimates components, not unprocessed vital-registration counts.",
                                        "All five annual estimates share Vintage2025; revisions must not be mixed across vintages.",
                                        "County of residence estimates do not count births occurring at local hospitals."]})
    write_json(destination / MANIFEST_NAME, manifest)
    return manifest["validation"]


def download_history(destination: Path = DESTINATION) -> dict:
    """Add historical sources without changing the completed five-year bundle."""
    original = json.loads((destination / MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest = {"schema_version": 1, "source_agency": original["source_agency"], "years": list(range(2006, 2026)),
                "source_kind": original["source_kind"], "sources": dict(original["sources"]),
                "correction_notice_url": "https://www.census.gov/programs-surveys/popest/technical-documentation/research/evaluation-estimates.2010.html",
                "correction_notice": "Original March29,2012 CO-EST2010-ALLDATA had incorrect2009–2010 components; Census replaced it April6,2012. Current linked archive downloaded.",
                "vintage_segments": {str(v): {"years": list(spec["years"]), "series_type": spec["series_type"]} for v, spec in HISTORICAL_SOURCES.items()}}
    for vintage in (2010, 2020):
        for kind, extension in (("csv", "csv"), ("layout", "pdf")):
            url = HISTORICAL_SOURCES[vintage][kind]
            name = f"births_co-est{vintage}-{'alldata' if kind == 'csv' else 'layout'}.{extension}"
            try:
                response = requests.get(url, timeout=120)
            except requests.RequestException:
                raise RuntimeError("Official historical Census download failed") from None
            if response.status_code != 200:
                raise RuntimeError(f"Official historical source returned HTTP {response.status_code}")
            if extension == "pdf" and not response.content.startswith(b"%PDF"):
                raise ValueError("Historical layout is not a PDF")
            if extension == "csv" and b"SUMLEV" not in response.content[:200]:
                raise ValueError("Historical source lacks expected CSV header")
            (destination / name).write_bytes(response.content)
            if extension == "csv":
                (destination / name.replace(".csv", ".utf8.csv")).write_text(response.content.decode("latin-1"), encoding="utf-8-sig", newline="")
            manifest["sources"][name] = {"source_url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                                         "sha256": sha256(destination / name), "bytes": len(response.content),
                                         "http_last_modified": response.headers.get("Last-Modified")}
            write_json(destination / "births_manifest_20yr.json", manifest)
            print(f"Saved {name}")
    return manifest


def normalize_history(companion_dir: Path, destination: Path = DESTINATION) -> dict:
    manifest = json.loads((destination / "births_manifest_20yr.json").read_text(encoding="utf-8"))
    first_five_hash = sha256(destination / OUTPUT_NAME)
    rows = []
    rate_differences = []
    for vintage, spec in HISTORICAL_SOURCES.items():
        csv_name, layout_name = f"births_co-est{vintage}-alldata.csv", f"births_co-est{vintage}-layout.pdf"
        for name in (csv_name, layout_name):
            if sha256(destination / name) != manifest["sources"][name]["sha256"]:
                raise ValueError("Historical source hash mismatch: " + name)
        copy_name = csv_name.replace(".csv", ".utf8.csv")
        profile = companion_dir / (copy_name + ".md")
        text = profile.read_text(encoding="utf-8-sig")
        if copy_name not in text[:4000] or "extraction_status: failed" in text[:4000]:
            raise ValueError("Missing successful historical CSV preprocessing")
        if (destination / copy_name).read_bytes().decode("utf-8-sig").encode("latin-1") != (destination / csv_name).read_bytes():
            raise ValueError("Historical encoding copy does not reverse to source")
        # Shared page-preserving output keeps the2010 PDF's columns paired.
        layout = re.sub(r"\s+", " ", (companion_dir / (layout_name + ".pages.md")).read_text(encoding="utf-8-sig"))
        for year in spec["years"]:
            if not re.search(rf"BIRTHS{year}.{{0,100}}7/1/{year-1}.{{0,40}}6/30/{year}", layout):
                raise ValueError(f"Cannot verify full annual birth period {vintage}/{year}")
            for population_year in (year - 1, year):
                if not re.search(rf"POPESTIMATE{population_year}.{{0,60}}7/1/{population_year}", layout):
                    raise ValueError("Population exposure dates not verified in the same source vintage")
        selected = set()
        with (destination / csv_name).open(encoding="latin-1", newline="") as handle:
            reader = csv.DictReader(handle)
            for source in reader:
                if source["STATE"].zfill(2) != "20" or source["SUMLEV"].zfill(3) != "050":
                    continue
                county = "20" + source["COUNTY"].zfill(3)
                if county in selected:
                    raise ValueError("Duplicate historical county row")
                selected.add(county)
                for year in spec["years"]:
                    fields = [source[f"BIRTHS{year}"], source[f"POPESTIMATE{year-1}"], source[f"POPESTIMATE{year}"]]
                    if not all(re.fullmatch(r"\d+", value.strip()) for value in fields):
                        raise ValueError("Invalid historical birth count or population denominator")
                    births, prior, current = map(int, fields)
                    exposure = (prior + current) / 2
                    if exposure <= 0:
                        raise ValueError("Nonpositive population exposure")
                    rate = births / exposure * 1000
                    published_rate = float(source[f"RBIRTH{year}"])
                    difference = abs(rate - published_rate)
                    if not 0 <= difference < 0.000001:
                        raise ValueError("Derived crude birth rate disagrees with published Census rate")
                    rate_differences.append(difference)
                    rows.append({"county_fips": county, "county_name": source["CTYNAME"], "year": year, "births": births,
                                 "period_start": f"{year-1}-07-01", "period_end": f"{year}-06-30", "vintage": vintage,
                                 "source_variable": f"BIRTHS{year}", "source_series_type": spec["series_type"],
                                 "source_url": spec["csv"], "availability_status": "published",
                                 "population_prior_july1": prior, "population_current_july1": current,
                                 "population_exposure": exposure, "rate_per_1000": rate,
                                 "published_rate_per_1000": published_rate, "source_rate_variable": f"RBIRTH{year}",
                                 "rate_method": "Derived crude birth rate: births / mean of same-vintage prior and current July1 population *1000",
                                 "vintage_boundary": year in (2011, 2021)})
        if len(selected) != 105:
            raise ValueError(f"Incomplete Kansas county coverage in vintage{vintage}")
    rows.sort(key=lambda row: (row["county_fips"], row["year"]))
    if len(rows) != 2100 or len({(r["county_fips"], r["year"]) for r in rows}) != 2100:
        raise ValueError("Expected2100 unique county-year observations")
    for county in {r["county_fips"] for r in rows}:
        if [r["year"] for r in rows if r["county_fips"] == county] != list(range(2006, 2026)):
            raise ValueError("Historical county has an annual gap")
    output = "births_county_2006_2025.json"
    write_json(destination / output, rows)
    manifest.update({"normalized_file": output, "normalized_sha256": sha256(destination / output),
                     "population_exposure_method": "Arithmetic mean of previous and current July1 resident populations, using the same vintage as each birth count",
                     "period_definition": "July1 of preceding year through June30 of named year; no partial April–June observations",
                     "boundary_warnings": ["Three source vintages are joined, not one harmonized intercensal series. Population denominators and estimation assumptions may shift at2011 and2021.",
                                           "Vintage2010 evaluation estimates are not intercensal estimates. Current archive incorporates the documented April6,2012 correction.",
                                           "Retrospective validation uses revised historical estimates, not data available in real time at each forecast origin.",
                                           "Derived rate is a crude birth rate per all residents, not fertility per women of reproductive age."],
                     "validation": {"counties": 105, "county_year_rows": len(rows), "years": list(range(2006, 2026)),
                                    "missing_births_or_exposures": 0, "duplicate_county_years": 0,
                                    "published_rate_matches": len(rate_differences), "max_rate_rounding_difference": max(rate_differences),
                                    "zero_birth_cells": sum(r["births"] == 0 for r in rows),
                                    "kansas_county_sums": {str(year): sum(r["births"] for r in rows if r["year"] == year) for year in range(2006, 2026)},
                                    "original_five_year_file_unchanged": sha256(destination / OUTPUT_NAME) == first_five_hash}})
    write_json(destination / "births_manifest_20yr.json", manifest)
    return manifest["validation"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--normalize-only", action="store_true")
    parser.add_argument("--csv-companion", type=Path)
    parser.add_argument("--layout-companion", type=Path)
    parser.add_argument("--history20", action="store_true", help="Build2006–2025 without replacing the five-year bundle")
    parser.add_argument("--companion-dir", type=Path, help="Shared preprocessing companion directory for all source vintages")
    args = parser.parse_args()
    if args.history20:
        if not args.normalize_only:
            download_history()
        if not args.download_only:
            if args.companion_dir is None:
                parser.error("Preprocess historical sources, then supply --companion-dir with --normalize-only --history20")
            print(json.dumps(normalize_history(args.companion_dir)))
        return
    if not args.normalize_only:
        download_inputs()
    if not args.download_only:
        if args.csv_companion is None or args.layout_companion is None:
            parser.error("Preprocess both downloaded sources, then supply --csv-companion and --layout-companion with --normalize-only")
        print(json.dumps(normalize_inputs(args.csv_companion, args.layout_companion)))


if __name__ == "__main__":
    main()
