"""Fetch ACS 2024 5-year B07001 (mobility by age) for the migrant age adjustment.

B07001 counts residents by where they lived one year earlier. The movers who
arrived from another county, another state or abroad give the age mix of
in-movers; migration_age_mix.py turns that into an 18-64 share. Census
credentials come from the environment or the local .env and are never written
to files, logs or exceptions (see build_demographic_inputs.safe_get).

Usage (from the dashboard root, with its venv):
    python scripts/build_migration_age_inputs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from build_demographic_inputs import ACS_URL, DESTINATION, census_key, provenance, safe_get, write_json  # noqa: E402

GROUP = "B07001"
WICHITA_CBSA = "48620"
REQUESTS = (
    ("labels", f"{ACS_URL}/groups/{GROUP}.json", {}),
    ("response", ACS_URL, {"get": f"group({GROUP})", "for": "county:*", "in": "state:20"}),
    ("msa48620_response", ACS_URL, {"get": f"group({GROUP})",
                                    "for": f"metropolitan statistical area/micropolitan statistical area:{WICHITA_CBSA}"}),
)


def download(destination: Path = DESTINATION) -> dict:
    key = census_key()
    manifest_path = destination / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with requests.Session() as session:
        for kind, url, params in REQUESTS:
            query = dict(params)
            if key and kind != "labels":
                query["key"] = key
            response = safe_get(session, url, query, json_expected=True)
            payload = response.json()
            if kind == "response" and (not isinstance(payload, list) or len(payload) != 106):
                raise ValueError("Expected 105 Kansas county rows for B07001")
            if kind == "msa48620_response" and (not isinstance(payload, list) or len(payload) != 2):
                raise ValueError("Expected one Wichita metro row for B07001")
            if kind == "labels" and not isinstance(payload.get("variables"), dict):
                raise ValueError("Missing variable dictionary for B07001")
            name = f"acs2024_5yr_{GROUP}_{kind}.json"
            (destination / name).write_bytes(response.content)
            manifest["sources"][name] = provenance(response.content, url + ("?" + urlencode(params) if params else ""))
            print(f"Saved {name}")
    write_json(manifest_path, manifest)
    return manifest


if __name__ == "__main__":
    download()
