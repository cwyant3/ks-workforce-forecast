"""Stage Kansas county QWI payloads for each dashboard industry and quarter.

    python scripts/stage_qwi.py --out data/pressure_sources/2026-11-10 \
        --periods 2025-Q2 2025-Q3 2025-Q4 [--industries 22 23 ...]

Saves the unmodified Census API JSON (all counties, private ownership A05,
all ages/sexes, unadjusted, all firm sizes/ages) as
``qwi-{period}-ks-{industry}.json``. Existing files are kept, so reruns
resume. The API key is read from .env and never written to disk.
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
from fetch_qwi import QWI_URL, qwi_query  # noqa: E402
from scripts.stage_statewide_sources import load_env_key  # noqa: E402

DASHBOARD_INDUSTRIES = ["22", "23", "31-33", "51", "54", "62", "71", "72", "81"]


def existing(out: Path, period: str, industry: str) -> bool:
    """Original manufacturing files use a different name; detect by content."""
    if (out / f"qwi-{period}-ks-{industry}.json").exists():
        return True
    for path in out.glob("qwi-*.json"):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        head = payload[0]
        if payload[1:] and payload[1][head.index("time")] == period and payload[1][head.index("industry")] == industry:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--periods", nargs="+", required=True)
    ap.add_argument("--industries", nargs="+", default=DASHBOARD_INDUSTRIES)
    args = ap.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    key = load_env_key("CENSUS_API_KEY")
    fetched, failed = [], []
    for industry in args.industries:
        for period in args.periods:
            if existing(out, period, industry):
                continue
            params = qwi_query("20", period, industry) | ({"key": key} if key else {})
            for attempt in range(4):
                try:
                    r = requests.get(QWI_URL, params=params, timeout=120)
                    payload = r.json() if r.status_code == 200 else None
                    if isinstance(payload, list) and payload and "Emp" in payload[0]:
                        (out / f"qwi-{period}-ks-{industry}.json").write_text(json.dumps(payload), encoding="utf-8")
                        fetched.append((industry, period))
                        break
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(3 * (attempt + 1))
            else:
                failed.append((industry, period))
            time.sleep(0.3)
    print(f"fetched {len(fetched)}; failed {failed}")


if __name__ == "__main__":
    main()
