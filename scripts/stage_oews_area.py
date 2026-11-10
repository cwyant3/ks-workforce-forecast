"""Stage one OEWS metropolitan area's full occupation inventory via the BLS API.

    python scripts/stage_oews_area.py --area 28140 --slug kansascity \
        --out data/pressure_sources/2026-11-10 [--year 2025]

BLS blocks scripted downloads of the OEWS bulk files (403, see fetch_oes.py),
but the registered API v2 serves area series. Candidate occupations are every
cross-industry SOC (detailed plus 00-0000) published at state level for Kansas
or Missouri in the cached state file; SOCs the area does not publish come back
empty and stay not_reported. Writes the unmodified API payload as
``oews-{slug}-{year}.json`` plus ``oews-{slug}-{year}-titles.json``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetch_oes import area_series_ids  # noqa: E402
from scripts.stage_statewide_sources import load_env_key  # noqa: E402

API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def candidate_occupations(year: int, states=("20", "29")) -> dict[str, str]:
    d = pd.read_parquet(ROOT / f"data/oes_cache/oes_allstates_{year}.parquet")
    d = d[d.area.astype(str).str.zfill(2).isin(states) & d.o_group.isin(["detailed", "total"])]
    return dict(sorted(d.drop_duplicates("occ_code").set_index("occ_code").occ_title.items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--area", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--year", type=int, default=2025)
    args = ap.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    key = load_env_key("BLS_API_KEY")
    if not key:
        sys.exit("BLS_API_KEY is required for a full-area inventory (unregistered limits are too low)")
    titles = candidate_occupations(args.year)
    ids = area_series_ids(args.area, list(titles))
    series, messages = [], []
    for i in range(0, len(ids), 50):
        body = {"seriesid": ids[i:i + 50], "startyear": str(args.year), "endyear": str(args.year),
                "registrationkey": key}
        for attempt in range(4):
            try:
                r = requests.post(API, json=body, timeout=90)
                data = r.json()
                if data.get("status") == "REQUEST_SUCCEEDED":
                    break
            except (requests.RequestException, ValueError):
                data = {}
            time.sleep(3 * (attempt + 1))
        else:
            sys.exit(f"BLS OEWS batch {i // 50} failed: {data.get('message')}")
        series.extend(data["Results"]["series"])
        messages.extend(m for m in data.get("message", []) if "does not exist" not in m)
        time.sleep(0.4)
    payload = {"status": "REQUEST_SUCCEEDED", "responseTime": 0, "message": messages,
               "Results": {"series": series}}
    (out / f"oews-{args.slug}-{args.year}.json").write_text(json.dumps(payload), encoding="utf-8")
    (out / f"oews-{args.slug}-{args.year}-titles.json").write_text(json.dumps(titles, indent=0), encoding="utf-8")
    with_data = sum(1 for s in series if s.get("data"))
    print(f"requested {len(ids)} series for {len(titles)} SOCs; {with_data} returned data")


if __name__ == "__main__":
    main()
