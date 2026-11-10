"""Stage Census County Business Patterns county payloads for the dashboard industries.

    python scripts/stage_cbp.py --out data/pressure_sources/2026-11-10-qwi-r2026q4 [--year 2023]

One Census API request per NAICS sector (batched NAICS filters return 204,
see fetch_cbp.py). Saves the unmodified JSON as ``cbp-{year}-ks-{naics}.json``;
the parser keeps only legal form 001 (all) and size class 001 (all).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.stage_statewide_sources import load_env_key  # noqa: E402
from scripts.stage_qwi import DASHBOARD_INDUSTRIES  # noqa: E402

FIELDS = "NAME,NAICS2017_LABEL,ESTAB,EMP,EMP_N,EMP_F,PAYANN,PAYANN_N,PAYANN_F,LFO,EMPSZES"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--year", type=int, default=2023)
    args = ap.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    key = load_env_key("CENSUS_API_KEY")
    for naics in DASHBOARD_INDUSTRIES:
        target = out / f"cbp-{args.year}-ks-{naics}.json"
        if target.exists():
            continue
        params = {"get": FIELDS, "for": "county:*", "in": "state:20", "NAICS2017": naics} | ({"key": key} if key else {})
        r = requests.get(f"https://api.census.gov/data/{args.year}/cbp", params=params, timeout=120)
        r.raise_for_status()
        payload = r.json()
        target.write_text(json.dumps(payload), encoding="utf-8")
        print(naics, len(payload) - 1, "rows")
        time.sleep(0.3)


if __name__ == "__main__":
    main()
