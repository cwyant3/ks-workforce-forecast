# Data refresh log

Append-only record of what each scheduled refresh routine did. One entry per run,
newest at the bottom. Written by the `ks-refresh-*` routines described in
[data-source-release-calendar.md](data-source-release-calendar.md).

Entry format — keep it parseable:

```
## [YYYY-MM-DD] {source} | {refreshed | no-op | blocked | failed}
Vintage before: {what was in data/outputs}
Vintage after:  {what is there now, or "unchanged"}
Checked:        {how publication was verified}
Outputs changed: {git status --short summary, or "none"}
Validation:     {pass | fail + detail}
Notes:          {anything a human needs to act on}
```

Status meanings:

- **refreshed** — new data published, pulled, validated. Needs a human commit.
- **no-op** — checked, nothing new published yet. No changes made.
- **blocked** — a manual download is required before this source can advance.
- **failed** — the fetch, pipeline, or validation errored. Do not commit.

---

## [2026-08-20] all | routines established
Vintage before: n/a
Vintage after:  n/a
Checked:        release calendars reviewed for all 14 sources
Outputs changed: none
Validation:     n/a
Notes:          Per-source routines created and `refresh_dashboard.py` given
                `--sources` / `--states` targeting. See
                data-source-release-calendar.md. Flagged on creation: the KDOL
                labor force export was 91 days old (three monthly vintages
                behind) and the BLS 2025-35 national projections cycle was
                expected imminently.

## [2026-08-20] KDOL labor force | refreshed
Vintage before: monthly through 2026-03 (labforce__99999999.xls, HTML export, 2026-05-21)
Vintage after:  monthly through 2026-06 (labforce__99999999.xlsx, 2026-08-20)
Checked:        new KLIC export confirmed to carry the same 23-column KDOL schema;
                66,978 monthly rows, 1976-2026, Areatype 01/04 present
Outputs changed: kdol_labforce_county_s20.parquet, kdol_labforce_state_s20.parquet,
                kdol_labforce_county_recent_s20.parquet (24-month window
                2024-07 -> 2026-06, 105 counties)
Validation:     parser sanity checks pass (105 counties, 3-char county_fips,
                24 distinct months); full pipeline not yet re-run
Notes:          Export was saved as .xlsx while the pipeline hardcoded .xls, so it
                would have been ignored — the stale .xls would have kept parsing
                silently. Fixed three format assumptions in
                parse_manual_kdol_labforce.py: read_html cannot open a real xlsx,
                the header-shift is HTML-only, and Areatype arrives as int (4) not
                str ("04") — that last one would have produced EMPTY county and
                state outputs without erroring. Driver now picks the most recently
                downloaded labforce__*.xls* by mtime. Also fixed a misleading print
                that reported the recent window's newest month as "2026/12" by
                taking max(year) and max(month) independently. Legacy .xls path
                regression-tested and still works.

## [2026-08-24] KDOL labor force | blocked
Vintage before: monthly through 2026-06 (labforce__99999999.xlsx, downloaded 2026-08-20)
Vintage after:  unchanged — monthly through 2026-06
Checked:        KDOL published the July 2026 Kansas Labor Market Report, statewide
                *and sub-state*, on 2026-08-21 (3rd Friday, as the calendar predicts).
                Confirmed via Hays Post, 2026-08-21, reporting KDOL county-level July
                figures ("Unemployment in Ellis County was up .1% in July from 3.5% in
                June"); KS statewide July rate 3.8% SA, LFPR 66.7%. dol.ks.gov and
                bls.gov both return HTTP 403 to the fetch tool, so the agency's own
                release page could not be read directly — see Notes.
                On-disk state verified two ways: the raw export
                data/kdol_cache/labforce__99999999.xlsx maxes at Periodyear*100+Period
                = 202606 (72,432 rows, 23-column KDOL schema intact), and
                kdol_labforce_county_recent_s20.parquet holds a 24-month window
                2024-07 -> 2026-06 across 105 counties.
Outputs changed: none — parser not run
Validation:     n/a (no refresh attempted)
Notes:          ONE monthly vintage behind. Chris needs to re-export the LAUS labor
                force report from https://klic.dol.ks.gov/ into data/kdol_cache/ as
                labforce__*.xls or .xlsx, then re-run this routine. Either format is
                fine — do not rename the extension.

                Staleness check gave a FALSE GREEN: the dry-run reported "KDOL labor
                force: ok 4d" because the 40-day window measures the file's *download*
                mtime, not the newest month inside it. The 2026-08-20 download landed
                one day before the 2026-08-21 release, so a freshly-downloaded file is
                nonetheless a vintage behind. The mtime heuristic cannot catch this
                class of miss on its own; the parquet max-period check in STEP 1 is
                what caught it, and is the check that actually matters. Worth
                considering a vintage-aware staleness signal (newest month in the
                parquet vs. the month expected for today's date) rather than file age.

                Pre-existing uncommitted work in the tree is from the 2026-08-20 run,
                not this one: the three kdol_labforce_*_s20.parquet files plus
                parse_manual_kdol_labforce.py, refresh_dashboard.py, README.md,
                fetch_ipeds.py, fetch_qcew.py, and the two untracked docs/ files still
                await a human commit. Whoever re-exports should expect the July refresh
                to stack on top of that diff rather than produce a clean one.

                No benchmark-revision signal this cycle — the annual revision landed
                2026-05-22 and is already in the adopted history.

## [2026-08-27] CBP | blocked
Vintage before: reference years 2015–2022 (data/outputs/cbp_estab_trends_s20.parquet
                year_range maxes at "2015–2022", n_years 8; data/cbp_cache/cbp_s20.parquet
                holds years 2015…2022, 5,686 rows, cached 2026-08-02)
Vintage after:  unchanged — 2015–2022
Checked:        No new vintage published. census.gov CBP updates page states "2023 CBP
                and ZBP were released June 26, 2025" and lists no 2024 release; the
                newest reference year advertised is 2023. Corroborated against the API
                catalog: api.census.gov/data.json advertises CBP vintages 1986…2023 with
                no 2024 endpoint. So the calendar's expectation holds — 2024 CBP is
                still not out as of today, and the Jun/Jul/Aug fire window found nothing.
Outputs changed: none — cache NOT cleared, pipeline not run (see Notes)
Validation:     n/a (no refresh attempted)
Notes:          **We are one published vintage behind, and STEP 2 cannot fix it.** The
                2023 vintage published 14 months ago (2025-06-26) and we never picked it
                up, because `fetch_cbp.py:39` hardcodes the year list:

                    CBP_YEARS = list(range(2015, 2023))   # 2015–2022

                CBP is therefore the same *notify-only* class as ACS (#1) — a cache clear
                alone re-downloads the identical years — but it is NOT listed in the
                calendar's "Sources that need a code change, not just a cache clear"
                section. That omission is why this went unnoticed for a year: every
                automated check compared "newest published" against "what the fetcher was
                told to want" rather than against the vintage actually on disk.

                I deliberately did NOT run `refresh_dashboard.py --sources cbp --states
                bloc`. It would have cleared data/cbp_cache and re-pulled 2015–2022 for
                all five states — the exact waste the ACS carve-out exists to prevent —
                and advanced nothing. Cache left intact.

                REQUIRED HUMAN ACTION — one-line change, then a normal refresh:
                  1. fetch_cbp.py:39 → `CBP_YEARS = list(range(2015, 2024))  # 2015–2023`
                  2. `python refresh_dashboard.py --sources cbp --states bloc`
                  3. README §5 row 6: "County×NAICS, 2015–2022" → "2015–2023"

                Pre-verified so step 1 is safe: CBP 2023 uses **NAICS2017**, so
                `_NAICS_VAR` (year >= 2017 → NAICS2017) is already correct and needs no
                edit. All five bloc states return 2023 county data — NAICS 23 county row
                counts, 2022 → 2023: KS 99→99, CO 61→62, MO 114→114, NE 83→81, OK 77→77.
                Expect n_years to go 8 → 9 and year_range to become "2015–2023", which
                shifts every estab_slope / estab_pct_chg — i.e. a forecast-affecting
                change, which is why this routine reports it rather than making it.

                ENVIRONMENT — CORRECTED, same session. Earlier in this run outbound HTTPS
                from the Bash tool failed repeatedly with WinError 10054 (curl reported
                000, `requests` raised ConnectionResetError on api.census.gov and
                pypi.org), while PowerShell returned 200, and I wrote that Bash was
                broken and routines should use PowerShell. **That was wrong — the failure
                was transient and has cleared.** Re-tested from Bash: `requests`, stdlib
                `urllib`, `curl`, and a raw `ssl` handshake all return 200, both sandboxed
                and with the sandbox disabled. No proxy env vars are set, and the raw
                handshake shows a genuine DigiCert chain (TLSv1.2, ECDHE-RSA-AES256-GCM-
                SHA384) — no TLS interception, nothing to reconfigure. **Do not re-route
                routines to PowerShell.** Most likely a cold sandbox network path at
                session start; if it recurs, retry before concluding anything.

                WHAT THE BLIP ACTUALLY EXPOSED — worth fixing, unlike the above.
                `fetch_cbp.py` turns transient network failure into silently truncated
                data that then gets frozen in cache:
                  - `_fetch_year` issues one request per NAICS code (~19/year, by design —
                    Census 204s on long NAICS lists). It has no error handling, so a
                    single blip on any one of them raises.
                  - That raise is swallowed by the year-level `except Exception` at
                    fetch_cbp.py:175, which prints "Warning … — skipping" and **drops the
                    whole year**, then keeps going.
                  - There is no retry or backoff anywhere.
                  - The cache parquet is written from whatever survived, so a partial
                    fetch is cached and every later run prints `[cache] CBP 20` and
                    returns the truncated data until someone manually clears it.
                At 9 years x ~19 NAICS x 5 states the adopted refresh is ~855 requests —
                ample surface for one blip to quietly cost a year. Same failure class as
                the 2026-08-24 KDOL `Areatype`-as-int bug: wrong data, no error raised.
                Suggested fix: retry with backoff inside the NAICS loop, and refuse to
                write the cache unless every requested year is present.

                Working tree unchanged by this run. The 12 uncommitted entries in
                `git status --short` are all pre-existing from the 2026-08-20 and
                2026-08-24 runs and still await a human commit.

                Next CBP fire is 2027-06-27 unless the cron is widened. If 2024 CBP holds
                the ~18-month lag it should land around June 2026 — already past — so
                2024 may be running late; the Jun/Jul/Aug window is correct but the
                Items Requiring Verification row for "CBP 2024 vintage" should stay open.

## [2026-08-27] CBP | refreshed
Vintage before: 2015–2022 (see the `blocked` entry above from the same day)
Vintage after:  **2015–2023** — the 2023 vintage is now adopted
Checked:        Human unblocked it by authorising the code change. fetch_cbp.py:39
                `CBP_YEARS` 2015–2022 → 2015–2023 (plus the matching docstring default);
                `_NAICS_VAR[2023]` verified to resolve to NAICS2017, no edit needed.
                Then `python refresh_dashboard.py --sources cbp --states bloc`, exit 0.
Outputs changed: 111 modified parquets in data/outputs + 3 new untracked
                (pc_occ_by_sector_s29/s31/s40). Content-level comparison against HEAD:
                **92 byte-identical, 19 genuinely changed** — see Notes for the split.
Validation:     PASS — "Output validation passed." x5, one per state;
                "REFRESH COMPLETE — 5 state(s) regenerated and validated."
Notes:          CBP fetched live for all five states, 9 years each, no dropped years.
                Cache coverage confirmed 2015–2023 / n=9 / MISSING=none for s20, s08,
                s29, s31, s40. Row growth ties out exactly to the fetch log — e.g. KS
                5,686 → 6,356 (+670) against a logged "CBP 2023 (state 20) … 670
                county-NAICS rows"; CO 3,646 → 4,092 (+446) against a logged 446. That
                one-year-exactly match is the check that proves nothing else moved
                inside CBP. `cbp_estab_trends_s*` shifted on estab_slope,
                estab_pct_chg, estab_latest, year_range, n_years in all five states,
                with year_range now "2015–2023" for 411/420 KS rows (the remainder are
                sparse counties Census suppresses in later years — pre-existing pattern).
                The silent-year-drop risk flagged in the `blocked` entry did not fire:
                zero `Warning: CBP` lines.

                **`--sources cbp` did not isolate the diff, and 6 unrelated layers
                drifted.** `--sources` gates cache clearing, but the run still calls
                `run_forecast.py --all`, so everything regenerates. Of the 19 real
                content changes: 10 are CBP (intended), 3 are the pre-existing
                uncommitted KDOL labor-force work from 2026-08-20, and **6 are drift
                a human must review before committing**:
                  - jolts_vacancy_rates.parquet — 9 cells in vacancy_rate_trend_slope
                    (JOLTS re-fetched live, 31 series; upstream revision)
                  - ks_occ_in_demand_top_s20.parquet — 305 cells in work_experience,
                    on_the_job_training
                  - ks_occ_proj_state_s20.parquet — 1,067 cells in education,
                    work_experience, on_the_job_training
                  - pc_occ_proj_s29 / s31 / s40.parquet — **(0,14) → 705 / 782 / 655
                    rows**: Projections Central occupational data for MO/NE/OK was
                    EMPTY in HEAD and is now populated. An improvement, but a
                    substantive one that arrived as a side effect of a CBP refresh.
                Corrected the release calendar, which claimed the diff "isolates the
                source that actually moved" — it does not.

                DRIVER REPORTING BUG: the run printed `[skip] cbp_cache (not present)`
                for a directory that demonstrably existed and was in fact cleared
                (`data/cbp_cache/` went empty, mtime 09:24, then refilled with 5 live
                files). clear_caches() at refresh_dashboard.py:238 is a plain
                `(DATA_DIR / name).exists()`, so this looks like a transient
                OneDrive-path stat miss — the same flakiness class as the network
                resets earlier in the session. Outcome was correct, but a routine that
                reports "skip" while actually clearing will eventually mislead someone
                into a wrong diagnosis. Worth making the message reflect the action.

                NOT COMMITTED, per routine rules. A human must review the 6 drift items
                above — especially the three pc_occ_proj files — before `git add`.

                Two pre-existing UserWarnings fired again and are unrelated to CBP:
                KDOL *UI claims* (data/kdol_cache/kdol_ui_manual.csv, a different
                dataset from the labor-force export) and a legacy Kansas-projections
                path (data/bls_proj_cache/ks_proj_manual.xlsx) that the adopted
                KDOL 2024–2034 workbooks in data/kdol_proj/ have superseded. The
                latter is a stale check worth deleting so it stops crying wolf.

## [2026-08-27] CBP | hardened (code change, no data change)
Vintage before: 2015–2023
Vintage after:  unchanged — 2015–2023
Checked:        n/a — this closes the silent-truncation risk flagged in the two
                entries above. Authorised by Chris after the refresh landed.
Outputs changed: none. fetch_cbp.py only; all five caches verified byte-intact and
                still served offline (6356/4092/7413/4971/5072 rows, 2015–2023, n=9).
Validation:     8/8 behavioural checks pass; `python -m py_compile` clean.
Notes:          Three changes to fetch_cbp.py, all aimed at the same failure mode —
                a transient blip silently costing a year and then freezing it in cache.

                1. `_get_with_retry()` — exponential backoff (4 attempts, 1.5x) on
                   connection resets, timeouts, and 429/5xx. Deliberately does NOT
                   retry other 4xx, which are deterministic. Every per-NAICS request
                   in `_fetch_year` now goes through it.
                2. Partial-pull guard — `fetch_cbp` tracks which years actually
                   returned and raises `CBPPartialFetchError` if any requested year is
                   absent, instead of the old behaviour (print "Warning … skipping",
                   drop the year, cache the remainder). New `allow_partial=True`
                   escape hatch returns the short series but STILL refuses to cache.
                   The old year-level handler printed "Warning"; it now prints "ERROR"
                   so the log line matches the severity.
                3. Stale-cache detection — a cache lacking any requested year is no
                   longer served as if complete; it re-fetches. This makes the
                   original bug self-healing: bumping CBP_YEARS now invalidates the
                   old cache automatically instead of silently returning short data.
                   Note it re-fetches the whole state, not just the missing year,
                   since the cache is one file per state.

                Verified by simulation (not just inspection): retry recovers after 2
                resets; gives up and raises after exhausting attempts; does not retry
                a 404; the guard raises with the missing year named and writes no
                cache; allow_partial returns data and still writes no cache; a
                complete cache is served with zero network calls; a cache missing a
                year triggers re-fetch.

                Behavioural change worth knowing: CBP is an *optional* step
                (`run_cbp`) called bare at run_forecast.py:278, so the guard turns an
                incomplete CBP pull into a hard failure for that state's refresh
                rather than a quiet partial. That is the intent — a wrong forecast is
                worse than a failed run — but it means a genuinely retired upstream
                year will now block the refresh until someone passes explicit `years=`
                or `allow_partial=True`.
