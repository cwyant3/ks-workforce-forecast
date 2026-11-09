"""Fetch and normalize public aggregate inputs for a separate demographic model.

No forecasts or existing outputs are changed. Census credentials remain in local
environment configuration and are omitted from logs, metadata and exceptions.
SSA HTML must pass through the vault's shared preprocess.py before normalization.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "data" / "demographic_inputs"
ACS_URL = "https://api.census.gov/data/2024/acs/acs5"
SSA_URL = "https://www.ssa.gov/oact/STATS/table4c6.html"


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def census_key(root: Path = ROOT) -> str | None:
    """Return the Census key from the environment or local .env; never log it."""
    key = os.getenv("CENSUS_API_KEY")
    # Match this application's supported local KEY=value configuration. This is
    # not a general dotenv parser and does not evaluate shell syntax.
    config = root / ".env"
    if not key and config.is_file():
        for line in config.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("CENSUS_API_KEY="):
                key = line.strip().split("=", 1)[1].strip()
                if len(key) >= 2 and key[0] == key[-1] and key[0] in {"'", '"'}:
                    key = key[1:-1]
                break
    return key


def download_inputs(destination: Path = DESTINATION, ssa_web_response: Path | None = None) -> dict:
    key = census_key()
    destination.mkdir(parents=True, exist_ok=True)
    sources = {}
    manifest = {"schema_version": 1, "baseline_period": "2020-2024", "acs_product": "2024 ACS 5-year",
                "ssa_reference_year": 2023, "ssa_trustees_report_year": 2026, "sources": sources}
    with requests.Session() as session:
        for group in ("B01001", "B23001"):
            for kind, url, params in (
                ("labels", f"{ACS_URL}/groups/{group}.json", {}),
                ("response", ACS_URL, {"get": f"group({group})", "for": "county:*", "in": "state:20"}),
                *(([("state_response", ACS_URL, {"get": f"group({group})", "for": "state:20"})]) if group == "B23001" else []),
            ):
                public_url = url + ("?" + urlencode(params) if params else "")
                query = dict(params)
                if key and kind != "labels":
                    query["key"] = key
                response = safe_get(session, url, query, json_expected=True)
                payload = response.json()
                if kind == "response" and (not isinstance(payload, list) or len(payload) != 106):
                    raise ValueError(f"Expected 105 Kansas county rows for {group}")
                if kind == "state_response" and (not isinstance(payload, list) or len(payload) != 2):
                    raise ValueError("Expected one Kansas statewide B23001 row")
                if kind == "labels" and not isinstance(payload.get("variables"), dict):
                    raise ValueError(f"Missing variable dictionary for {group}")
                name = f"acs2024_5yr_{group}_{kind}.json"
                (destination / name).write_bytes(response.content)
                sources[name] = provenance(response.content, public_url)
                write_json(destination / "source_manifest.json", manifest)
                print(f"Saved {name}")
        if ssa_web_response is not None:
            record = json.loads(ssa_web_response.read_text(encoding="utf-8"))
            if record.get("source_url") != SSA_URL or "2023" not in record["response_text"] or "2026" not in record["response_text"]:
                raise ValueError("Web response does not match the reviewed SSA source")
            name = "ssa2023_web_response.json"
            content = ssa_web_response.read_bytes()
            (destination / name).write_bytes(content)
            sources[name] = {**provenance(content, SSA_URL), "retrieved_at_utc": record["retrieved_at_utc"],
                             "acquisition": record["acquisition"],
                             "original_response_text_sha256": hashlib.sha256(record["response_text"].encode()).hexdigest()}
            manifest["access_notes"] = ["SSA raw HTML download returned HTTP403; full official web-tool response preserved instead."]
        else:
            response = safe_get(session, SSA_URL, {}, json_expected=False)
            if b"2023" not in response.content or b"2026" not in response.content or b"Period Life Table" not in response.content:
                raise ValueError("SSA page does not match the reviewed 2023/2026 table")
            name = "ssa2023_period_life_table.html"
            (destination / name).write_bytes(response.content)
            sources[name] = provenance(response.content, SSA_URL)
        print(f"Saved {name}")
    write_json(destination / "source_manifest.json", manifest)
    return manifest


def provenance(content: bytes, url: str) -> dict:
    return {"source_url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def safe_get(session, url: str, params: dict, *, json_expected: bool):
    try:
        response = session.get(url, params=params, timeout=120)
    except requests.RequestException:
        raise RuntimeError("Official public source request failed; credentials and request URL omitted") from None
    if response.status_code != 200:
        raise RuntimeError(f"Official public source returned HTTP {response.status_code}; request URL omitted")
    if json_expected:
        try:
            response.json()
        except ValueError:
            raise RuntimeError("Census returned non-JSON content; check local API key configuration") from None
    return response


def age_limits(label: str) -> tuple[int, int | None]:
    label = label.rstrip(":").strip()
    if label == "Under 5 years":
        return 0, 4
    if match := re.fullmatch(r"(\d+) to (\d+) years", label):
        return int(match[1]), int(match[2])
    if match := re.fullmatch(r"(\d+) and (\d+) years", label):
        return int(match[1]), int(match[2])
    if match := re.fullmatch(r"(\d+) years", label):
        return int(match[1]), int(match[1])
    if match := re.fullmatch(r"(\d+) years and over", label):
        return int(match[1]), None
    raise ValueError("Unrecognized official age band: " + label)


def read_group(destination: Path, group: str) -> tuple[list[dict], dict]:
    payload = json.loads((destination / f"acs2024_5yr_{group}_response.json").read_text(encoding="utf-8"))
    labels = json.loads((destination / f"acs2024_5yr_{group}_labels.json").read_text(encoding="utf-8"))["variables"]
    if len(set(payload[0])) != len(payload[0]):
        raise ValueError("Duplicate source column names")
    records = [dict(zip(payload[0], row, strict=True)) for row in payload[1:]]
    fips = [row["state"] + row["county"] for row in records]
    if len(fips) != 105 or len(set(fips)) != 105 or any(not f.startswith("20") or len(f) != 5 for f in fips):
        raise ValueError("Invalid Kansas county coverage")
    return records, labels


def observed_number(value):
    if value in (None, "", "null"):
        return None
    number = float(value)
    return number if number >= 0 else None  # Census negative sentinel values stay unavailable.


def observation(row: dict, variable: str, labels: dict) -> dict:
    estimate = observed_number(row.get(variable))
    return {"source_variable": variable, "source_label": labels[variable]["label"],
            "estimate": estimate, "moe90": observed_number(row.get(variable[:-1] + "M")),
            "availability_status": "published" if estimate is not None else "not_reported",
            "raw_estimate": row.get(variable), "raw_moe": row.get(variable[:-1] + "M")}


def normalize_acs(destination: Path) -> tuple[list[dict], list[dict]]:
    population, labor_force = [], []
    for group in ("B01001", "B23001"):
        counties, labels = read_group(destination, group)
        for county in counties:
            identity = {"county_fips": county["state"] + county["county"], "county_name": county["NAME"],
                        "period_start": "2020-01-01", "period_end": "2024-12-31", "acs_year": 2024}
            for variable, metadata in sorted(labels.items()):
                if not re.fullmatch(group + r"_\d{3}E", variable):
                    continue
                parts = metadata["label"].split("!!")
                if len(parts) < 4 or parts[2].rstrip(":") not in {"Male", "Female"}:
                    continue
                sex = parts[2].rstrip(":").lower()
                lo, hi = age_limits(parts[3])
                base = {**identity, "sex": sex, "age_start": lo, "age_end": hi, "age_label": parts[3].rstrip(":")}
                if group == "B01001" and len(parts) == 4:
                    population.append({**base, **observation(county, variable, labels)})
                elif group == "B23001":
                    tail = [part.rstrip(":") for part in parts[4:]]
                    metric = ("population" if not tail else "civilian_labor_force" if tail == ["In labor force", "Civilian"]
                              else "armed_forces" if tail == ["In labor force", "In Armed Forces"]
                              else None)
                    # Older B23001 bands omit the Armed Forces branch.
                    if tail == ["In labor force"] and lo >= 65:
                        metric = "civilian_labor_force"
                    if metric:
                        labor_force.append({**base, "measure": metric, **observation(county, variable, labels)})
    for county in {row["county_fips"] for row in population}:
        for sex in ("male", "female"):
            bands = [row for row in population if row["county_fips"] == county and row["sex"] == sex]
            expected_start = 0
            for row in sorted(bands, key=lambda r: r["age_start"]):
                if row["age_start"] != expected_start:
                    raise ValueError("Population age bands overlap or have gaps")
                expected_start = row["age_end"] + 1 if row["age_end"] is not None else 120
            if expected_start != 120:
                raise ValueError("Population age bands lack an open-ended oldest age")
            labor = [row for row in labor_force if row["county_fips"] == county and row["sex"] == sex]
            pop = {row["age_start"]: row["estimate"] for row in labor if row["measure"] == "population"}
            lf = {row["age_start"]: row["estimate"] for row in labor if row["measure"] == "civilian_labor_force"}
            if set(pop) != set(lf) or len(pop) != 13:
                raise ValueError("Incomplete B23001 population/civilian labor-force band pairs")
            if any(pop[age] is not None and value is not None and value > pop[age] for age, value in lf.items()):
                raise ValueError("Civilian labor force exceeds its total age-sex population")
    return population, labor_force


def normalize_survival(companion: Path) -> list[dict]:
    """Parse a preprocessed HTML companion or preserved official web-tool text."""
    text = companion.read_text(encoding="utf-8-sig")
    if companion.suffix == ".json":
        text = json.loads(text)["response_text"]
    if "2023" not in text or "2026" not in text:
        raise ValueError("SSA companion lacks reviewed reference years")
    rows = []
    for line in text.splitlines():
        line = re.sub(r"^L\d+:\s*", "", line)
        cells = [cell.strip().replace(",", "") for cell in line.strip().strip("|").split("|")]
        if len(cells) != 7 or not cells[0].isdigit():
            continue
        age = int(cells[0])
        values = [float(value) for value in cells[1:]]
        rows.append(dict(age=age, male_qx=values[0], male_lx=int(values[1]), female_qx=values[3], female_lx=int(values[4])))
    if [row["age"] for row in rows] != list(range(120)):
        raise ValueError("SSA normalization requires exactly ages 0–119")
    for sex in ("male", "female"):
        if any(not 0 <= row[f"{sex}_qx"] <= 1 or row[f"{sex}_lx"] < 0 for row in rows):
            raise ValueError("Invalid survival probabilities or survivor counts")
        if rows[0][f"{sex}_lx"] != 100000 or any(b[f"{sex}_lx"] > a[f"{sex}_lx"] for a, b in zip(rows, rows[1:])):
            raise ValueError("Invalid survivor radix or monotonicity")
        if any(abs(a[f"{sex}_lx"] * (1 - a[f"{sex}_qx"]) - b[f"{sex}_lx"]) > 1.1
               for a, b in zip(rows, rows[1:])):
            raise ValueError("Survivor progression disagrees with published qx beyond rounding tolerance")
    return rows


def normalize_inputs(destination: Path, companion: Path) -> dict:
    manifest = json.loads((destination / "source_manifest.json").read_text(encoding="utf-8"))
    for name, source in manifest["sources"].items():
        if hashlib.sha256((destination / name).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Source hash mismatch: " + name)
    population, labor = normalize_acs(destination)
    survival = normalize_survival(companion)
    # Published grouped population counts should reconcile exactly. MOEs are
    # retained per variable, never summed as if estimates were independent.
    for group, bands in (("B01001", population), ("B23001", [r for r in labor if r["measure"] == "population"])):
        source_rows, _ = read_group(destination, group)
        for row in source_rows:
            fips = row["state"] + row["county"]
            values = [r["estimate"] for r in bands if r["county_fips"] == fips]
            total = observed_number(row[f"{group}_001E"])
            if total is not None and all(v is not None for v in values) and sum(values) != total:
                raise ValueError(f"{group} age-sex bands do not reconcile to published total for {fips}")
    state = json.loads((destination / "acs2024_5yr_B23001_state_response.json").read_text(encoding="utf-8"))
    if len(state) != 2 or len(set(state[0])) != len(state[0]) or dict(zip(state[0], state[1], strict=True))["state"] != "20":
        raise ValueError("Invalid Kansas statewide fallback record")
    for name, rows in (("population_age_sex_bands.json", population), ("labor_force_age_sex_bands.json", labor),
                       ("survival_age_sex.json", survival)):
        write_json(destination / name, rows)
    summary = {"population_rows": len(population), "labor_force_rows": len(labor), "survival_rows": len(survival)}
    if len(population) != 105 * 46 or len(labor) != 105 * 72:
        raise ValueError("Unexpected ACS normalized band coverage")
    summary["county_count"] = 105
    summary["normalization_checks"] = ["105 distinct Kansas counties", "46 exhaustive population age-sex bands per county",
                                         "13 population/civilian labor force pairs per sex and county", "civilian labor force <= population",
                                         "age-sex totals reconcile to published group totals", "one Kansas statewide B23001 fallback record",
                                         "120 unique survival ages 0–119", "valid qx, nonincreasing lx and rounded progression", "original response hashes verified"]
    summary["unavailable_population_cells"] = sum(row["estimate"] is None for row in population)
    summary["unavailable_labor_force_cells"] = sum(row["estimate"] is None for row in labor)
    summary["zero_population_denominators"] = sum(row["estimate"] == 0 for row in labor if row["measure"] == "population")
    summary["survival_terminal_note"] = "Published qx119=0.926604, not1; model terminal-age handling must be explicit. Published lx rounds to0 at extreme ages."
    summary["normalized_sha256"] = {name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
                                    for name in ("population_age_sex_bands.json", "labor_force_age_sex_bands.json", "survival_age_sex.json")}
    write_json(destination / "validation.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--normalize-only", action="store_true")
    parser.add_argument("--ssa-companion", type=Path)
    parser.add_argument("--ssa-web-response", type=Path,
                        help="Preserved exact official web-tool text when raw SSA HTML download is unavailable")
    args = parser.parse_args()
    if not args.normalize_only:
        download_inputs(ssa_web_response=args.ssa_web_response)
    if not args.download_only:
        source = args.ssa_companion or args.ssa_web_response
        if source is None:
            parser.error("Run shared preprocess.py on the SSA HTML, then supply --ssa-companion with --normalize-only")
        print(json.dumps(normalize_inputs(DESTINATION, source)))


if __name__ == "__main__":
    main()
