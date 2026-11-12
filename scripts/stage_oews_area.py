"""Stage one OEWS substate area's occupation inventory via the BLS API.

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
import csv
import hashlib
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


def stage_workbook_area(workbook: Path, area: str, slug: str, year: int, out: Path):
    """Stream an exact area slice after the source workbook has been preprocessed.

    Retain every source column and occupation level. The observation adapter
    selects detailed occupations plus the all-occupation total explicitly.
    """
    from openpyxl import load_workbook
    sheet_name = f"All May {year} data"
    book = load_workbook(workbook, read_only=True, data_only=True)
    try:
        if sheet_name not in book.sheetnames:
            raise ValueError(f"Expected verified vintage sheet: {sheet_name}")
        rows = book[sheet_name].iter_rows(values_only=True)
        header = next(rows)
        area_column = header.index("AREA")
        selected = [row for row in rows if str(row[area_column]).strip().removesuffix('.0') == str(area)]
    finally:
        book.close()
    if not selected:
        raise ValueError(f"No published source rows for area {area}")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"oews-{slug}-{year}.csv"
    with target.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(selected)
    with workbook.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    (out / f"oews-{slug}-{year}-source.json").write_text(json.dumps({
        'source_file': workbook.name, 'source_sha256': digest, 'sheet': sheet_name,
        'source_url': f'https://www.bls.gov/oes/special-requests/oesm{str(year)[-2:]}all.zip',
        'catalog_url': 'https://www.bls.gov/oes/tables.htm',
        'area_code': area, 'reference_year': year, 'source_rows': len(selected),
        'selection': 'All rows with exact AREA; all source columns and occupation levels preserved',
        'observation_scope': 'cross-industry, all covered ownership, detailed occupations plus all-occupation total',
    }, indent=2), encoding='utf-8')
    print(f'{slug}: staged {len(selected)} original area rows from {sheet_name}', flush=True)


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
    ap.add_argument("--workbook", type=Path, help="Preprocessed official all-data workbook; stage an offline area CSV slice")
    args = ap.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    if args.workbook:
        workbook = args.workbook if args.workbook.is_absolute() else ROOT / args.workbook
        stage_workbook_area(workbook, args.area, args.slug, args.year, out)
        return
    key = load_env_key("BLS_API_KEY")
    if not key:
        sys.exit("BLS_API_KEY is required for a full-area inventory (unregistered limits are too low)")
    titles = candidate_occupations(args.year)
    ids = area_series_ids(args.area, list(titles))
    out.mkdir(parents=True, exist_ok=True)
    batches = out / f".oews-{args.slug}-{args.year}-batches"
    batches.mkdir(exist_ok=True)
    series, messages = [], []
    for i in range(0, len(ids), 50):
        batch_ids = ids[i:i + 50]
        saved = batches / f"{i // 50:03d}.json"
        data = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else None
        if data is not None and (data.get("status") != "REQUEST_SUCCEEDED" or
                {s.get("seriesID") for s in data.get("Results", {}).get("series", [])} != set(batch_ids)):
            raise ValueError(f"Cached OEWS batch does not match requested series: {saved.name}")
        body = {"seriesid": batch_ids, "startyear": str(args.year), "endyear": str(args.year),
                "registrationkey": key}
        for attempt in range(4) if data is None else []:
            try:
                r = requests.post(API, json=body, timeout=90)
                data = r.json()
                if data.get("status") == "REQUEST_SUCCEEDED":
                    break
            except (requests.RequestException, ValueError):
                data = {}
            time.sleep(3 * (attempt + 1))
        if data is None or data.get("status") != "REQUEST_SUCCEEDED":
            sys.exit(f"BLS OEWS batch {i // 50} failed: {(data or {}).get('message')}")
        if {s.get("seriesID") for s in data["Results"]["series"]} != set(batch_ids):
            sys.exit(f"BLS OEWS batch {i // 50} returned an incomplete series inventory")
        if not saved.exists():
            saved.write_text(json.dumps(data), encoding="utf-8")
        series.extend(data["Results"]["series"])
        messages.extend(m for m in data.get("message", []) if "does not exist" not in m)
        time.sleep(0.4)
        if i // 50 % 10 == 0 or i + 50 >= len(ids):
            print(f"{args.slug}: {min(i + 50, len(ids))}/{len(ids)} series staged", flush=True)
    payload = {"status": "REQUEST_SUCCEEDED", "responseTime": 0, "message": messages,
               "Results": {"series": series}}
    (out / f"oews-{args.slug}-{args.year}.json").write_text(json.dumps(payload), encoding="utf-8")
    (out / f"oews-{args.slug}-{args.year}-titles.json").write_text(json.dumps(titles, indent=0), encoding="utf-8")
    with_data = sum(1 for s in series if s.get("data"))
    print(f"requested {len(ids)} series for {len(titles)} SOCs; {with_data} returned data")


if __name__ == "__main__":
    main()
