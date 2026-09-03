"""
Shared cache-freshness guard for the fetch_* layer.

WHY THIS EXISTS
---------------
Every fetcher caches its pull as a parquet. Several of them keyed that cache on
a FIXED filename — `laus_s20.parquet`, `ksde.parquet`, `lodes_s20_all.parquet`
— and returned it whenever the file merely existed, without ever asking whether
it held the years being requested. Combined with a hardcoded year list, that
makes staleness completely silent: the fetch "succeeds", the pipeline
"succeeds", validation passes, and the dashboard serves years-old numbers under
a current timestamp.

JOLTS sat at reference month 2023-12 for 31 months that way, while its
scheduled routine fired monthly and reported success every time. CBP sat on a
year-old vintage for over a year. See docs/data-source-release-calendar.md
§"Sources that need a code change".

This module is the one implementation of the guard CBP grew on 2026-08-27 and
JOLTS on 2026-09-02, factored out on 2026-09-02 when it became clear five more
fetchers needed it. One copy means one place to fix.

WHAT A CALLER MUST STILL DO
---------------------------
This guard only stops a *cache* from hiding staleness. It cannot make a frozen
year list advance — that is the other half of the bug, and it lives in each
fetcher's YEARS constant. Two rules for those:

  1. **Only list years the agency has actually published.** Verify against the
     agency, not against what the fetcher currently wants. A year that will
     never resolve makes `save_if_complete` refuse to cache forever, turning
     every run into a full re-download.
  2. **Do not cache a partial pull.** Use `save_if_complete` so the next run
     retries the missing years instead of inheriting a gap that now looks
     authoritative.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_fresh_cache(
    cache_file: Path | None,
    wanted: list[int] | set[int] | None,
    label: str,
    year_col: str = "year",
) -> pd.DataFrame | None:
    """
    Return the cached frame only if it covers every year in `wanted`.

    Returns None — meaning "re-fetch" — when the cache is absent, unreadable,
    lacks the year column, or is short of the requested years. Every rejection
    prints its reason, because the failure this replaces was silent.

    Passing `wanted` as None or empty skips the year comparison and accepts any
    existing cache. Only correct for sources with no meaningful year axis
    (a current-snapshot pull); never use it to sidestep a year check.
    """
    if cache_file is None or not cache_file.exists():
        return None

    try:
        cached = pd.read_parquet(cache_file)
    except Exception as exc:
        print(f"  [cache unreadable] {label} — {type(exc).__name__}: {exc}; re-fetching")
        return None

    if not wanted:
        print(f"  [cache] {label}")
        return cached

    if cached.empty:
        print(f"  [cache stale] {label}: cached frame is empty — re-fetching")
        return None

    if year_col not in cached.columns:
        print(f"  [cache stale] {label}: no '{year_col}' column "
              f"(columns: {list(cached.columns)[:8]}) — re-fetching")
        return None

    have = {int(y) for y in pd.to_numeric(cached[year_col], errors="coerce").dropna()}
    missing = sorted(set(wanted) - have)
    if missing:
        print(f"  [cache stale] {label} missing {missing} — re-fetching")
        return None

    print(f"  [cache] {label}")
    return cached


def save_if_complete(
    df: pd.DataFrame,
    cache_file: Path | None,
    wanted: list[int] | set[int] | None,
    obtained: list[int] | set[int] | None,
    label: str,
) -> list[int]:
    """
    Write the cache only when the pull covered every requested year.

    Returns the sorted list of missing years (empty when complete). A partial
    pull is deliberately NOT cached: caching it would freeze the gap in place
    and the next run would serve it as though it were complete — which is how
    a short pull becomes a permanent one.
    """
    # Built with explicit None checks rather than `wanted or ()`: callers
    # pass `sorted(df["year"].unique())`, and a bare truthiness test on a
    # numpy array raises "truth value of an array is ambiguous". Lists are
    # safe today, so this is guarding the next caller, not the current ones.
    missing = sorted(
        {int(y) for y in (wanted if wanted is not None else ())}
        - {int(y) for y in (obtained if obtained is not None else ())}
    )

    if cache_file is None:
        return missing

    if missing:
        print(f"  [not cached] {label}: {len(missing)} requested year(s) absent "
              f"{missing} — cache left untouched so the next run re-fetches")
        return missing

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_file, index=False)
    print(f"  [saved] {cache_file.name}  ({len(df)} rows)")
    return missing
