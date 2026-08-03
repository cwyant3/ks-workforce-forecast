"""
refresh_dashboard.py
Monthly refresh driver for the KS Workforce Dashboard.

Why this exists
---------------
The fetch_*.py modules are *cache-on-exists*: each one returns its cached
parquet whenever the file is present, without checking the source for newer
data. A plain `python run_forecast.py --all` therefore regenerates the SAME
numbers month to month. To actually pull fresh data we must delete the
API-backed caches first, then re-run the pipeline.

This script:
  1. Clears the API-backed caches so the next run re-fetches live data.
  2. Parses the manual-download inputs (KDOL labor force, SSA disability, KDOL
     industry + occupational projections). run_forecast.py does not regenerate
     these — their parsers are standalone, so they must run here or their
     outputs silently keep whatever vintage was last parsed by hand.
  3. Runs run_forecast.py --all for the target state.
  4. Runs scripts/validate_outputs.py.
  5. Reports which MANUAL-download sources are stale (no public API).

Caches that are PRESERVED (never auto-cleared):
  - acs_cache    : ACS vintages are bound to the hardcoded ACS_YEARS list in
                   fetch_acs.py and self-stale via B23001_SCHEMA_VERSION.
                   A new ACS vintage requires editing ACS_YEARS by hand, so
                   clearing this monthly only re-fetches identical years.
  - kdol_cache   : KDOL UI claims — manual file (kdol_ui_manual.csv).
  - ssa_cache    : SSA disability — manual file (ssa_manual.csv).
  - bls_proj_cache : BLS/KS employment projections — manual .xlsx files.
  - ksde_cache   : KSDE K-12 enrollment override — annual, leave intact.
Deleting any of those would destroy user-placed manual downloads.

Usage:
    python refresh_dashboard.py                 # Kansas (state 20), full refresh
    python refresh_dashboard.py --state 20
    python refresh_dashboard.py --dry-run       # show what would be cleared, do nothing
    python refresh_dashboard.py --keep-annual-cache   # only clear monthly series (LAUS/JOLTS)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# Caches re-fetched from a live API. Safe to delete — they regenerate.
# Split so --keep-annual-cache can skip the heavy annual re-downloads.
MONTHLY_API_CACHES = ["laus_cache", "jolts_cache"]
# pc_cache (Projections Central) is a live REST API on an annual cadence; clearing
# it lets states that have newly published their cycle get picked up. Gap states
# aren't cached anyway, so they re-fetch every run regardless.
ANNUAL_API_CACHES = ["qcew_cache", "ipeds_cache", "lodes_cache", "oes_cache",
                     "cbp_cache", "pc_cache"]

# Manual-download sources (no public API). Checked for staleness, never cleared.
MANUAL_SOURCES = {
    # KDOL labor force export from KLIC's Telerik report builder (HTML-as-.xls).
    # NOTE: This REPLACES the abandoned "KDOL UI claims" source. KDOL does not
    # expose UI claims by county x NAICS publicly (see parse_manual_kdol_labforce.py
    # docstring); fetch_kdol_ui.py is a dead path that returns empty. The labor
    # force file is the dataset that actually feeds the dashboard's pulse layer.
    "KDOL labor force": {
        "files": ["kdol_cache/labforce__99999999.xls"],
        "url": "https://klic.dol.ks.gov/",
    },
    # SSA OASDI "Beneficiaries by State and County" workbook (oasdi_sc{YY}.xlsx),
    # downloaded manually from SSA (the site 403-blocks scripted requests).
    # NOTE: This REPLACES the abandoned "ssa_manual.csv" fallback. The auto-
    # downloader fetch_ssa_disability.py is a dead path (403 + legacy format);
    # the real input is this workbook parsed by parse_manual_ssa.py. SSA renamed
    # the publication from /oasdi_county/ to /oasdi_sc/ and publishes annually
    # with ~18mo lag, so this only needs re-downloading once a year.
    "SSA disability": {
        "files": ["ssa_cache/oasdi_sc24.xlsx"],
        "url": "https://www.ssa.gov/policy/docs/statcomps/oasdi_sc/index.html",
    },
    "BLS national projections": {
        "files": ["bls_proj_cache/bls_proj_national_manual.xlsx"],
        "url": "https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm",
    },
    # KDOL industry projections (KLIC Telerik report builder, HTML-as-.xls).
    # NOTE: This REPLACES the abandoned "KS state projections" source, which
    # pointed at bls_proj_cache/ks_proj_manual.xlsx — a file that never existed.
    # fetch_bls_proj.fetch_ks_state_projections() is a dead path: it calls
    # pd.ExcelFile() on what is actually an HTML <table>, so it raises
    # "Excel file format cannot be determined" no matter what the file is
    # renamed to. The real input is the .xls below, parsed by
    # scripts/parse_manual_ks_proj.py into ks_proj_industry.parquet.
    # Since the 2024-2034 cycle KDOL publishes clean .xlsx workbooks whose names
    # lead with the vintage ("2024-2034 KS Industry Projections.xlsx"), so a
    # lexical sort of the glob picks the newest cycle. Drop them in data/kdol_proj/
    # under their published filenames — no renaming needed. Both parsers still
    # accept the legacy Telerik HTML-as-.xls export as a fallback
    # (bls_proj_cache/ks_proj_manual.xls and data/occproj__*.xls).
    "KS industry projections": {
        "glob": "kdol_proj/*Industry Projections*.xlsx",
        "url": "https://www.dol.ks.gov/lmis/employment-projections",
    },
    "KS occupational projections": {
        "glob": "kdol_proj/*Occupational Projections*.xlsx",
        "url": "https://www.dol.ks.gov/lmis/employment-projections",
    },
    # Supplies the in-demand / rank flags the published projections book omits.
    # Without it the dashboard's in-demand layer is empty.
    "KS occupational demand flags": {
        "glob": "kdol_proj/*Occupational Employment Demand*.xlsx",
        "url": "https://www.dol.ks.gov/lmis/employment-projections",
    },
}

STALE_AFTER_DAYS = 100  # manual sources older than this get flagged


def _mtime_age_days(path: Path, today: float) -> float | None:
    if not path.exists():
        return None
    return (today - path.stat().st_mtime) / 86400.0


def _resolve_source_files(meta: dict) -> list[str]:
    """Resolve a MANUAL_SOURCES entry to DATA_DIR-relative file paths.

    Entries normally list explicit "files". An entry may instead (or also)
    carry a "glob" for sources whose filename encodes a vintage — the newest
    match by name wins, since KDOL's vintage codes sort chronologically
    (202201002032 < 202501002035). An unmatched glob is returned as-is so it
    reports MISSING rather than silently passing.
    """
    rels = list(meta.get("files", []))
    pattern = meta.get("glob")
    if pattern:
        matches = sorted(DATA_DIR.glob(pattern))
        # Must stay DATA_DIR-relative (not just .name) so globs that reach into a
        # subdirectory still resolve when re-joined to DATA_DIR.
        rels.append(matches[-1].relative_to(DATA_DIR).as_posix()
                    if matches else pattern)
    return rels


def newest_glob(pattern: str) -> Path | None:
    """Newest DATA_DIR file matching `pattern` by name, or None."""
    matches = sorted(DATA_DIR.glob(pattern))
    return matches[-1] if matches else None


def clear_caches(cache_names: list[str], dry_run: bool) -> list[str]:
    cleared = []
    for name in cache_names:
        cache = DATA_DIR / name
        if cache.exists():
            print(f"  {'[dry-run] would clear' if dry_run else '[clear]'} {name}")
            if not dry_run:
                shutil.rmtree(cache)
            cleared.append(name)
        else:
            print(f"  [skip] {name} (not present)")
    return cleared


def report_manual_sources(now_ts: float) -> list[str]:
    stale = []
    print("\n=== Manual-download sources (no public API) ===")
    for label, meta in MANUAL_SOURCES.items():
        statuses = []
        worst_missing = False
        worst_stale = False
        for rel in _resolve_source_files(meta):
            age = _mtime_age_days(DATA_DIR / rel, now_ts)
            if age is None:
                statuses.append(f"MISSING ({rel})")
                worst_missing = True
            elif age > STALE_AFTER_DAYS:
                statuses.append(f"STALE {age:.0f}d ({rel})")
                worst_stale = True
            else:
                statuses.append(f"ok {age:.0f}d")
        flag = "  !! " if (worst_missing or worst_stale) else "     "
        print(f"{flag}{label}: {'; '.join(statuses)}")
        if worst_missing or worst_stale:
            stale.append(f"{label} -> {meta['url']}")
    if not stale:
        print("     All manual sources present and fresh.")
    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly KS Workforce Dashboard refresh")
    parser.add_argument("--state", default="20", help="State FIPS (default 20 = Kansas)")
    parser.add_argument("--dry-run", action="store_true", help="Show actions, change nothing")
    parser.add_argument("--keep-annual-cache", action="store_true",
                        help="Only clear monthly series (LAUS/JOLTS); keep annual caches")
    parser.add_argument("--sims", default=2000, type=int, help="Monte Carlo sims per county")
    args = parser.parse_args()

    # time.time() is the wall clock; staleness comparison only, fine for a driver.
    import time
    now_ts = time.time()

    print("=" * 60)
    print(f"  KS Workforce Dashboard refresh — state {args.state}")
    print("=" * 60)

    caches = list(MONTHLY_API_CACHES)
    if not args.keep_annual_cache:
        caches += ANNUAL_API_CACHES

    print("\n=== Clearing API caches (forces live re-fetch) ===")
    clear_caches(caches, args.dry_run)

    if args.dry_run:
        print("\n[dry-run] would run: run_forecast.py --all and validate_outputs.py")
        report_manual_sources(now_ts)
        print("\n[dry-run] complete — no changes made.")
        return 0

    # ── Prepare manual inputs BEFORE the pipeline ─────────────────────────
    # These parsers must run before run_forecast.py so their outputs are present
    # when the pipeline consumes them. In particular the SSA parser writes the
    # ssa_cache file that run_forecast's Step 15 reads to build the participation
    # model's disability layer — if it ran after, the disability adjustment would
    # be a cycle stale (or absent on a fresh cache).

    # KDOL labor force export (KS-only). run_forecast.py does NOT regenerate the
    # KDOL labor-force outputs — the parser is standalone. A missing export is a
    # warning, not a failure (outputs persist from the prior run).
    if args.state.zfill(2) == "20":
        labforce_xls = DATA_DIR / "kdol_cache" / "labforce__99999999.xls"
        if labforce_xls.exists():
            print("\n=== Parsing KDOL labor force export ===")
            lf = subprocess.run(
                [sys.executable, "scripts/parse_manual_kdol_labforce.py",
                 "--state", args.state, "--input", str(labforce_xls)],
                cwd=str(BASE_DIR),
            )
            if lf.returncode != 0:
                print(f"  !! KDOL labforce parse FAILED (exit {lf.returncode}) — "
                      f"continuing; existing kdol_labforce outputs left intact.")
        else:
            print("\n=== KDOL labor force export MISSING — skipping parse ===")
            print(f"     Re-export from {MANUAL_SOURCES['KDOL labor force']['url']} "
                  f"and save as {labforce_xls.relative_to(BASE_DIR)}")

        # KDOL industry + occupational projections (KS-only, same Telerik
        # HTML-as-.xls family as the labor force export). run_forecast.py does
        # NOT regenerate these — the parsers are standalone and the dashboard
        # reads their parquet outputs directly, so without this block the
        # ks_proj_industry / ks_occ_* outputs stay frozen at whatever vintage
        # was last parsed by hand. A missing export or a failing parse is a
        # warning, not a failure: existing outputs are left intact.
        # Prefer KDOL's published .xlsx workbooks (newest vintage wins) and fall
        # back to the legacy Telerik HTML-as-.xls exports. The published books are
        # statewide-only and carry no in-demand flags, so the occupational parser
        # also gets the companion demand workbook to join those flags back on.
        ind_src = (newest_glob("kdol_proj/*Industry Projections*.xlsx")
                   or DATA_DIR / "bls_proj_cache" / "ks_proj_manual.xls")
        occ_src = (newest_glob("kdol_proj/*Occupational Projections*.xlsx")
                   or newest_glob("occproj__*.xls"))
        demand_src = newest_glob("kdol_proj/*Occupational Employment Demand*.xlsx")
        occ_extra = ["--demand-file", str(demand_src)] if demand_src else []

        for label, script, src, extra in (
            ("KDOL industry projections",
             "scripts/parse_manual_ks_proj.py", ind_src, []),
            ("KDOL occupational projections",
             "scripts/parse_manual_ks_occproj.py", occ_src, occ_extra),
        ):
            if src is None or not src.exists():
                print(f"\n=== {label} export MISSING — skipping parse ===")
                print(f"     Download from "
                      f"{MANUAL_SOURCES['KS industry projections']['url']} "
                      f"into data/kdol_proj/ (published filename is fine)")
                continue
            print(f"\n=== Parsing {label} ({src.name}) ===")
            proc = subprocess.run(
                [sys.executable, script,
                 "--state", args.state, "--input", str(src), *extra],
                cwd=str(BASE_DIR),
            )
            if proc.returncode != 0:
                print(f"  !! {label} parse FAILED (exit {proc.returncode}) — "
                      f"continuing; existing outputs left intact.")

    # SSA disability workbook (multi-state). fetch_ssa_disability.py is a dead path
    # (SSA 403-blocks scripts + renamed the publication); the real input is the
    # oasdi_sc{YY}.xlsx workbook parsed by parse_manual_ssa.py, which reads the
    # per-state "Table 4 - {State}" sheet — the SAME workbook serves every state.
    # The parser writes the ssa_cache copy that run_forecast Step 15 then reads.
    ssa_workbooks = list((DATA_DIR / "ssa_cache").glob("oasdi_sc*.xlsx"))
    if ssa_workbooks:
        print("\n=== Parsing SSA disability workbook ===")
        ssa = subprocess.run(
            [sys.executable, "scripts/parse_manual_ssa.py", "--state", args.state],
            cwd=str(BASE_DIR),
        )
        if ssa.returncode != 0:
            print(f"  !! SSA parse FAILED (exit {ssa.returncode}) — "
                  f"continuing; existing ssa_disability output left intact.")
    else:
        print("\n=== SSA disability workbook MISSING — skipping parse ===")
        print(f"     Download (in a browser) from "
              f"{MANUAL_SOURCES['SSA disability']['url']} into data/ssa_cache/")

    # ── Run the pipeline ──────────────────────────────────────────────────
    print("\n=== Running run_forecast.py --all ===")
    run = subprocess.run(
        [sys.executable, "run_forecast.py", "--all",
         "--state", args.state, "--sims", str(args.sims)],
        cwd=str(BASE_DIR),
    )
    if run.returncode != 0:
        print(f"\n!! run_forecast.py FAILED (exit {run.returncode}) — aborting before validation.")
        return run.returncode

    # ── Validate ──────────────────────────────────────────────────────────
    print("\n=== Validating outputs ===")
    val = subprocess.run(
        [sys.executable, "scripts/validate_outputs.py", "--state", args.state],
        cwd=str(BASE_DIR),
    )
    if val.returncode != 0:
        print(f"\n!! Output validation FAILED (exit {val.returncode}). "
              f"Do NOT commit — investigate above.")
        return val.returncode

    # ── Manual-source staleness report ────────────────────────────────────
    stale = report_manual_sources(now_ts)

    print("\n" + "=" * 60)
    print("  REFRESH COMPLETE — outputs regenerated and validated.")
    if stale:
        print("  Manual sources needing a download before next run:")
        for s in stale:
            print(f"    - {s}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
