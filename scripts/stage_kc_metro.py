"""Stage Missouri-side Kansas City metro evidence (the 9 Missouri counties of OEWS area 28140).

    python scripts/stage_kc_metro.py --base data/pressure_sources/2026-11-10-qwi-r2026q4 \
        --out data/pressure_sources/2026-11-10-kc-metro

Copies the Kansas evidence unchanged, then adds ``metro-``-prefixed raw
payloads so Kansas parsers never pick them up: ACS 2024 5-year B23025 and
S2301 for Missouri counties, BLS LAUS monthly 2026 and QCEW 2025Q1/2026Q1
area slices for the nine counties. Keys are read from .env, never saved.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetch_laus import _build_series_ids  # noqa: E402
from scripts.stage_statewide_sources import load_env_key, BLS_URL  # noqa: E402

# Missouri members of the Kansas City, MO-KS OEWS area (BLS May 2025 definitions).
MISSOURI_KC_COUNTIES = ("29013", "29025", "29037", "29047", "29049", "29095", "29107", "29165", "29177")
ACS_FIELDS = {"b23025": ("acs/acs5", ",".join(f"B23025_{i:03d}{s}" for i in range(1, 8) for s in "EM")),
              "s2301": ("acs/acs5/subject", "S2301_C02_001E,S2301_C02_001M")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    base, out = (p if p.is_absolute() else ROOT / p for p in (args.base, args.out))
    out.mkdir(parents=True, exist_ok=True)
    for f in base.iterdir():
        if f.is_file() and not (out / f.name).exists():
            shutil.copy2(f, out / f.name)
    census, bls = load_env_key("CENSUS_API_KEY"), load_env_key("BLS_API_KEY")
    for table, (path, fields) in ACS_FIELDS.items():
        target = out / f"metro-acs-2024-{table}-mo.json"
        if not target.exists():
            r = requests.get(f"https://api.census.gov/data/2024/{path}", timeout=120,
                             params={"get": "NAME," + fields, "for": "county:*", "in": "state:29", **({"key": census} if census else {})})
            r.raise_for_status()
            target.write_text(json.dumps(r.json()), encoding="utf-8")
    target = out / "metro-laus-mo-2026.json"
    if not target.exists():
        body = {"seriesid": _build_series_ids("29", [c[2:] for c in MISSOURI_KC_COUNTIES]), "startyear": "2026", "endyear": "2026"}
        if bls:
            body["registrationkey"] = bls
        data = requests.post(BLS_URL, json=body, timeout=90).json()
        if data.get("status") != "REQUEST_SUCCEEDED":
            sys.exit("BLS LAUS request failed: " + "; ".join(data.get("message", [])))
        target.write_text(json.dumps(data), encoding="utf-8")
    for fips in MISSOURI_KC_COUNTIES:
        for year, q in ((2025, 1), (2026, 1)):
            target = out / f"metro-qcew-{fips}-{year}q{q}.csv"
            if target.exists():
                continue
            for attempt in range(4):
                try:
                    r = requests.get(f"https://data.bls.gov/cew/data/api/{year}/{q}/area/{fips}.csv", timeout=45,
                                     headers={"User-Agent": "WorkforceDashboard research (public data)"})
                    if r.status_code == 200 and r.text.startswith('"area_fips"'):
                        target.write_text(r.text, encoding="utf-8")
                        break
                except requests.RequestException:
                    pass
                time.sleep(3 * (attempt + 1))
            else:
                print("missing", target.name)
    print("metro files:", len(list(out.glob("metro-*"))))


if __name__ == "__main__":
    main()
