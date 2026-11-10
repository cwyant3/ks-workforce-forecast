"""Stage Kansas-resident outbound commuting from every other state's LODES aux file.

    python scripts/stage_lodes_outbound.py --year 2023 [--raw-dir PATH]

A state's ``aux`` OD file holds jobs located in that state whose workers live
elsewhere, so Kansas residents working out of state appear only in other
states' aux files. Raw downloads go to ``--raw-dir`` (default: a temp folder
outside OneDrive) and are deleted after aggregation; only Kansas-origin county
pairs are kept, as ``data/lodes_cache/ks_residents_aux_{sf}_JT01_{year}.parquet``.
A coverage file records every state, including unpublished ones.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetch_lodes import FIPS_TO_ABBR, _HTTP_HEADERS, _od_url, read_od_partition  # noqa: E402
from scripts.stage_statewide_sources import load_env_key  # noqa: E402

CACHE = ROOT / "data/lodes_cache"


def stage(year: int, raw_dir: Path, home_state: str = "20", states=None) -> list[dict]:
    coverage = []
    for sf, abbr in FIPS_TO_ABBR.items():
        if sf == home_state or (states and abbr not in states):
            continue
        url = _od_url(abbr, year, "JT01", "aux")
        target = CACHE / f"ks_residents_aux_{sf}_JT01_{year}.parquet"
        if target.exists():
            coverage.append(dict(state=abbr, state_fips=sf, year=year, status="published", url=url))
            continue
        try:
            r = requests.get(url, headers=_HTTP_HEADERS, timeout=300)
            if r.status_code == 404:
                coverage.append(dict(state=abbr, state_fips=sf, year=year, status="not_published", url=url))
                continue
            r.raise_for_status()
            raw = raw_dir / url.rsplit("/", 1)[-1]
            raw.write_bytes(r.content)
            flows = read_od_partition(raw, sf, year, "JT01", "aux")
            raw.unlink()
            flows = flows[flows.h_county_fips.str.startswith(home_state)]
            flows.to_parquet(target, index=False)
            coverage.append(dict(state=abbr, state_fips=sf, year=year, status="published", url=url,
                                 ks_resident_jobs=int(flows.jobs.sum())))
        except (requests.RequestException, OSError, ValueError) as exc:
            coverage.append(dict(state=abbr, state_fips=sf, year=year, status="access_failed", url=url,
                                 error=type(exc).__name__))
    return coverage


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--raw-dir", type=Path)
    ap.add_argument("--states", nargs="*", help="Limit to these state abbreviations")
    ap.add_argument("--evidence-out", type=Path, help="Also write resident-flow evidence files to this folder")
    ap.add_argument("--main-parquet", type=Path, help="Home-state main JT01 county pairs for --evidence-out")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        raw_dir = args.raw_dir or Path(tmp)
        coverage = stage(args.year, raw_dir, states=args.states)
    path = CACHE / f"ks_residents_aux_coverage_JT01_{args.year}.json"
    old = json.loads(path.read_text()) if path.exists() else []
    merged = {c["state"]: c for c in old} | {c["state"]: c for c in coverage}
    path.write_text(json.dumps(sorted(merged.values(), key=lambda c: c["state"]), indent=1))
    print({s: sum(1 for c in merged.values() if c["status"] == s) for s in ("published", "not_published", "access_failed")})
    if args.evidence_out:
        write_evidence(args.evidence_out, args.year, args.main_parquet)



def write_evidence(out: Path, year: int, main_parquet: Path, home_state: str = "20") -> None:
    """Combine the home state's main county pairs with staged aux pairs into evidence files."""
    import pandas as pd
    main = pd.read_parquet(main_parquet)
    main = main[main.h_county_fips.str.startswith(home_state) & main.year.eq(year)]
    aux = [pd.read_parquet(p) for p in sorted(CACHE.glob(f"ks_residents_aux_*_JT01_{year}.parquet"))]
    flows = pd.concat([main, *aux], ignore_index=True)[["h_county_fips", "w_county_fips", "jobs", "year", "partition", "source_url"]]
    # Destination county names (all U.S. counties) from the Census ACS API, for chart labels only.
    key = load_env_key("CENSUS_API_KEY")
    r = requests.get(f"https://api.census.gov/data/{min(year, 2023)}/acs/acs5",
                     params={"get": "NAME", "for": "county:*", **({"key": key} if key else {})}, timeout=90)
    r.raise_for_status()
    rows = r.json()
    names = {s + c: n for n, s, c in rows[1:]}
    flows["w_county_name"] = flows.w_county_fips.map(names).fillna("")
    flows.to_parquet(out / f"lodes_resident_flows_{year}.parquet", index=False)
    coverage = json.loads((CACHE / f"ks_residents_aux_coverage_JT01_{year}.json").read_text())
    coverage = [c for c in coverage if c.get("year", year) == year]
    (out / f"lodes_resident_coverage_{year}.json").write_text(json.dumps(coverage, indent=1))
    print(f"evidence: {len(flows)} county pairs, {sum(c['status'] == 'published' for c in coverage)} destination states")


if __name__ == "__main__":
    main()
