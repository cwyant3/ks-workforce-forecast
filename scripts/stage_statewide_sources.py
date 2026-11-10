"""Stage a statewide (105-county) pressure evidence folder from public sources.

    python scripts/stage_statewide_sources.py --base data/pressure_sources/2026-10-20 \
        --out data/pressure_sources/2026-11-10 [--laus-end-month 8]

Copies the reviewed base evidence, drops its pilot-only LAUS probes, then adds
the missing county QCEW area slices (BLS open data API) and a statewide LAUS
monthly file (BLS API v2). Nothing is adopted; the output folder is the input
to ``build_county_pressure.py``. Requests are explicit; keys are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetch_laus import _build_series_ids, MEASURES  # noqa: E402

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"


def load_env_key(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def county_fips5() -> list[str]:
    import pandas as pd
    proj = pd.read_parquet(ROOT / "data/outputs/projections_s20.parquet", columns=["county_fips"])
    return sorted("20" + str(c).zfill(3) for c in proj.county_fips.unique())


def stage_qcew(out: Path, counties: list[str], periods=((2025, 1), (2026, 1))) -> list[str]:
    missing = []
    for fips in counties:
        for year, quarter in periods:
            target = out / f"qcew-{fips}-{year}q{quarter}.csv"
            if target.exists():
                continue
            url = f"https://data.bls.gov/cew/data/api/{year}/{quarter}/area/{fips}.csv"
            for attempt in range(4):
                try:
                    r = requests.get(url, timeout=45, headers={"User-Agent": "WorkforceDashboard research (public data)"})
                    if r.status_code == 200 and r.text.startswith('"area_fips"'):
                        target.write_text(r.text, encoding="utf-8")
                        break
                except requests.RequestException:
                    pass
                time.sleep(3 * (attempt + 1))
            else:
                missing.append(target.name)
            time.sleep(0.2)
    return missing


def stage_laus(out: Path, counties: list[str], year: int, key: str | None) -> dict:
    ids = _build_series_ids("20", [c[2:] for c in counties])
    batch = 50 if key else 25
    series, messages = [], []
    for i in range(0, len(ids), batch):
        payload = {"seriesid": ids[i:i + batch], "startyear": str(year), "endyear": str(year)}
        if key:
            payload["registrationkey"] = key
        r = requests.post(BLS_URL, json=payload, timeout=90)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError("BLS LAUS batch failed: " + "; ".join(data.get("message", [])))
        series.extend(data["Results"]["series"])
        messages.extend(data.get("message", []))
        time.sleep(0.5)
    result = {"status": "REQUEST_SUCCEEDED", "responseTime": 0, "message": messages,
              "Results": {"series": series}}
    (out / f"laus-kansas-{year}.json").write_text(json.dumps(result), encoding="utf-8")
    return {"requested": len(ids), "returned": len(series)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--laus-year", type=int, default=2026)
    args = ap.parse_args()
    base, out = (ROOT / args.base if not args.base.is_absolute() else args.base,
                 ROOT / args.out if not args.out.is_absolute() else args.out)
    out.mkdir(parents=True, exist_ok=True)  # re-running resumes: existing files are kept
    for f in base.iterdir():
        if f.is_file() and not f.name.startswith("laus-") and not (out / f.name).exists():
            shutil.copy2(f, out / f.name)
    counties = county_fips5()
    print("counties:", len(counties))
    missing = stage_qcew(out, counties)
    print("qcew missing:", missing)
    print("laus:", stage_laus(out, counties, args.laus_year, load_env_key("BLS_API_KEY")))


if __name__ == "__main__":
    main()
