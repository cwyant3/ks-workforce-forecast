"""
bulk_cache.py
Process-shared cache for the large NATIONAL bulk archives that several fetchers
download in full and then filter down to a single state.

Why this exists
---------------
fetch_qcew and fetch_ipeds pull a national archive and cache only the per-state
slice they extracted, discarding the archive. That keeps the on-disk cache tiny,
but it means a multi-state refresh re-downloads the same archive once per state.
The QCEW annual by-area ZIP is ~140 MB per year, so the five deployed states
across ten years cost roughly 7 GB of transfer to produce 200 KB of parquet —
and it scales linearly with the state count (all 51 jurisdictions would be
~71 GB per refresh, which BLS would rightly start refusing).

This module caches the raw archive bytes on disk for the duration of ONE refresh
session, so every state in that session shares a single download.

Scope is a session, deliberately not a permanent cache
------------------------------------------------------
A permanent archive cache would defeat refresh_dashboard.py, which clears the
derived per-state caches precisely to force a live re-fetch. A stale archive
sitting on disk would silently republish last quarter's numbers as if they were
fresh — the exact failure the refresh driver exists to prevent.

The location is also outside the project tree. The archives are large, and
data/ lives under OneDrive, so caching them there would push over a gigabyte of
ZIPs into a synced folder.

Usage
-----
refresh_dashboard.py opens a session when it has more than one state to run: it
creates a temp directory, exports KSWF_BULK_CACHE, and removes the directory
when the run finishes. Fetchers just wrap their download call in
cached_download(). With the variable unset — a bare
`python run_forecast.py --state 20` — nothing is cached and behavior is
byte-for-byte what it was before this module existed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

ENV_VAR = "KSWF_BULK_CACHE"


def session_dir() -> Path | None:
    """The active bulk-cache directory, or None when no session is open."""
    raw = os.environ.get(ENV_VAR)
    if not raw:
        return None
    path = Path(raw)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        # An unusable cache directory must never break a fetch — fall back to
        # downloading, which is merely slower.
        return None
    return path


ZIP_MAGIC = b"PK\x03\x04"


def _looks_like_zip(data: bytes) -> bool:
    return data[:4] == ZIP_MAGIC


def cached_download(key: str, fetch: Callable[[], bytes]) -> bytes:
    """Return the bytes for `key`, calling `fetch()` only on a cache miss.

    `key` must identify the archive's content (include the year and dataset),
    since a session cache is keyed by filename alone.

    Both callers fetch ZIP archives, so the payload is magic-byte checked on the
    way in and on the way out. That is not paranoia about disk corruption — it is
    about the upstream: an agency that answers a bulk-file request with an HTML
    error page and a 200 would otherwise poison the cache, and every remaining
    state in the run would reuse the garbage instead of retrying. A non-ZIP is
    passed through to the caller (which will fail loudly on its own terms) but
    never cached.
    """
    cache = session_dir()
    if cache is None:
        return fetch()

    path = cache / key
    # is_file(), not exists(): a directory sitting at this name would pass an
    # exists() check and then blow up on read. And a cache entry that cannot be
    # read must degrade to a download — never harden into a failed refresh.
    if path.is_file():
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"    [bulk cache] {key} unreadable ({exc}); re-downloading")
        else:
            if _looks_like_zip(data):
                size_mb = len(data) // (1024 * 1024)
                print(f"    [bulk cache] reusing {key} ({size_mb} MB, already "
                      f"downloaded this run)")
                return data
            print(f"    [bulk cache] {key} is not a ZIP ({len(data)} bytes); "
                  f"discarding and re-downloading")
            path.unlink(missing_ok=True)

    data = fetch()

    if not _looks_like_zip(data):
        print(f"    [bulk cache] refusing to cache {key}: response is not a ZIP "
              f"({len(data)} bytes) — passing it through unchanged")
        return data

    # Write to a .part file and rename. A crash or Ctrl-C mid-write would
    # otherwise leave a truncated archive that the next state in the loop would
    # read as if it were complete.
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError as exc:
        print(f"    [bulk cache] could not cache {key} ({exc}); continuing")
        tmp.unlink(missing_ok=True)

    return data
