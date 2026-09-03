"""
Audit every fetcher for the "unvalidated cache served on sight" defect.

WHY THIS EXISTS
---------------
On 2026-09-02 the JOLTS layer was found frozen at reference month 2023-12 —
31 months stale — while its scheduled refresh routine fired every month and
reported success each time. Two things combined to make that silent:

  1. `fetch_jolts.py` held a hardcoded `JOLTS_YEARS = range(2015, 2024)`, so
     the requested years never advanced.
  2. The cache filename (`jolts_U.parquet`) carried NO vintage, and the code
     returned that parquet on sight if the file merely existed — without ever
     asking whether it contained the years being requested.

Either alone is survivable. Together they are undetectable from the outside:
the fetch "succeeds", the pipeline "succeeds", validation "passes", and the
dashboard serves years-old numbers with a current timestamp.

This is a CLASS of defect, not one bug. ACS, CBP, and BLS national
projections each hit the year-list half of it (see the release calendar's
"Sources that need a code change" section). This script finds the second
half — the cache contract — across every fetcher, so the next instance is
caught by running a command instead of by noticing a suspicious chart.

WHAT IT CHECKS
--------------
Static (no network, no credentials):
  For each `.exists()`-guarded cache read, classify the cache CONTRACT:
    VINTAGE-KEYED  the filename interpolates a year, so a new vintage is a
                   new filename and therefore a cache miss. Safe by
                   construction — the year list is then the only risk.
    VALIDATED      fixed filename, but the code compares the cached contents
                   against the requested years and re-fetches when short.
    UNVALIDATED    fixed filename returned on sight. THE DEFECT.

Data (reads local parquet only):
  Compare the years each fetcher would REQUEST against the years actually
  present in its cache and in `data/outputs/`. A shortfall on an UNVALIDATED
  source is the live signature of the JOLTS bug.

LIMITS — read these before trusting a clean result
--------------------------------------------------
This is a heuristic AST scan, not a proof. It recognises the shapes this
codebase actually uses; a cache read written in a novel shape may be missed
entirely. A VINTAGE-KEYED verdict says the cache cannot go stale, NOT that
the data is current — a frozen year list defeats it (that is exactly the ACS
and CBP failure). And "years requested" is read from the fetcher's own
defaults, which is the thing that was wrong in every instance so far, so a
green row here still does not tell you the source is up to date against the
AGENCY. Only checking the agency's release page does that.

Usage:
    python scripts/audit_cache_freshness.py
    python scripts/audit_cache_freshness.py --json
    python scripts/audit_cache_freshness.py --strict   # exit 1 on UNVALIDATED
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import importlib
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

# Names that, when interpolated into a cache filename, make it vintage-keyed.
_YEAR_TOKENS = ("year", "yy", "vintage", "base", "proj", "cycle")

# Names suggesting a freshness guard on a fixed-name cache.
_GUARD_TOKENS = ("missing", "stale", "short", "incomplete", "requested", "fresh")

# The shared guard in cache_freshness.py. A call to this IS the freshness
# check, so a fetcher that routes its cache read through it is VALIDATED by
# construction — and the `if cache_file.exists()` shape disappears, which
# would otherwise make this scan report "no cache read found".
_GUARD_HELPERS = {"load_fresh_cache"}

# Where each source's cache lives and which output carries its vintage.
# years_attr: module-level constant or zero-arg callable giving requested years.
REGISTRY: list[dict] = [
    {"source": "jolts",    "module": "fetch_jolts",    "years_attr": "default_jolts_years",
     "cache": "data/jolts_cache/jolts_U.parquet",        "output": "data/outputs/jolts.parquet"},
    {"source": "laus",     "module": "fetch_laus",     "years_attr": "LAUS_YEARS",
     "cache": "data/laus_cache/laus_s20.parquet",        "output": "data/outputs/laus_s20.parquet"},
    {"source": "oes",      "module": "fetch_oes",      "years_attr": "OES_YEARS",
     "cache": "data/oes_cache/oes_state_s20.parquet",    "output": "data/outputs/oes_state_s20.parquet"},
    {"source": "lodes",    "module": "fetch_lodes",    "years_attr": "LODES_YEARS",
     "cache": "data/lodes_cache/lodes_s20_all.parquet",  "output": "data/outputs/lodes_s20.parquet"},
    {"source": "ipeds",    "module": "fetch_ipeds",    "years_attr": "IPEDS_YEARS",
     "cache": "data/ipeds_cache/ipeds_s20_all.parquet",  "output": "data/outputs/ipeds_s20.parquet"},
    {"source": "cbp",      "module": "fetch_cbp",      "years_attr": "CBP_YEARS",
     "cache": "data/cbp_cache/cbp_s20.parquet",          "output": "data/outputs/cbp_s20.parquet"},
    {"source": "qcew",     "module": "fetch_qcew",     "years_attr": "QCEW_YEARS",
     "cache": None,                                       "output": None},
    {"source": "acs",      "module": "fetch_acs",      "years_attr": "ACS_YEARS",
     "cache": None,                                       "output": "data/outputs/acs_combined_s20.parquet"},
    {"source": "ksde",     "module": "fetch_ksde",     "years_attr": "KSDE_YEARS",
     "cache": "data/ksde_cache/ksde.parquet",            "output": "data/outputs/ksde.parquet"},
    # Manual: the live path is scripts/parse_manual_ssa.py reading a
    # hand-placed workbook, so SSA_YEARS is vestigial for the API path and
    # comparing it against the output is meaningless (the workbook yields a
    # single data year). Contract still worth auditing; year math is not.
    {"source": "ssa",      "module": "fetch_ssa_disability", "years_attr": "SSA_YEARS",
     "manual": True,
     "cache": "data/ssa_cache/ssa_disability_s20.parquet",
     "output": "data/outputs/ssa_disability_s20.parquet"},
    {"source": "kdol_ui",  "module": "fetch_kdol_ui",  "years_attr": None,
     "cache": "data/kdol_cache/kdol_ui.parquet",         "output": None},
    {"source": "bls_proj", "module": "fetch_bls_proj", "years_attr": None,
     "cache": None,        "output": "data/outputs/bls_proj_sector_outlook.parquet"},
    {"source": "projections_central", "module": "fetch_projections_central", "years_attr": None,
     "cache": "data/pc_cache/pc_longterm_s20.parquet",   "output": "data/outputs/pc_occ_proj_s20.parquet"},
]


# ══════════════════════════════════════════════════════════════════════════
#  Static pass — classify each cache read's contract
# ══════════════════════════════════════════════════════════════════════════

def _is_read_parquet(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_parquet"
    )


def _calls_exists(test: ast.AST) -> list[str]:
    """Cache-path variable names whose .exists() the test consults."""
    names = []
    for n in ast.walk(test):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "exists"
            and isinstance(n.func.value, ast.Name)
        ):
            names.append(n.func.value.id)
    return names


def _assigned_paths(func: ast.FunctionDef) -> dict[str, ast.AST]:
    """Map local variable name -> the expression assigned to it."""
    out: dict[str, ast.AST] = {}
    for n in ast.walk(func):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    out[tgt.id] = n.value
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value:
            out[n.target.id] = n.value
    return out


def _is_vintage_keyed(expr: ast.AST | None) -> bool:
    """True if the path expression interpolates a year-like name."""
    if expr is None:
        return False
    for n in ast.walk(expr):
        # f-string interpolations
        if isinstance(n, ast.FormattedValue):
            for sub in ast.walk(n.value):
                if isinstance(sub, ast.Name) and any(
                    t in sub.id.lower() for t in _YEAR_TOKENS
                ):
                    return True
                if isinstance(sub, ast.Attribute) and any(
                    t in sub.attr.lower() for t in _YEAR_TOKENS
                ):
                    return True
        # .format(...) / % style
        if isinstance(n, ast.keyword) and n.arg and any(
            t in n.arg.lower() for t in _YEAR_TOKENS
        ):
            return True
    return False


def _has_freshness_guard(func: ast.FunctionDef) -> bool:
    """
    True if the function appears to compare cached contents against the
    requested years rather than trusting file existence.
    """
    for n in ast.walk(func):
        if isinstance(n, ast.Name) and any(t in n.id.lower() for t in _GUARD_TOKENS):
            return True
        # ...["year"].unique() / .isin(years) over a *cached* frame
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in ("unique", "isin", "difference", "issubset"):
                src = ast.dump(n)
                if "year" in src.lower() and "cach" in src.lower():
                    return True
    return False


def scan_module(path: Path) -> list[dict]:
    """Classify every .exists()-guarded cache read in one fetcher."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    findings: list[dict] = []

    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        paths = _assigned_paths(func)
        guarded = _has_freshness_guard(func)

        # A cache read routed through the shared guard.
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _GUARD_HELPERS
            ):
                findings.append({
                    "file": path.name,
                    "function": func.name,
                    "line": node.lineno,
                    "cache_var": node.func.id + "()",
                    "verdict": "VALIDATED",
                })

        for node in ast.walk(func):
            if not isinstance(node, ast.If):
                continue
            var_names = _calls_exists(node.test)
            if not var_names:
                continue
            # Does this branch actually read a cached parquet?
            reads = [c for c in ast.walk(node) if _is_read_parquet(c)]
            if not reads:
                continue

            for var in var_names:
                expr = paths.get(var)
                if _is_vintage_keyed(expr):
                    verdict = "VINTAGE-KEYED"
                elif guarded:
                    verdict = "VALIDATED"
                else:
                    verdict = "UNVALIDATED"
                findings.append({
                    "file": path.name,
                    "function": func.name,
                    "line": node.lineno,
                    "cache_var": var,
                    "verdict": verdict,
                })
    return findings


# ══════════════════════════════════════════════════════════════════════════
#  Data pass — requested years vs. years actually held
# ══════════════════════════════════════════════════════════════════════════

def _years_in(path: Path) -> list[int] | None:
    if not path.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(path)
    except Exception:
        return None
    for col in ("year", "base_year"):
        if col in df.columns and not df.empty:
            return sorted(int(y) for y in df[col].dropna().unique())
    return []


def _requested_years(entry: dict) -> list[int] | None:
    attr = entry.get("years_attr")
    if not attr:
        return None
    try:
        mod = importlib.import_module(entry["module"])
        val = getattr(mod, attr, None)
        if callable(val):
            val = val()
        return sorted(int(y) for y in val) if val else None
    except Exception:
        return None


def _fmt_years(years: list[int] | None) -> str:
    if years is None:
        return "n/a"
    if not years:
        return "none"
    return f"{min(years)}-{max(years)}"


def audit() -> dict:
    sys.path.insert(0, str(BASE_DIR))

    static: list[dict] = []
    for path in sorted(BASE_DIR.glob("fetch_*.py")):
        static.extend(scan_module(path))

    # Worst verdict per module drives the source's contract rating.
    rank = {"VINTAGE-KEYED": 0, "VALIDATED": 0, "UNVALIDATED": 2}
    by_module: dict[str, str] = {}
    for f in static:
        mod = f["file"][:-3]
        if rank[f["verdict"]] >= rank.get(by_module.get(mod, "VINTAGE-KEYED"), 0):
            if mod not in by_module or rank[f["verdict"]] > rank[by_module[mod]]:
                by_module[mod] = f["verdict"]

    rows: list[dict] = []
    for entry in REGISTRY:
        requested = _requested_years(entry)
        cache_years = _years_in(BASE_DIR / entry["cache"]) if entry.get("cache") else None
        out_years = _years_in(BASE_DIR / entry["output"]) if entry.get("output") else None

        manual = bool(entry.get("manual"))
        short_by = []
        if requested and out_years and not manual:
            short_by = [y for y in requested if y not in out_years]

        contract = by_module.get(entry["module"], "no cache read found")

        # The trap this audit exists to expose. When a source is UNVALIDATED
        # *and* its newest requested year is well behind the present, the two
        # halves of the JOLTS bug are both in place: the year list cannot
        # advance, and the cache would not notice if it did. `short_by` is
        # then EMPTY — requested matches output precisely because both are
        # frozen — so the absence of a shortfall proves nothing here.
        needs_agency_check = (
            contract == "UNVALIDATED"
            and not manual
            and bool(requested)
            and max(requested) < dt.date.today().year - 1
        )

        rows.append({
            "source": entry["source"],
            "contract": contract,
            "manual": manual,
            "requested": requested,
            "cache_years": cache_years,
            "output_years": out_years,
            "output_short_of_requested": short_by,
            "needs_agency_check": needs_agency_check,
            "years_behind_present": (
                dt.date.today().year - max(requested) if requested else None
            ),
        })

    return {
        "generated": dt.date.today().isoformat(),
        "static_findings": static,
        "sources": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any UNVALIDATED cache read is found")
    args = ap.parse_args()

    result = audit()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=" * 78)
        print("  Cache-contract audit — 'unvalidated cache served on sight'")
        print("=" * 78)
        print()
        print(f"{'source':22s} {'contract':16s} {'requested':11s} {'in output':11s}  note")
        print("-" * 78)
        for r in result["sources"]:
            if r["output_short_of_requested"]:
                note = "output SHORT of requested: " + ",".join(
                    str(y) for y in r["output_short_of_requested"][:4]
                )
            elif r["needs_agency_check"]:
                note = f"frozen {r['years_behind_present']}y behind — VERIFY vs agency"
            elif r["manual"]:
                note = "manual source — year math n/a"
            else:
                note = "-"
            print(
                f"{r['source']:22s} {r['contract']:16s} "
                f"{_fmt_years(r['requested']):11s} {_fmt_years(r['output_years']):11s}  {note}"
            )

        unval = [f for f in result["static_findings"] if f["verdict"] == "UNVALIDATED"]
        print()
        print(f"UNVALIDATED cache reads: {len(unval)}")
        for f in unval:
            print(f"  {f['file']}:{f['line']}  {f['function']}()  [{f['cache_var']}]")

        flagged = [r for r in result["sources"] if r["needs_agency_check"]]
        if flagged:
            print()
            print("!" * 78)
            print("  BOTH HALVES OF THE JOLTS BUG PRESENT — verify these against the")
            print("  agency's release page before trusting the dashboard's vintage:")
            print("!" * 78)
            for r in flagged:
                print(
                    f"  {r['source']:20s} year list ends {max(r['requested'])}, "
                    f"{r['years_behind_present']}y behind {dt.date.today().year}; "
                    f"cache would not notice a newer vintage"
                )
            print()
            print("  These show no shortfall above, and that is the point: requested and")
            print("  output agree BECAUSE both are frozen. A matching pair is not")
            print("  evidence of currency.")

        print()
        print("Legend")
        print("  VINTAGE-KEYED  filename carries a year -> new vintage misses cache. Safe.")
        print("  VALIDATED      fixed name, but contents checked against requested years.")
        print("  UNVALIDATED    fixed name returned on sight. Can serve stale data forever.")
        print()
        print("A clean run does NOT mean the data is current — it means the CACHE cannot")
        print("hide staleness. A frozen year list still can. Verify vintages against the")
        print("agency's release page; see docs/data-source-release-calendar.md.")

    if args.strict and any(
        f["verdict"] == "UNVALIDATED" for f in result["static_findings"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
