"""Stage the reviewed Census PEP source, never adopt or rebuild forecasts.

Run vault preprocess.py on the CSV before using this builder. Every source row
is validated; only YEAR=7 (July 2025) is selected for the preview.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

SOURCE_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/asrh/cc-est2025-syasex-20.csv"
LAYOUT_URL = "https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2020-2025/CC-EST2025-SYASEX.pdf"


def normalize_population(body, expected_counties):
    cells, names = {}, {}
    for row in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))):
        if row["SUMLEV"] != "050" or row["STATE"] != "20":
            raise ValueError("Only Kansas county records are supported.")
        county = row["STATE"]+row["COUNTY"].zfill(3)
        year, age = int(row["YEAR"]), int(row["AGE"])
        key = county, year, age
        if key in cells:
            raise ValueError("Duplicate county/year/age key.")
        total, male, female = (int(row[k]) for k in ("TOT_POP", "TOT_MALE", "TOT_FEMALE"))
        if min(total, male, female) < 0 or male+female != total:
            raise ValueError("Population counts must be nonnegative and reconcile by sex.")
        cells[key] = [male, female]
        names[county] = row["CTYNAME"]
    expected = {(c, y, a) for c in expected_counties for y in range(1, 8) for a in range(86)}
    if len(expected_counties) != 105 or set(cells) != expected:
        raise ValueError("Complete 105-county, seven-year, age0–84/85+ coverage is required.")
    return {"reference_date": "2025-07-01", "year_code": 7,
            "sex": ["male", "female"], "ages": list(range(85))+["85+"],
            "counties": {c: {"name": names[c], "population":
                         [[cells[c, 7, a][sex] for a in range(86)] for sex in range(2)]}
                         for c in sorted(expected_counties)}}


def build(source, output, acs_file):
    acs = json.loads(acs_file.read_text(encoding="utf-8"))
    records = [dict(zip(acs[0], row)) for row in acs[1:]]
    counties = {r["state"]+r["county"] for r in records}
    body = source.read_bytes()
    normalized = normalize_population(body, counties)
    output.mkdir(parents=True, exist_ok=True)
    raw = output/"cc-est2025-syasex-20.csv"
    raw.write_bytes(body)
    target = output/"population_july2025.json"
    target.write_text(json.dumps(normalized, indent=2)+"\n", encoding="utf-8", newline="\n")
    manifest = {"status": "preview-not-adopted", "reference_date": "2025-07-01",
                "source_url": SOURCE_URL, "layout_url": LAYOUT_URL,
                "source_file": raw.name, "source_sha256": hashlib.sha256(body).hexdigest(),
                "normalized_file": target.name, "normalized_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "source_rows_checked": 105*7*86, "counties": 105,
                "selection": "All source rows validated; select YEAR=7, ages0–84 and published85+.",
                "boundaries": "January 1, 2025", "acquired": "2026-11-12",
                "limitations": ["Population estimates, not direct census counts.",
                                "85+ single-age split is modeled using national life-table lx.",
                                "ACS2020–2024 participation/calibration remain unchanged; mixed source periods.",
                                "No modeled2026 bridge or calibrated probability intervals."]}
    (output/"source_manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8", newline="\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--acs-file", type=Path, default=Path(__file__).resolve().parents[1]/"data/demographic_inputs/acs2024_5yr_B01001_response.json")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output_dir, args.acs_file), indent=2))
