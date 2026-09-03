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

Per-source refresh (added 2026-08-20)
------------------------------------
Each upstream agency publishes on its own calendar, so refreshing everything
whenever any one source updates is wasteful and blurs the audit trail. --sources
narrows the cache clear to one source's cache, leaving every other cache intact
so the pipeline re-fetches ONLY the source that actually published. The pipeline
itself still runs `--all` (the tested path); the deterministic layers recompute
to byte-identical outputs because the seed and their caches are unchanged, so the
resulting git diff isolates the source that moved. See
docs/data-source-release-calendar.md for the release calendar the scheduled
routines are built from.

Usage:
    python refresh_dashboard.py                 # Kansas (state 20), full refresh
    python refresh_dashboard.py --state 20
    python refresh_dashboard.py --dry-run       # show what would be cleared, do nothing
    python refresh_dashboard.py --keep-annual-cache   # only clear monthly series (LAUS/JOLTS)
    python refresh_dashboard.py --sources laus --states bloc   # one source, all 5 deployed states
    python refresh_dashboard.py --sources none --states bloc   # manual sources only: re-parse + rerun
    python refresh_dashboard.py --list-sources  # print the source -> cache map and exit
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import bulk_cache

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

# ── Per-source cache map (for --sources) ──────────────────────────────────────
# Keys are the source names used by the scheduled refresh routines, one per row
# of README §5. A source whose caches list is empty has no API cache to clear:
# either it is a manual download (the parsers run every non-dry-run invocation
# regardless) or its cache is deliberately preserved.
#
# acs and ksde are opt-in ONLY. Clearing acs_cache re-downloads every vintage in
# fetch_acs.ACS_YEARS, which is a hardcoded list — so it is worth clearing only
# right after that list has been edited to add a newly published ACS vintage.
# Clearing it on any other occasion re-fetches identical years for nothing.
SOURCE_CACHES: dict[str, list[str]] = {
    "acs":            ["acs_cache"],      # opt-in; requires editing ACS_YEARS first
    "qcew":           ["qcew_cache"],
    "laus":           ["laus_cache"],
    "jolts":          ["jolts_cache"],
    "ipeds":          ["ipeds_cache"],
    "cbp":            ["cbp_cache"],
    "lodes":          ["lodes_cache"],
    "oes":            ["oes_cache"],
    "pc":             ["pc_cache"],
    "ksde":           ["ksde_cache"],     # opt-in; annual K-12 override
    # Manual downloads — no API cache to clear. Named so a routine can say what
    # it is refreshing, and so --sources rejects nothing a routine legitimately
    # passes. The manual parsers run on every non-dry-run invocation.
    "ssa":            [],
    "kdol-labforce":  [],
    "bls-proj":       [],
    "kdol-proj":      [],
    # Explicit "clear nothing" — the manual-source routines' normal setting.
    "none":           [],
}

# The deployed bloc: every state whose outputs are tracked in git and therefore
# served by the Streamlit app (see .gitignore). Kansas first so a partial run
# still leaves the primary state current. AL 01 / HI 15 / MN 27 are test builds
# with untracked outputs and are deliberately excluded.
BLOC_STATES = ["20", "08", "29", "31", "40"]  # KS, CO, MO, NE, OK

# Manual-download sources (no public API). Checked for staleness, never cleared.
MANUAL_SOURCES = {
    # KDOL labor force export from KLIC's Telerik report builder (HTML-as-.xls).
    # NOTE: This REPLACES the abandoned "KDOL UI claims" source. KDOL does not
    # expose UI claims by county x NAICS publicly (see parse_manual_kdol_labforce.py
    # docstring); fetch_kdol_ui.py is a dead path that returns empty. The labor
    # force file is the dataset that actually feeds the dashboard's pulse layer.
    "KDOL labor force": {
        # KLIC exports this either as HTML-as-.xls (Telerik report builder) or as
        # a real .xlsx. Glob both: the filename carries a fixed 99999999 sentinel
        # rather than a vintage, so the newest download is whichever file was
        # written last — see the mtime pick in refresh_state().
        "glob": "kdol_cache/labforce__*.xls*",
        "glob_by": "mtime",
        "url": "https://klic.dol.ks.gov/",
        # KDOL publishes the KS labor report monthly (3rd Friday). A single
        # flat 100-day threshold let this file drift three vintages behind
        # while still reporting "ok", so it carries its own cadence-sized
        # window: one month plus slack for a late report.
        "stale_days": 40,
    },
    # SSA OASDI "Beneficiaries by State and County" workbook (oasdi_sc{YY}.xlsx),
    # downloaded manually from SSA (the site 403-blocks scripted requests).
    # NOTE: This REPLACES the abandoned "ssa_manual.csv" fallback. The auto-
    # downloader fetch_ssa_disability.py is a dead path (403 + legacy format);
    # the real input is this workbook parsed by parse_manual_ssa.py. SSA renamed
    # the publication from /oasdi_county/ to /oasdi_sc/ and publishes annually
    # with ~18mo lag, so this only needs re-downloading once a year.
    # Globbed, not pinned: the filename carries the publication year
    # (oasdi_sc24 -> oasdi_sc25), and editions sort chronologically, so the
    # newest match wins. Pinning the 2024 filename made this report STALE
    # forever once the 2025 edition landed beside it, because it kept measuring
    # the age of the superseded file.
    "SSA disability": {
        "glob": "ssa_cache/oasdi_sc*.xlsx",
        "url": "https://www.ssa.gov/policy/docs/statcomps/oasdi_sc/index.html",
    },
    # Same fix, same reason. The glob requires a leading digit after the prefix
    # so it matches vintage-named workbooks (bls_proj_national_2025_2035.xlsx)
    # and never the legacy vintage-less bls_proj_national_manual.xlsx, which
    # would otherwise sort last ("m" > "2") and win permanently.
    "BLS national projections": {
        "glob": "bls_proj_cache/bls_proj_national_[0-9]*.xlsx",
        "url": "https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm",
    },
    # OEWS all-data workbook, the ONLY route to May 2025 state estimates: every
    # oesm25*.zip pattern returns 403 (see the URL notes in fetch_oes.py), so
    # unlike the other manual sources this one is a partial fallback — the state
    # layer fetches 2015-2024 live and needs a hand-placed file for 2025 alone.
    # Lives in data/oes_manual/ rather than data/oes_cache/ because
    # ANNUAL_API_CACHES rmtree's the latter, which would delete it.
    #
    # 400-day window, not the flat 100-day default: OEWS publishes annually, so
    # a 100-day threshold would report this file stale within months of a
    # correct download and stay that way until the next edition — the same
    # false-alarm shape that made the KDOL labour-force file carry its own
    # 40-day window for the opposite reason.
    "OES state all-data workbook": {
        "glob": "oes_manual/all_data_M_[0-9]*.xls*",
        "url": "https://www.bls.gov/oes/tables.htm",
        "stale_days": 400,
    },
    # KDOL industry projections (KLIC Telerik report builder, HTML-as-.xls).
    # NOTE: This REPLACES the abandoned "KS state projections" source, which
    # pointed at bls_proj_cache/ks_proj_manual.xlsx — a file that never existed.
    # fetch_bls_proj.fetch_ks_state_projections() was that dead path: it called
    # pd.ExcelFile() on what is actually an HTML <table>, so it raised
    # "Excel file format cannot be determined" no matter what the file was
    # renamed to. It was DELETED 2026-08-27 — it had been warning on every
    # refresh while contributing zero rows. The real input is the .xls below,
    # parsed by scripts/parse_manual_ks_proj.py into ks_proj_industry.parquet.
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

# Default staleness window for a manual source, in days. Sized for the annual
# publications (SSA, BLS/KDOL projections): well inside a year, so a missed
# download surfaces long before the next cycle. Sources that publish faster
# override it with their own "stale_days" — see KDOL labor force above.
STALE_AFTER_DAYS = 100


def _mtime_age_days(path: Path, today: float) -> float | None:
    if not path.exists():
        return None
    return (today - path.stat().st_mtime) / 86400.0


def _resolve_source_files(meta: dict) -> list[str]:
    """Resolve a MANUAL_SOURCES entry to DATA_DIR-relative file paths.

    Entries normally list explicit "files". An entry may instead (or also)
    carry a "glob" for sources whose filename encodes a vintage — the newest
    match by name wins, since KDOL's vintage codes sort chronologically
    (202201002032 < 202501002035). An entry whose filename does NOT encode a
    vintage sets "glob_by": "mtime" instead, so the most recently downloaded
    file wins regardless of extension. An unmatched glob is returned as-is so it
    reports MISSING rather than silently passing.
    """
    rels = list(meta.get("files", []))
    pattern = meta.get("glob")
    if pattern:
        key = (lambda p: p.stat().st_mtime) if meta.get("glob_by") == "mtime" else None
        matches = sorted(DATA_DIR.glob(pattern), key=key)
        # Must stay DATA_DIR-relative (not just .name) so globs that reach into a
        # subdirectory still resolve when re-joined to DATA_DIR.
        rels.append(matches[-1].relative_to(DATA_DIR).as_posix()
                    if matches else pattern)
    return rels


def newest_glob(pattern: str) -> Path | None:
    """Newest DATA_DIR file matching `pattern` by name, or None."""
    matches = sorted(DATA_DIR.glob(pattern))
    return matches[-1] if matches else None


def newest_by_mtime(pattern: str) -> Path | None:
    """Most recently modified DATA_DIR file matching `pattern`, or None.

    For sources whose filename carries no vintage (KDOL's labor force export
    uses a fixed 99999999 sentinel), download time is the only ordering
    available — and it must not be confused by the extension, since KLIC emits
    both .xls and .xlsx for the same report.
    """
    matches = sorted(DATA_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _reads_as_absent(path: Path, probes: int = 3, delay: float = 0.4) -> bool:
    """True only if `path` reads as absent on every probe.

    The vault lives under OneDrive, where a stat can transiently fail on a
    directory that is really there. On 2026-08-27 a CBP refresh printed
    "[skip] cbp_cache (not present)" for a cache that demonstrably existed; the
    fetch layer then also read it as absent and re-downloaded, so the outcome
    was right by luck rather than by design. Probing more than once keeps a sync
    hiccup from being reported — or acted on — as a missing cache.
    """
    for attempt in range(probes):
        if path.exists():
            return False
        if attempt < probes - 1:
            time.sleep(delay)
    return True


def clear_caches(cache_names: list[str], dry_run: bool) -> list[str]:
    """Clear the named caches, reporting what actually happened.

    Every message below follows the action rather than a separate pre-check, so
    "[skip]" can never be printed for a cache that was in fact removed.
    """
    cleared = []
    for name in cache_names:
        cache = DATA_DIR / name

        if _reads_as_absent(cache):
            print(f"  [skip] {name} (not present)")
            continue

        if dry_run:
            print(f"  [dry-run] would clear {name}")
            cleared.append(name)
            continue

        try:
            shutil.rmtree(cache)
        except FileNotFoundError:
            print(f"  [skip] {name} (vanished before it could be cleared)")
            continue
        except OSError as exc:
            # A locked or partially-removed cache is worse than an untouched one:
            # the fetch layer may serve half a vintage. Say so loudly.
            print(f"  [WARN] {name} — could not clear: {exc}")
            continue

        if cache.exists():
            print(f"  [WARN] {name} — rmtree returned but the path still exists; "
                  f"treat this cache as suspect")
            continue

        print(f"  [clear] {name}")
        cleared.append(name)

    return cleared


def report_manual_sources(now_ts: float) -> list[str]:
    stale = []
    print("\n=== Manual-download sources (no public API) ===")
    for label, meta in MANUAL_SOURCES.items():
        statuses = []
        worst_missing = False
        worst_stale = False
        stale_days = meta.get("stale_days", STALE_AFTER_DAYS)
        for rel in _resolve_source_files(meta):
            age = _mtime_age_days(DATA_DIR / rel, now_ts)
            if age is None:
                statuses.append(f"MISSING ({rel})")
                worst_missing = True
            elif age > stale_days:
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


def resolve_sources(names: list[str]) -> list[str]:
    """Map --sources names to the caches they own, rejecting unknown names.

    Returns caches in SOURCE_CACHES declaration order (deduped) so the printed
    clear list is stable regardless of the order the routine passed them in.
    """
    unknown = [n for n in names if n not in SOURCE_CACHES]
    if unknown:
        raise SystemExit(
            f"Unknown --sources value(s): {', '.join(unknown)}\n"
            f"Valid: {', '.join(SOURCE_CACHES)}"
        )
    selected = {c for n in names for c in SOURCE_CACHES[n]}
    ordered: list[str] = []
    for cache_list in SOURCE_CACHES.values():
        for cache in cache_list:
            if cache in selected and cache not in ordered:
                ordered.append(cache)
    return ordered


def refresh_state(state: str, sims: int) -> int:
    """Run the manual parsers, pipeline, and validation for one state.

    Cache clearing is NOT done here — it happens once, before the state loop, so
    a multi-state refresh re-fetches each source once instead of once per state.
    """
    print("\n" + "-" * 60)
    print(f"  State {state}")
    print("-" * 60)

    # ── Prepare manual inputs BEFORE the pipeline ─────────────────────────
    # These parsers must run before run_forecast.py so their outputs are present
    # when the pipeline consumes them. In particular the SSA parser writes the
    # ssa_cache file that run_forecast's Step 15 reads to build the participation
    # model's disability layer — if it ran after, the disability adjustment would
    # be a cycle stale (or absent on a fresh cache).

    # KDOL labor force export (KS-only). run_forecast.py does NOT regenerate the
    # KDOL labor-force outputs — the parser is standalone. A missing export is a
    # warning, not a failure (outputs persist from the prior run).
    if state.zfill(2) == "20":
        # KLIC exports either HTML-as-.xls or a real .xlsx and the filename
        # carries no vintage, so take whichever was downloaded most recently
        # rather than pinning an extension. The parser detects the format by
        # content, so either one is fine from here.
        labforce_src = newest_by_mtime("kdol_cache/labforce__*.xls*")
        if labforce_src is not None:
            print(f"\n=== Parsing KDOL labor force export ({labforce_src.name}) ===")
            lf = subprocess.run(
                [sys.executable, "scripts/parse_manual_kdol_labforce.py",
                 "--state", state, "--input", str(labforce_src)],
                cwd=str(BASE_DIR),
            )
            if lf.returncode != 0:
                print(f"  !! KDOL labforce parse FAILED (exit {lf.returncode}) — "
                      f"continuing; existing kdol_labforce outputs left intact.")
        else:
            print("\n=== KDOL labor force export MISSING — skipping parse ===")
            print(f"     Re-export from {MANUAL_SOURCES['KDOL labor force']['url']} "
                  f"into data/kdol_cache/ (labforce__*.xls or .xlsx)")

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
                 "--state", state, "--input", str(src), *extra],
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
            [sys.executable, "scripts/parse_manual_ssa.py", "--state", state],
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
         "--state", state, "--sims", str(sims)],
        cwd=str(BASE_DIR),
    )
    if run.returncode != 0:
        print(f"\n!! run_forecast.py FAILED (exit {run.returncode}) — aborting before validation.")
        return run.returncode

    # ── Validate ──────────────────────────────────────────────────────────
    print("\n=== Validating outputs ===")
    val = subprocess.run(
        [sys.executable, "scripts/validate_outputs.py", "--state", state],
        cwd=str(BASE_DIR),
    )
    if val.returncode != 0:
        print(f"\n!! Output validation FAILED (exit {val.returncode}). "
              f"Do NOT commit — investigate above.")
        return val.returncode

    return 0


def resolve_states(spec: str) -> list[str]:
    """Parse --states into a list of zero-padded FIPS codes.

    "bloc" expands to BLOC_STATES (every state the deployed dashboard serves).
    Anything else is a comma-separated list of FIPS codes.
    """
    if spec.strip().lower() == "bloc":
        return list(BLOC_STATES)
    states = [s.strip().zfill(2) for s in spec.split(",") if s.strip()]
    if not states:
        raise SystemExit("--states resolved to an empty list")
    return states


def main() -> int:
    parser = argparse.ArgumentParser(description="KS Workforce Dashboard refresh driver")
    parser.add_argument("--state", default="20", help="State FIPS (default 20 = Kansas)")
    parser.add_argument("--states", default=None,
                        help="Comma-separated FIPS list, or 'bloc' for every deployed "
                             f"state ({','.join(BLOC_STATES)}). Overrides --state.")
    parser.add_argument("--sources", default=None,
                        help="Comma-separated source names to refresh; only their caches "
                             "are cleared. 'none' clears nothing (manual-source runs). "
                             "Omit for the full monthly refresh. "
                             f"Valid: {','.join(SOURCE_CACHES)}")
    parser.add_argument("--list-sources", action="store_true",
                        help="Print the source -> cache map and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show actions, change nothing")
    parser.add_argument("--keep-annual-cache", action="store_true",
                        help="Only clear monthly series (LAUS/JOLTS); keep annual caches")
    parser.add_argument("--sims", default=2000, type=int, help="Monte Carlo sims per county")
    args = parser.parse_args()

    if args.list_sources:
        print("source           caches cleared")
        print("-" * 48)
        for name, caches in SOURCE_CACHES.items():
            print(f"{name:<16} {', '.join(caches) if caches else '(manual — none)'}")
        print(f"\nbloc states: {', '.join(BLOC_STATES)}")
        return 0

    # time.time() is the wall clock; staleness comparison only, fine for a driver.
    import time
    now_ts = time.time()

    states = resolve_states(args.states) if args.states else [args.state.zfill(2)]

    if args.sources:
        source_names = [s.strip() for s in args.sources.split(",") if s.strip()]
        caches = resolve_sources(source_names)
        scope = f"sources={','.join(source_names)}"
    else:
        source_names = None
        caches = list(MONTHLY_API_CACHES)
        if not args.keep_annual_cache:
            caches += ANNUAL_API_CACHES
        scope = "full refresh"

    print("=" * 60)
    print(f"  KS Workforce Dashboard refresh — {scope}")
    print(f"  States: {', '.join(states)}")
    print("=" * 60)

    print("\n=== Clearing API caches (forces live re-fetch) ===")
    if caches:
        clear_caches(caches, args.dry_run)
    elif source_names:
        print("  (no API caches for these sources — manual downloads only)")

    if args.dry_run:
        print(f"\n[dry-run] would run, for each of {len(states)} state(s): "
              f"run_forecast.py --all and validate_outputs.py")
        report_manual_sources(now_ts)
        print("\n[dry-run] complete — no changes made.")
        return 0

    # ── Refresh each state ────────────────────────────────────────────────
    # Caches were cleared once above, so the first state re-fetches live data and
    # the rest reuse it. A failure on one state stops the run: a half-refreshed
    # bloc is easier to reason about than one where an unknown state silently
    # failed mid-loop.
    #
    # QCEW and IPEDS pull NATIONAL archives but cache only their per-state slice,
    # so without a shared archive cache each state re-downloads the same ~140 MB
    # QCEW ZIP per year — 7 GB of transfer across the bloc to produce 200 KB of
    # parquet. Open a session cache so the states share one download each. It is
    # session-scoped on purpose (see bulk_cache.py): persisting it would let a
    # stale archive survive the cache clear this driver just performed.
    bulk_dir = None
    if len(states) > 1:
        bulk_dir = Path(tempfile.mkdtemp(prefix="kswf-bulk-"))
        os.environ[bulk_cache.ENV_VAR] = str(bulk_dir)
        print(f"\n=== Sharing national bulk archives across {len(states)} states ===")
        print(f"    Cache: {bulk_dir} (removed when this run finishes)")
        print(f"    Expect ~1.4 GB transient disk use while QCEW years download.")

    try:
        for state in states:
            rc = refresh_state(state, args.sims)
            if rc != 0:
                print(f"\n!! State {state} FAILED (exit {rc}) — stopping. "
                      f"States already completed are refreshed and valid.")
                return rc
    finally:
        if bulk_dir is not None:
            os.environ.pop(bulk_cache.ENV_VAR, None)
            shutil.rmtree(bulk_dir, ignore_errors=True)
            print(f"\n=== Removed shared bulk-archive cache ===")

    # ── Manual-source staleness report ────────────────────────────────────
    stale = report_manual_sources(now_ts)

    print("\n" + "=" * 60)
    print(f"  REFRESH COMPLETE — {len(states)} state(s) regenerated and validated.")
    if stale:
        print("  Manual sources needing a download before next run:")
        for s in stale:
            print(f"    - {s}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
