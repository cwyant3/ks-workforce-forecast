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

## [2026-08-29] BLS national projections | blocked
Vintage before: 2024–2034 cycle (base_year=2024, proj_year=2034 in both
                bls_proj_sector_outlook.parquet — 5 sectors — and
                bls_proj_occupations.parquet — 1,113 occupations)
Vintage after:  unchanged
Checked:        WebSearch (bls.gov 403s WebFetch). The **2025–2035 cycle published
                2026-08-27** at 10:00 ET — confirmed by two independent searches
                landing on bls.gov/news.release/ecopro.nr0.htm ("Employment
                Projections: 2025-2035 Summary", 2025 A01 Results) and reported
                headline figures: total employment 170.3M -> 176.2M, +5.9M jobs,
                +3.5% over the decade vs +10.9% for 2015–25, growth led by private
                healthcare and social assistance. Date is consistent with cadence
                (2024–34 landed 2025-08-28; both are the last Thursday of August).
                Held workbook data/bls_proj_cache/bls_proj_national_manual.xlsx is
                dated 2026-05-21 — the 2024–34 cycle. Outputs (2026-08-27 10:08)
                are newer than the workbook, so the held cycle was already parsed.
Outputs changed: none. `git status --short` shows only the three pre-existing
                uncommitted files unrelated to this run (fetch_bls_proj.py,
                refresh_dashboard.py, run_forecast.py — the 2026-08-27 CBP/refresh
                work). No data/ path touched.
Validation:     n/a — no pipeline run.
Notes:          ACTION REQUIRED, and it is NOT just a download. Two blockers, both
                in fetch_bls_proj.py:

                1. Download the 2025–35 workbook to
                   data/bls_proj_cache/bls_proj_national_manual.xlsx from
                   https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm

                2. **This source belongs in the calendar's "need a code change, not
                   just a cache clear" list** alongside ACS and CBP. Dropping the new
                   workbook in place alone changes nothing, and would then parse to
                   nothing:

                   - `fetch_national_projections()` (fetch_bls_proj.py:247-248)
                     defaults `base_year=2024, proj_year=2034`, and run_forecast.py:385
                     calls it with no year arguments. The cache filename is
                     cycle-keyed — `bls_proj_national_{base}_{proj}.parquet` — so the
                     existing bls_proj_national_2024_2034.parquet is served on sight
                     and the manual workbook is never opened. Bumping the defaults to
                     2025/2035 changes the cache key, which is what forces the reread;
                     deleting the old parquet without bumping would just reparse the
                     old cycle under the old label.
                   - The column hints are **year-literal**: `_BASE_EMP_HINTS`
                     (:165) matches "employment, 2024"/"2022" and `_PROJ_EMP_HINTS`
                     (:169) "employment, 2034"/"2032". A 2025–35 workbook labels those
                     columns "Employment, 2025" / "Employment, 2035", so neither
                     matches. `_parse_proj_df` returns an empty frame when
                     base_emp_col is None (:208-209) and the caller only prints
                     "Warning: could not parse BLS projections file" (:308) — a
                     non-fatal warning. Add the 2025/2035 hints, or make the hints
                     year-agnostic.

                Failure mode if step 2 is skipped: no exception, just a warning in
                the log and an empty projections frame — the same silent-emptiness
                class of bug as the KDOL Areatype int/str issue on 2026-08-20.

                When the cycle does advance, check the display labels by hand: the
                dashboard labels this layer by cycle, and Executive Narrative mode
                suppresses unvalidated demand claims. Display layer only — no effect
                on the cohort population model.

                One-state note for the eventual refresh run: this layer writes only
                national files (no _s{fips} suffix) that every state reads, so
                `--states 20` regenerates them for all five deployed states.

## [2026-09-01] BLS national projections + SSA disability | refreshed
Vintage before: BLS national 2024–2034 (bls_proj_occupations.parquet, 1,113 occupations);
                SSA data year 2023 (oasdi_sc24.xlsx, downloaded 2026-05-21)
Vintage after:  BLS national **2025–2035** (1,112 occupations);
                SSA data year **2024** (oasdi_sc25.xlsx, downloaded 2026-09-01)
Checked:        Both workbooks were placed by hand by Chris on 2026-09-01, closing the
                two STALE-103d flags from that morning's supervisor sweep. Cycle read
                from the workbooks themselves, not assumed: Table 1.2 of the BLS book
                carries "Employment, 2025" / "Employment, 2035" columns, and the SSA
                book is named for its publication year (2025 edition = data year 2024).
Outputs changed: 17 files, and — unusually — all 17 are genuinely attributable.
                  - bls_proj_occupations.parquet, bls_proj_sector_outlook.parquet
                    (national, no _s{fips} suffix; every state reads them)
                  - ssa_disability_s{08,20,29,31,40}.parquet
                  - participation_s*, projections_effective_s* (5 each) — downstream
                    of the disability decrement
                No unrelated drift this run, unlike the 2026-08-27 CBP refresh which
                rewrote 111 parquets for 10 CBP files. The difference is that
                `--sources ssa,bls-proj` cleared no API cache at all, so every other
                layer was served from cache and rewrote identically.
Validation:     8/8 per state, all five states pass. Content-diffed against HEAD rather
                than trusting `git status`:
                  - BLS cycle label 2024–2034 -> 2025–2035 in both national files.
                    Sector growth: Healthcare 9.2%->10.1%, Hospitality 3.8%->4.6%,
                    Manufacturing 0.3%->1.2%, Skilled Trades 4.9%->5.2%, and
                    IT/Computer Services 7.2%->**6.3%** — the one sector BLS revised
                    DOWN this cycle. Worth knowing before anyone quotes the IT number.
                  - SSA year 2023 -> 2024 with county counts unchanged (105/64/115/93/77)
                    and SSDI totals down 0.7%–2.6% per state.
                  - projections_effective eff_p50 moved +11 on average and is positive
                    in 63.8% of county-years — the right direction for a shrinking
                    disability decrement. Max delta 314 people; no row-count changes.
Notes:          THREE code changes were required first. Dropping the files in place
                alone would have adopted neither, and would have failed silently in
                both cases — the same class of bug as the 2026-08-20 KDOL Areatype
                int/str issue.

                1. fetch_bls_proj.py — the year-literal column hints are GONE. The
                   old _BASE_EMP_HINTS/_PROJ_EMP_HINTS matched "employment, 2024" /
                   "employment, 2034" literally, so a 2025–35 workbook matched
                   neither, _parse_proj_df returned empty, and the caller printed a
                   warning instead of raising. Replaced with `_EMP_YEAR_RE` matching
                   "Employment, <4 digits>" anchored to the full column name, plus
                   `detect_cycle()`, which takes the earliest year as base and the
                   latest as projection target. The next cycle needs no edit here.
                   The anchor matters: without it the regex would also swallow
                   "Employment distribution, percent, 2025" and "Employment change,
                   numeric, 2025–35", which sit adjacent in the same table.
                2. fetch_bls_proj.py — defaults bumped 2024/2034 -> 2025/2035, which
                   is what changes the cache key and forces the reread, and
                   `_find_manual_workbook()` now accepts a vintage-named workbook
                   (bls_proj_national_2025_2035.xlsx) in preference to the legacy
                   vintage-less bls_proj_national_manual.xlsx. The vintage glob
                   requires a leading digit — a bare `bls_proj_national_*.xlsx` would
                   sort "manual" above "2025_2035" ("m" > "2") and pin the legacy file
                   forever.
                   Added a **cycle-mismatch guard**: if the workbook's detected years
                   disagree with the requested ones, it raises instead of writing a
                   mislabelled parquet. Verified by simulation — requesting 2024–34
                   against the 2025–35 workbook now raises ValueError; requesting the
                   defaults parses 1,112 occupations; a second call hits the parquet
                   cache and returns an identical frame.
                3. scripts/parse_manual_ssa.py — two hardcodings, both silent.
                   `candidates[0]` took whichever workbook the glob listed first, which
                   with oasdi_sc24 and oasdi_sc25 side by side is the OLD one; now
                   `newest_workbook()` sorts and takes the last, preferring the
                   oasdi_sc* naming so a legacy `oasdi_2024.xlsx` cannot sort ahead of
                   it ("2" < "s"). And `--pub-year` defaulted to 2024 while
                   refresh_dashboard.py never passes the flag, so the 2025 edition
                   would have been stamped year=2023 — the previous vintage's label on
                   the new numbers. It is now inferred from the filename, with the flag
                   kept as an override.

                Also fixed, smaller: the BLS UserWarning fired on EVERY refresh telling
                the operator to place a file that was already sitting there, because it
                was emitted before the manual-file lookup. It now fires only when no
                workbook is found. This is the third "warning that cries wolf" removed
                from this pipeline in two weeks; the pattern to watch is a warning
                emitted on the *expected* path rather than the failure path.

                Staleness reporting fixed too: refresh_dashboard.py pinned
                `ssa_cache/oasdi_sc24.xlsx` and `bls_proj_national_manual.xlsx` by exact
                filename, so once the new editions landed beside the old ones the report
                would have kept measuring the age of the SUPERSEDED file and read STALE
                forever. Both are now globs that take the newest match. All six manual
                sources currently report ok.

                NOT COMMITTED, per routine rules. The Streamlit dashboard still serves
                2024–34 BLS and 2023 SSA until someone reviews and pushes. Note the
                dashboard's cycle captions are data-driven (`vintage_label()` reads
                base_year/proj_year off the frame), so they will follow the new vintage
                with no display-layer edit.

                Kansas state projections are unaffected and remain on the KDOL 2024–2034
                cycle — a different source from the BLS national book. README §5 row 12
                now records both cycles separately so the two are not confused.

## [2026-09-02] JOLTS | refreshed
Vintage before: monthly through 2023-12; annual vacancy rates 2015–2023
Vintage after:  monthly through 2026-07; annual vacancy rates 2015–2025
Checked:        WebSearch (bls.gov returns 403 to WebFetch) — JOLTS news release
                "Job Openings and Labor Turnover — July 2026" (2026 M07 results),
                published 2026-09-01, job openings 7.3M / openings rate 4.4%.
                Matches the calendar's expected 2026-09-01 date for Jul 2026.
Outputs changed: data/outputs/jolts.parquet (3,348 -> 4,309 rows),
                data/outputs/jolts_vacancy_rates.parquet (54 -> 66 rows),
                fetch_jolts.py (code change, see Notes).
                No per-state parquets moved — unusually clean for a run that
                invokes run_forecast.py --all. git status --short showed exactly
                those three paths.
Validation:     pass ("Output validation passed"). Content diff confirms no
                historical revision: all 2015–2023 vacancy_rate_pct values are
                bit-identical across the overlap (0 of 54 rows changed), so the
                new years are additive rather than a restatement.
Notes:          THIS WAS NOT A MISSED REFRESH — it was the fourth instance of the
                hardcoded-vintage pattern, and the routine as written could never
                have advanced it. `fetch_jolts.py` had `JOLTS_YEARS =
                list(range(2015, 2024))` and `run_forecast.py` passes no `years`,
                so `--sources jolts` re-downloaded the same nine years every month.
                A monthly source sat 31 months behind while every fire reported
                success. Per the calendar's own lesson (§"Sources that need a code
                change"): compare the newest PUBLISHED vintage against what is in
                data/outputs/, never against what the fetcher is configured to want.

                Two fixes, both permanent — no year bump will be needed next cycle:

                1. `JOLTS_YEARS` replaced with `default_jolts_years()`, computing
                   2015 -> current calendar year. Requesting a partly-published
                   year is harmless (the API returns the months that exist).
                2. Cache self-invalidation. The cache filename `jolts_{seasonal}
                   .parquet` carries NO vintage, and the old code returned it on
                   sight if it existed. That is what made the staleness silent and
                   survivable. It now compares the held years against the requested
                   ones and re-fetches when short — the same guard CBP received
                   2026-08-27. It earned itself immediately on this run: the cache
                   clear FAILED (`[WARN] jolts_cache — could not clear: [WinError 5]
                   Access is denied`, a OneDrive lock), so the refresh advanced only
                   because the guard invalidated the stale parquet. Without it this
                   run would have reported success and changed nothing.

                Third change, methodological — incomplete years are now excluded
                from the annual averages. JOLTS 2026 has 7 of 12 months, and
                `compute_annual_averages` took a plain mean over whatever was
                present. Unguarded, a Jan–Jul mean would have been published as
                "2026", and the dashboard takes `year.max()` as its HEADLINE
                vacancy rate and feeds each year to the trend-slope regression as
                an equally-weighted point. `complete_years()` now gates it (12
                months required) and prints what it dropped. This changes nothing
                for 2015–2025, which are all complete. The monthly jolts.parquet
                still carries all of 2026 — nothing is discarded, it just does not
                masquerade as an annual figure.

                FOR THE REVIEWER — the forecast-relevant part. Trend slopes roughly
                halved in every sector, because the old series ended at the tail of
                the 2021–22 vacancy spike and the model was extrapolating a rising
                trend from it. 2024–25 unwind most of that:

                  Healthcare                  0.457 -> 0.247
                  Hospitality & Entertainment  0.525 -> 0.233
                  IT/Computer Services         0.425 -> 0.236
                  Manufacturing                0.406 -> 0.174
                  Skilled Trades               0.312 -> 0.129

                Headline vacancy rates fall correspondingly (Healthcare 7.5% in
                2023 -> 5.7% in 2025; Hospitality 7.1% -> 5.6%). The dashboard's
                demand-pressure signal will read materially cooler after this is
                pushed. That is the data, not a modelling change — but it is a
                visible shift in the public forecast and worth a look before it
                ships.

                NOT COMMITTED, per routine rules.

                Follow-ups for a human, not done here:
                - The calendar's §"Sources that need a code change" list should
                  gain JOLTS as instance #4 (now self-healing, so it belongs as a
                  resolved note rather than a standing warning). Not edited: that
                  file is the routines' shared authority and a data-refresh run
                  should not rewrite it unreviewed.
                - `scripts/validate_outputs.py` checks nothing about JOLTS at all.
                  A vintage-recency assertion there (newest month within ~90 days
                  of run date) would have caught this in month two instead of
                  month 31. This is the class of defect no amount of scheduling
                  fixes.
                - The failed cache clear is a real operational hazard beyond JOLTS:
                  `--sources X` silently degrades to "serve from cache" for any
                  source whose cache is locked by OneDrive and whose fetcher lacks
                  a freshness guard. Worth auditing which other fetchers return an
                  unvalidated cache on sight.

## [2026-09-02] JOLTS follow-up | tooling
Vintage before: n/a — no data refresh in this entry
Vintage after:  unchanged (data outputs untouched since the entry above)
Checked:        n/a
Outputs changed: docs/data-source-release-calendar.md,
                scripts/validate_outputs.py,
                scripts/audit_cache_freshness.py (new)
Validation:     pass — validate_outputs.py --state 20 passes on current outputs;
                JOLTS guards individually exercised against synthetic bad data
                (see Notes)
Notes:          Closes the three follow-ups left open by the JOLTS entry above.

                1. RELEASE CALENDAR. JOLTS recorded as instance #4 of the
                   hardcoded-vintage class, marked resolved. Two additions beyond
                   the bare record: the note that JOLTS is the only MONTHLY source
                   in that list (which is why 31 months elapsed rather than one
                   annual cycle), and the generalised operational lesson — that
                   `--sources X` silently degrades to "serve from cache" for any
                   source whose cache directory is OneDrive-locked and whose
                   fetcher lacks a freshness guard. A cache clear is best-effort
                   on this machine and cannot be the only thing between a refresh
                   and stale output. §1 now warns that its own "check before
                   working" step is weaker than it appears, since it compares
                   outputs against what the FETCHER wants.

                2. VALIDATOR. `_failures_for_jolts()` added to
                   scripts/validate_outputs.py, wired into validate() as a
                   national check that runs regardless of --state (every state
                   reads the same two files). Asserts: newest reference month
                   within JOLTS_MAX_STALE_DAYS (120); annual file holds only
                   12-month-complete years; no complete year present monthly is
                   absent from the annual averages; all five dashboard sectors
                   present; rates non-null and within 0-25%. Absence of both files
                   is NOT a failure — run_forecast.py writes them only under
                   --jolts and the dashboard degrades to "not loaded".

                   Each guard was exercised against synthetic bad data rather than
                   assumed: the historical bug (frozen at 2023-12) now fails with
                   "976d old (limit 120d)"; a partial 2026 leaked into the annual
                   file fails; a dropped complete year fails; a missing sector, a
                   x100 unit error, an empty frame, and one file present without
                   the other all fail; both-absent correctly passes.

                   One real quirk found while testing, worth knowing before
                   anyone extends this: the annual frame carries a "Total" row
                   whose vacancy_rate_pct is legitimately NULL. _build_series_ids
                   requests only the total-nonfarm openings LEVEL (…JOL) as a
                   scale reference and no matching rate series. The first version
                   of the check failed on it. Null-rate and trend-slope checks are
                   therefore scoped to the five mapped supersectors, and the
                   reason is commented in place so it is not "fixed" back.

                3. CACHE AUDIT — scripts/audit_cache_freshness.py (new).
                   Classifies every .exists()-guarded cache read in every
                   fetch_*.py as VINTAGE-KEYED (filename carries a year, so a new
                   vintage misses the cache — safe by construction), VALIDATED
                   (fixed name but contents checked against requested years), or
                   UNVALIDATED (fixed name returned on sight — the defect). Then
                   cross-checks requested years against years in data/outputs/.
                   --strict exits 1 on any UNVALIDATED read, for CI.

                   THE AUDIT'S FINDING IS THAT JOLTS WAS NOT ISOLATED. 9
                   UNVALIDATED cache reads across 8 modules. Five sources have
                   BOTH halves of the bug in place today — an unvalidated cache
                   AND a year list ending well before the present:

                     laus    year list ends 2023 (3y behind) — fetch_laus.py:156
                     oes     year list ends 2023 (3y behind) — fetch_oes.py:233,336
                     lodes   year list ends 2021 (5y behind) — fetch_lodes.py:144
                     ipeds   year list ends 2023 (3y behind) — fetch_ipeds.py:304
                     ksde    year list ends 2023 (3y behind) — fetch_ksde.py:262

                   Two of those are confirmed unadopted from this calendar's own
                   records, with no further research needed: OEWS May 2025
                   estimates released 2026-05-15, and LODES 8.3 (2022 data)
                   released 2024-11-19. LAUS is the highest priority of the five
                   because its routine fires MONTHLY, so it looks the most
                   actively maintained while being three years behind.

                   Note the counter-intuitive part, called out in the tool's own
                   output because the intuitive reading is backwards: these five
                   show NO shortfall between requested and output years. Both
                   numbers agree precisely BECAUSE both are frozen. A matching
                   pair is not evidence of currency, which is why the contract
                   column exists alongside the year columns.

                   LODES is the instructive case: its per-year sub-caches ARE
                   correctly vintage-keyed, and a combined-cache shortcut checked
                   before the loop bypasses them entirely. Getting the sub-caches
                   right does not help if a fixed-name aggregate short-circuits
                   ahead of them.

                   Registry modelling note: SSA is marked manual: True. Its live
                   path is scripts/parse_manual_ssa.py reading a hand-placed
                   workbook, so SSA_YEARS is vestigial for the API path and
                   comparing it against the single-data-year output produced a
                   false STALE flag in the first run. Contract is still audited;
                   year math is skipped.

                NOT COMMITTED, per routine rules. No data outputs were touched by
                this entry — the diff is one doc, one validator, one new script.

                DELIBERATELY NOT DONE: the five flagged sources were not fixed.
                Each needs its vintage confirmed against its agency first (this
                routine's remit is JOLTS, and three of the five have no confirmed
                published-vintage evidence yet), and each is a separate forecast
                diff a human should review on its own. They are now recorded as
                rows in the calendar's "Items Requiring Verification" table rather
                than left as a paragraph in a log entry. Extending
                validate_outputs.py's recency assertion to those layers is the
                cheapest next step — the machinery is in _failures_for_jolts() and
                per-source thresholds are the only new input needed.

## [2026-09-03] QCEW | refreshed
Vintage before: annual averages 2015-2024; sector base_year 2024 in all five
                sector_projections_s{08,20,29,31,40}.parquet; qcew_cache held
                s{fips}_2015..2024.parquet (50 files)
Vintage after:  annual averages 2015-2025; sector base_year **2025** in all five
                states; qcew_cache holds s{fips}_2015..2025.parquet (55 files)
Checked:        Two things, and only the second one mattered.
                (1) Quarterly release, per the routine's remit: Q1 2026 published
                **2026-08-28** 10:00 ET, confirmed via WebSearch against the BLS
                release archive (bls.gov/news.release/archives/cewqtr_08282026.pdf,
                USDL-26-1424) and the BLS August 2026 schedule list. Matches the
                date this calendar already predicted.
                (2) **The release that actually moved this layer is the ANNUAL
                file, not the quarterly one.** This module consumes
                {year}_annual_by_area.zip, so a new quarter does not advance it.
                Direct probe of data.bls.gov with the pipeline's own UA:
                2024 -> 200 (136,138,122 B, ZIP magic PK/x03/x04),
                2025 -> **200 (114,997,862 B, valid ZIP)**,
                2026 -> 404 (193 B text/html).
                So 2025 annual averages were published (they land with the Q4 2025
                release, ~June 2026) and had been sitting unadopted.
Outputs changed: 57 genuine content changes vs HEAD out of 57 modified data files
                — 0 byte-identical rewrites, which is the reverse of the
                2026-08-27 CBP run and is explained below.
                **The tree was already dirty when this run started**: 43 data
                files carried the uncommitted 2026-09-02 LAUS/LODES/IPEDS/KSDE/
                JOLTS adoptions. So "57 changes vs HEAD" conflates two sessions
                and is NOT this run's footprint.
                This run's own footprint is the 14 paths newly appearing in
                git status between session start and now, and they are exactly
                the QCEW-driven sector layer and nothing else:
                  sector_projections_s{08,29,31,40}         (4)
                  state_sector_projection_s{08,20,29,31,40} (5)
                  state_total_projection_s{08,20,29,31,40}  (5)
                sector_projections_s20 was already dirty, but it did change here —
                its base_year read 2024 at session start and reads 2025 now.
                Note mtime cannot separate the two sessions: --sources qcew still
                invokes run_forecast.py --all, so ~140 outputs carry today's
                timestamp regardless of whether their content moved.
Validation:     pass. scripts/validate_outputs.py passed inline for all five
                states during the run, and was re-run standalone for KS
                afterwards. scripts/audit_cache_freshness.py now reports qcew as
                VINTAGE-KEYED, requested 2015-2025.
Notes:          **SECTOR BASELINE YEAR ADVANCED 2024 -> 2025 IN ALL FIVE STATES.**
                Flagged per routine because the dashboard's baseline labels are
                dynamic — a human should eyeball them. The label plumbing is
                already correct and needs no edit: dashboard/app.py:1023-1029
                reads sec_base_year from the sector outputs' own base_year column
                (falling back to the ACS base_year), deliberately separate from
                the ACS cohort baseline, and sector_model.py:321 derives base_year
                from the QCEW data's max year rather than a literal. Note the ACS
                cohort baseline did NOT move; the two baselines legitimately
                differ now and the UI shows both.

                **FIFTH INSTANCE OF THE FROZEN-YEAR-LIST DEFECT — fixed
                permanently, no bump needed next cycle.** fetch_qcew.py:34 held
                QCEW_YEARS = list(range(2015, 2025)) and run_forecast.py:159
                passes no years, so the request set could never advance past 2024
                no matter what BLS published. Same shape as ACS, CBP, the BLS
                projections cycle, and JOLTS. It is now:
                  - default_qcew_years() computes QCEW_START_YEAR -> last calendar
                    year. Deliberately optimistic: a year's annual averages land
                    ~5 months after its Q4 closes, so asking for last calendar
                    year is right most of the year and merely early in Q1.
                  - _in_publication_window() plus a requests.HTTPError catch at
                    the download call site make that early ask safe: a 404 on one
                    of the two most recent calendar years prints "not published
                    yet" and skips the year; a 404 on anything OLDER re-raises.
                    That asymmetry is deliberate — OES lost its 2024+ files to
                    exactly that kind of silent per-file 403/404, and quietly
                    shrinking the history the trend regression fits on would be
                    worse than failing loudly.
                  - QCEW_YEARS is retained as a module attribute because
                    scripts/audit_cache_freshness.py:98 reads it by name.
                Verified by construction: as of 2027-01-15 the list ends 2026, as
                of 2030-07-01 it ends 2029.

                **The cache clear FAILED and the refresh advanced anyway** —
                the same OneDrive lock JOLTS hit on 2026-09-02:
                  [WARN] qcew_cache - could not clear: [WinError 5] Access is
                  denied: ...\data\qcew_cache
                It removed the files but could not remove the directory itself
                (observed live: the file count fell from 50 to 10, then climbed
                back to 55), so all 11 years did re-fetch and QCEW's routine
                historical revisions were picked up. The refresh was safe
                regardless because this cache is VINTAGE-KEYED —
                s{fips}_{year}.parquet means 2025 is a cache miss by
                construction. Restating the standing lesson: a cache clear is
                best-effort on this machine and must never be the only thing
                standing between a refresh and stale output.

                **Suppression rose 5-9 points in every state for 2025** — worth a
                human look, though it is not a defect and not a blocker. Area and
                sector coverage are identical year over year (KS 107 areas /
                5 sectors in both) and row counts are within 4, but the suppressed
                share moved KS 39.3->44.8, CO 23.6->31.2, MO 27.8->36.3,
                NE 37.3->41.5, OK 25.8->34.9. That uniform jump is what accounts
                for the 2025 archive being 21 MB smaller than 2024's. It is
                concentrated in small rural counties: Kansas STATEWIDE (area
                20000) is fully disclosed for 2025 with entirely plausible
                year-over-year moves — Healthcare 196,320 -> 200,436 (+2.1%),
                Manufacturing 174,495 -> 173,891 (-0.3%), Hospitality ~flat,
                IT ~flat, Skilled Trades +2-3%, and wages +3-5% across all five.
                The model absorbs the extra suppression without gaps: KS
                sector_projections has 0 null emp_proj across 5,250 rows and all
                105 counties retain a projection (method mix option_a 3,960 /
                option_b 1,290). Expect newest-vintage suppression to relax when
                BLS revises.

                NOT COMMITTED, per routine rules. The diff a human is reviewing
                spans two sessions: this QCEW adoption plus the uncommitted
                2026-09-02 work. Worth committing them separately.

## [2026-09-03] LAUS + LODES + IPEDS + KSDE | refreshed (BACKFILL for 2026-09-02)
Vintage before: LAUS  annual averages through 2023 (LAUS_YEARS  = range(2015, 2024))
                LODES through 2021                (LODES_YEARS = range(2015, 2022))
                IPEDS collection year 2023        (IPEDS_YEARS = range(2015, 2024))
                KSDE  collection year 2023        (KSDE_YEARS  = range(2010, 2024))
Vintage after:  LAUS  2015-2025  (laus_s20.parquet,  1,199 rows)
                LODES 2015-2023  (lodes_s20.parquet, 64,486 rows)
                IPEDS 2015-2024  (ipeds_s20.parquet, 16,502 rows)
                KSDE  2010-2024  (ksde.parquet,      6,300 rows)
                OES   unchanged on 2023 — still BLOCKED
Checked:        **Read this entry's provenance before trusting its detail.** The
                work was done on 2026-09-02; this entry was reconstructed on
                2026-09-03 from the diff of commit 9d29cdc, not written by the
                session that did it. The per-source publication evidence is
                therefore second-hand — it is recorded in the resolved rows of
                the calendar's "Items Requiring Verification" table, which that
                session did write and which carries the actual probe results
                (LAUS via LAUCN201730000000003 over 2022-2026; LODES via
                ks_od_main_JT00_{year}.csv.gz returning 200 for 2021/2022/2023
                and 404 for 2024; IPEDS via C2024_A.zip + HD2024.zip at 200 and
                C2025_A/HD2025 at 404; KSDE via ccd/enrollment counts for
                2022/2023/2024 with 2025 at HTTP 500). What IS first-hand here
                is the code and data diff, and every figure above was read back
                out of the committed outputs on 2026-09-03.
Outputs changed: 42 data files, committed as part of 9d29cdc:
                  laus_s{08,20,29,31,40}, lodes_s{...}, ipeds_s{...},
                  ipeds_by_sector_s{...}, participation_s{...},
                  commute_metrics_s{...}, commute_snapshot_s{...} (5 each),
                  ksde, jolts, jolts_vacancy_rates, county_summary_s20.csv,
                  projections_s20, projections_effective_s20, state_projection_s20
Validation:     pass — but re-established rather than observed. The original run's
                validation result was not recorded. `scripts/validate_outputs.py`
                passed for all five states during the 2026-09-03 QCEW refresh,
                which regenerated every one of these outputs from the same caches,
                and `scripts/audit_cache_freshness.py` reports all four sources
                VALIDATED with requested years matching output years.
Notes:          **WHY THIS ENTRY EXISTS: the log above it says this work was not
                done.** The preceding entry, "[2026-09-02] JOLTS follow-up |
                tooling", closes with "DELIBERATELY NOT DONE: the five flagged
                sources were not fixed", and gives sound reasons — each needed its
                vintage confirmed against its agency first, and each is a separate
                forecast diff worth reviewing on its own. That was accurate when
                written and was then superseded **later the same session**: four of
                the five were confirmed, fixed, and adopted, and no entry was added
                saying so. The result is that the log's own record contradicted the
                working tree for a day, and a reader would have concluded these
                four layers were still years stale. Found 2026-09-03 while
                splitting the uncommitted tree into per-session commits.

                The lesson is narrow and worth stating: **an entry that says
                "deliberately not done" ages badly, because the natural next step
                is to go do it.** When a session reverses one of its own decisions,
                the reversal needs its own entry — editing the earlier one would
                violate append-only, and leaving it alone makes the log actively
                misleading rather than merely incomplete.

                WHAT THE DIFF SHOWS WAS DONE (9d29cdc, 927 insertions across 9
                Python files):

                1. Four year lists advanced. LODES moved **two** vintages
                   (2021 -> 2023), not one, which matches the calendar's finding
                   that the 8.4 tech-doc hint was right and two vintages were
                   unadopted. LAUS also moved two (2023 -> 2025).

                2. New `cache_freshness.py` (129 lines) factoring out the guard CBP
                   grew 2026-08-27 and JOLTS 2026-09-02, exporting
                   `load_fresh_cache` / `save_if_complete`. All five fetchers had
                   the pattern
                       if cache_file.exists(): return pd.read_parquet(cache_file)
                   replaced with a call that compares the cache's held years
                   against the requested years and re-fetches when short. That
                   pattern is the half that made JOLTS's 31-month staleness silent,
                   and it was present in fetch_{laus,lodes,ipeds,ksde,oes} too.

                3. `save_if_complete` also refuses to cache a **partial** pull, so
                   a run that half-fails cannot poison the cache with a short
                   parquet that later reads as complete. Called out in comments in
                   fetch_laus.py and fetch_oes.py.

                4. KSDE got a deliberate exemption: its cache may hold a file an
                   operator placed by hand, so it passes `None if used_manual else
                   years` and skips the completeness check in that case rather than
                   judging a manual file against KSDE_YEARS.

                5. **OES was fixed structurally but NOT advanced, on purpose.** It
                   received the freshness guard and a BLOCKER note above
                   `OES_YEARS`, but the list stays at 2023 so the config does not
                   claim a vintage the data lacks. May 2024/2025 are published and
                   403 at this module's `oesm{yy}st.zip`; adopting 2024 needs the
                   URL moved to `oesm{yy}all.zip` plus an `AREA_TYPE == 2` filter
                   in `_parse_state_oes`, because the existing AREA-prefix mask
                   would otherwise admit CBSA codes like 20020 as Kansas. This
                   remains the one unresolved source.

                6. New `scripts/audit_cache_freshness.py` (433 lines) and +165 lines
                   to `scripts/validate_outputs.py`. The validator additions are
                   JOLTS-only (`JOLTS_MAX_STALE_DAYS = 120`, `_failures_for_jolts`,
                   sector coverage, rate bounds), plus the hook that runs national
                   layers once regardless of `--state`. Extending that recency
                   assertion to LAUS/LODES/IPEDS/KSDE is still the cheapest
                   available next check and is still not done.

                COMMITTED AND PUSHED 2026-09-03 as 9d29cdc (this work) and f3bb2d2
                (the QCEW adoption), d65d6c1..f3bb2d2 -> origin/master. This
                departs from the standing "routines never commit or push" rule at
                explicit human instruction, not routine behaviour; the forecast
                diff was reviewed first. Streamlit Cloud will rebuild from f3bb2d2.

## [2026-09-03] OES/OEWS | refreshed
Vintage before: state layer 2015-2023 (OES_YEARS capped, BLOCKER comment);
                sector layer 2021-2023, and inflated 3-4x by a granularity
                double-count (see below)
Vintage after:  state layer **2015-2025** in all five states (s20 6,825 rows,
                s08 7,437, s29 7,454, s31 6,645, s40 7,133);
                sector layer **2021-2025** (oes_by_sector.parquet, 18,900 rows:
                3,704 / 3,784 / 3,793 / 3,817 / 3,802 by year)
Checked:        Re-probed every URL pattern with the pipeline's own UA. Results
                identical to the 2026-09-02 sweep, so the 403s are stable and
                were worked around rather than fixed:
                  2021 all 200 (78,047,282 B)   2021 in4 200 (31,451,833 B)
                  2022 all 200 (78,529,122 B)   2022 in4 200 (31,356,007 B)
                  2023 all 200 (80,129,309 B)   2023 in4 200 (31,801,148 B)
                  2023 st  200 (7,445,440 B)
                  2024 all 200 (79,846,301 B)   2024 in4 403, st 403, nat 403
                  2025 all 403                  2025 in4 403, st 403, nat 403
                So 2025 has NO reachable URL under any probed pattern. It is
                served by data/oes_manual/all_data_M_2025.xlsx (80,657,033 B),
                which already existed in the vault's raw/ layer from a hand
                download dated 2026-05-28; copied into the project so the
                pipeline is self-contained. All three tiers were exercised live
                for BOTH layers in this run.
Outputs changed: exactly SIX files, all OES — oes_by_sector.parquet
                (270,169 -> 18,900 rows, the granularity fix removing nested
                rows) and oes_state_s{20,08,29,31,40}.parquet (each +2 years:
                5,461->6,825, 5,945->7,437, 5,953->7,454, 5,301->6,645,
                5,706->7,133). Verified by content diff against HEAD, all six
                genuinely CHANGED, nothing else in data/outputs differs.
                Notably the downstream ks_occ_by_sector_s20 and
                pc_occ_by_sector_* layers did NOT change, so nothing consuming
                oes_by_sector propagated the correction — worth knowing, since
                it means those layers do not read the inflated employment
                figures. --sources oes still invokes run_forecast.py --all and
                rewrote ~140 outputs, so this tight diff is a content result,
                not a git-status one.
Validation:     pass — inline for all five states during the run, and re-run
                standalone for KS afterwards. audit_cache_freshness now reports
                oes VALIDATED, requested 2015-2025, output 2015-2025 (it had
                correctly flagged "output SHORT of requested: 2024,2025" in
                between the code change and the refresh). Independent checks:
                0 duplicate (year, naics, occ_code) keys in oes_by_sector, and
                exactly the 9 expected sector NAICS codes
                (22, 23, 31-33, 51, 54, 62, 71, 72, 81).
Notes:          **SIXTH INSTANCE OF THE YEAR-LIST DEFECT — but the honest one.**
                OES_YEARS deliberately stopped at 2023 with a BLOCKER comment
                explaining why, so unlike ACS/CBP/BLS-proj/JOLTS/QCEW the config
                never claimed a vintage the data lacked. The fix was not a bump
                but making acquisition fall through tiers instead of trusting a
                single URL.

                **STATE LAYER — three-tier acquisition.**
                st ZIP -> all-areas ZIP -> hand-placed workbook. Observed in
                this run: 2015-2023 state-only, 2024 all-areas, 2025 manual.

                Four things the 2026-09-02 plan did not anticipate:

                1. *The AREA convention was documented wrong.* The old comment
                   claimed a 7-char "2000000" form and matched a 2-char PREFIX
                   of the zero-stripped value. AREA for a state row is actually
                   a bare 2-char FIPS in every tier, and all three tiers share
                   an IDENTICAL 32-column schema. Replaced with exact FIPS
                   equality. The AREA_TYPE==2 filter mattered exactly as
                   predicted: 2,922 MSA rows in the 2024 file have CBSA codes
                   starting "20" (Dothan AL 20020, Duluth MN-WI 20260, Dubuque
                   IA 20220...) and the old mask would have counted them as
                   Kansas.
                2. *_read_oes_excel_from_zip never matched anything.* It
                   compared candidates against the full member path, but every
                   archive nests its payload ("oesm24all/all_data_M_2024.xlsx",
                   "oesm23st/state_M2023_dl.xlsx"), so EVERY year silently fell
                   through to the largest-xlsx fallback. It picked correctly by
                   luck. Now matches on basename — which is what made the
                   sector fix below possible at all.
                3. *The manual workbook cannot live in data/oes_cache/.* That
                   directory is in ANNUAL_API_CACHES and clear_caches() removes
                   it with shutil.rmtree, so the first `--sources oes` refresh
                   would have deleted the only copy of 2025. It lives in
                   data/oes_manual/ (gitignored, 80 MB) and is registered in
                   refresh_dashboard.MANUAL_SOURCES with a 400-day window
                   rather than the flat 100-day default, which would false-alarm
                   on an annual source within months of a correct download.
                4. *run_forecast.py is a separate SUBPROCESS per state.* An
                   in-process memo therefore buys nothing across a five-state
                   refresh — the 80 MB workbook would be parsed 5x for 2024 and
                   5x for 2025. The parsed AREA_TYPE slices are now cached to
                   disk (vintage-keyed, ~1.6 MB each), so bulk_cache shares the
                   download and these share the parse.

                **SECTOR LAYER — the granularity double-count, fixed.** This was
                the substantive find and it was worse than recorded. The
                diagnosis "in4 mixes granularity in one file" was wrong:
                oesm{yy}in4.zip ships EIGHT workbooks already split by level
                (natsector, nat3d, nat4d, nat5d_6d, three *_owner_* variants,
                file_descriptions) and the old reader concatenated ALL of them.
                The nesting came from stitching together files BLS had
                deliberately separated. The natsector member is now named
                explicitly — and note that without fix #2 above, the
                largest-xlsx fallback would have picked nat4d: 4-digit rows with
                no sector totals at all.

                THREE nesting axes had to be collapsed, not one:
                  - *Industry* — I_GROUP=='sector'. Lands exactly one code set
                    per dashboard sector. Manufacturing arrives as the combined
                    "31-33", matching QCEW's convention, so the two layers agree.
                  - *Occupation* — O_GROUP=='detailed'. **51.9% of rows were
                    minor/broad/major group rows**, so the "top occupations"
                    tables were ranking aggregates against their own children:
                    Registered Nurses appeared twice, as broad 29-1140 and
                    detailed 29-1141, with identical employment. The old regex
                    (^\d{2}-\d{4}$ plus the 24 major codes in _GROUP_CODES)
                    removes only MAJOR groups. That filter is harmless in the
                    state file — KS 2024 gives 687 rows either way, because OEWS
                    state output publishes no minor/broad rows — and badly wrong
                    in the industry file. Same buggy filter, opposite
                    consequences, purely from file contents.
                  - *Ownership* — needs no filter, but is now ASSERTED. OWN_CODE
                    values are overlapping aggregates rather than a partition
                    (57 Private+Postal contains 5 Private; 123 Fed+State+Local
                    contains 235 State+Local), so summing them would be a third
                    double-count. It does not arise because BLS publishes
                    exactly one aggregate per sector (62->58, 71/72->57, the
                    rest->5), verified on 2023. _parse_industry_oes now raises
                    if any (naics, occ_code) key repeats, so if the all-areas
                    fallback ever differs a human decides rather than the sum
                    silently inflating. **The guard held on the 2024 and 2025
                    all-areas runs**, which is what proves the two tiers are
                    equivalent.

                Measured effect on 2023: electricians (47-2111) in Skilled
                Trades went from 2,245,100 to 569,740; inflation ranged 3.0-4.4x
                across sectors. Corrected Healthcare top 5 is now Home Health
                and Personal Care Aides 3,575,980 / RNs 2,675,320 / Nursing
                Assistants 1,231,380 / Medical Assistants 715,860 / Medical
                Secretaries 675,060 — real occupations at levels that track
                BLS's published national figures. Before, the top entry was the
                aggregate group "Healthcare Diagnosing or Treating
                Practitioners" at 20,631,030. Coverage cost is negligible: 0-7
                occupations dropped per sector out of ~1,000, being ones that
                exist in a sub-industry but are suppressed at sector level.

                Row counts are smooth across BOTH source switches — 3,704 /
                3,784 / 3,793 (in4 natsector) / 3,817 (all-areas) / 3,802
                (manual) — which is the continuity check that the tiers are
                interchangeable.

                **Retired:** _read_oes_industry_excel_from_zip (the
                concatenate-everything reader, now dead) and the ~900 MB of in4
                downloads that a full sector refresh used to need; in4 is still
                the preferred tier for 2021-2023 because it is smaller (~31 MB
                vs ~79 MB), but one 80 MB all-areas file now serves both layers
                for 2024-2025 and the opportunistic cross-slice cache means it
                is read once, not twice.

                **A WEIGHTED-vs-UNWEIGHTED TRAP worth knowing.** The KS "median
                of the 687 occupation medians" jumped +9.7% from 2024 to 2025,
                which looks alarming and is not the published statistic. The
                official all-occupations median (the 00-0000 row this parser
                filters out) moved 46,850 -> 48,010, **+2.5%**, with employment
                1,431,180 -> 1,437,520 (+0.4%). The unweighted median is a
                composition artifact. Do not quote it as wage growth.

                STILL OPEN, deliberately: extending validate_outputs.py's
                recency assertion beyond JOLTS. Nothing in the validator would
                have caught either OES defect — the layer sat 2 vintages behind
                with a 4x double-count and validation passed every run. That
                remains the cheapest available guard against instance seven.

## [2026-09-03] all layers | tooling — annual vintage-recency assertions
Vintage before: n/a — no data changed
Vintage after:  unchanged
Checked:        n/a. This closes the "STILL OPEN" item carried in the
                2026-09-02 JOLTS follow-up entry and repeated in today's QCEW
                and OES entries: extending the recency assertion beyond JOLTS.
Outputs changed: none. One file: scripts/validate_outputs.py (+ the calendar).
Validation:     `validate_outputs.py --state 20` and the full-bloc run both
                pass against current outputs.
Notes:          **WHY: six instances, and the validator would have caught none
                of them.** ACS, CBP, BLS projections, JOLTS, QCEW and OES all
                failed the same way — fetch succeeded, pipeline succeeded,
                validation passed, dashboard served years-old numbers under a
                current timestamp. JOLTS got a recency assertion on 2026-09-02
                and it is the only reason that layer is now honest. Nine more
                layers now have the equivalent: QCEW (via the sector model's
                base_year), LAUS, OES state, OES sector, IPEDS, CBP, LODES,
                KSDE, ACS.

                **DESIGN: years, not days, and two-tier.** JOLTS is monthly so
                it can be checked in days. Everything else is annual, which
                means the best achievable detection latency is about one release
                cycle — you cannot distinguish "not published yet" from "frozen"
                faster than the publication cadence. Each entry in
                ANNUAL_VINTAGE_CHECKS carries a `normal_lag` (years behind the
                current calendar year once that year's release has landed) and a
                `released_month` (when we expect it, with a couple of months of
                grace already built in). Before that month the allowance is
                normal_lag + 1; after it, normal_lag.

                That two-tier shape is the whole trick. A flat threshold cannot
                work: tight enough to catch QCEW frozen one vintage back also
                fails every January when last year's data legitimately is not
                out yet. Making the allowance tighten once the release month
                passes gives both properties.

                **CALIBRATED AGAINST THIS REPO'S OWN HISTORY, not guessed.**
                Every threshold is set so the vintage actually in data/outputs
                today passes AND the frozen vintage each source was really found
                at would have failed:

                  source  frozen  lag  allowed  caught   now   lag  passes
                  qcew    2024     2      1      yes    2025    1    yes
                  laus    2023     3      2      yes    2025    1    yes
                  oes     2023     3      1      yes    2025    1    yes
                  ipeds   2023     3      2      yes    2024    2    yes
                  cbp     2022     4      3      yes    2023    3    yes
                  lodes   2021     5      4      yes    2023    3    yes
                  ksde    2023     3      2      yes    2024    2    yes
                  acs     2023     3      2      yes    2024    2    yes

                Verified end-to-end rather than only on paper: rolling
                oes_state_s20 back to 2023 and lodes_s20 back to 2021 in a
                scratch copy of data/outputs/ makes the validator fail on
                exactly those two layers and nothing else.

                **A DELIBERATE SECOND EFFECT: it is also an "overdue for a
                refresh" alarm.** Because the allowance tightens when a release
                month passes, today's outputs begin failing through 2027 as each
                agency's next vintage comes due — ACS and KSDE in June, OES in
                August, CBP/IPEDS/QCEW in September. Confirmed by running the
                check against every month of 2026 and 2027: zero failures for
                the rest of 2026, then that staggered escalation. This is
                intended. A layer nobody refreshed for a year should not
                validate silently — that is the exact condition all six
                instances shared.

                **HOW TO READ A FAILURE — two possibilities, and the message
                says both.** Either the layer is frozen (hardcoded year list, or
                a cache served without a freshness check — run
                audit_cache_freshness.py next), OR the agency's release slipped
                past the grace window. The second is genuinely possible, and the
                correct response is to confirm against the agency and widen the
                threshold WITH the evidence recorded in the calendar — not to
                delete the check. Written into the failure string so nobody has
                to remember it.

                **KNOWN TIGHT SPOT: IPEDS.** normal_lag 2 from September is
                exactly what separates the current 2024 from the frozen 2023 it
                was found at, so there is no slack there. IPEDS provisional
                timing is also the least well established of the nine (the
                calendar still lists its exact release date as unverified). If a
                future provisional slips past September this will fire; confirm
                against nces.ed.gov before widening. Flagged rather than
                silently loosened, because loosening it to 3 would have made it
                miss the real historical case.

                LODES got the opposite treatment — held flat at 4 year-round
                with no month tier, because its publisher is the most irregular
                here (8.3 shipped 2024-11-19; the 2023 data appeared with no
                announcement anyone caught). Better an honest loose threshold
                than a precise-looking month that is fiction.

                Absence is never a failure, matching the JOLTS rule: not every
                layer is written for every state, and some are written only
                under a flag.

                NOT COVERED, deliberately: the manual sources (SSA, KDOL labour
                force, KDOL/BLS projections, Projections Central). Their
                staleness is already reported by refresh_dashboard's
                MANUAL_SOURCES file-age check, which measures the right thing
                for a hand-placed file, and duplicating it here would produce
                two alarms for one condition.

## [2026-09-03] all sources | refreshed — full live re-fetch, zero drift
Vintage before: qcew 2025 · laus 2025 · oes state 2025 · oes sector 2025 ·
                ipeds 2024 · cbp 2023 · lodes 2023 · ksde 2024 · acs 2024 ·
                bls_proj 2025 · pc 2024 · ssa 2024 · jolts 2026-07
Vintage after:  identical — every layer confirmed current by LIVE re-fetch
                rather than by reading its own cache
Checked:        `python refresh_dashboard.py --states bloc` (no --sources), the
                full monthly refresh. Cleared and re-fetched all eight
                API-backed caches — laus, jolts, qcew, ipeds, lodes, oes, cbp,
                pc — across all five deployed states. acs_cache and ksde_cache
                are excluded by the driver's own design: both read hardcoded
                year lists, so clearing them re-downloads identical vintages.
                Confirmed via --dry-run before running.
Outputs changed: **NONE. `git status` is completely clean.** Every one of ~140
                outputs was rewritten (mtimes 13:12-13:53) and every one came
                out byte-identical to HEAD. Content-diffed as well: 0 genuine
                changes, 0 content-identical-but-byte-different rewrites.
Validation:     pass, 5/5 states.
Notes:          **THIS RUN IS THE STRONGEST EVIDENCE THE TREE IS HONEST that
                this project has produced.** Everything committed today was
                validated against caches that the same session had just written.
                This run threw all of those away and re-pulled from the
                agencies, and reproduced the committed outputs exactly. Three
                things follow:

                1. Every vintage is genuinely current, established by live
                   re-fetch rather than by a fetcher reading its own cache —
                   which is the precise failure mode that hid six instances of
                   silent staleness.
                2. The pipeline is byte-reproducible for unchanged inputs. That
                   is a stronger property than the 2026-08-27 CBP entry assumed
                   ("parquet is not byte-reproducible"); on this toolchain,
                   identical inputs do produce identical bytes. Worth knowing,
                   because it means a non-empty `git status` after a refresh is
                   ALWAYS real drift and never noise.
                3. The annual recency assertions added earlier today passed
                   5/5 against a completely rebuilt tree — their first test
                   against data they did not help produce.

                **ALL EIGHT CACHE CLEARS FAILED, and the refresh was still
                genuine.** Every one reported
                  [WARN] {name}_cache — could not clear: [WinError 5] Access is
                  denied
                This is the OneDrive lock documented for JOLTS on 2026-09-02 and
                for QCEW earlier today — but this run shows it hitting **100% of
                caches, not intermittently.** The refresh worked anyway because
                `shutil.rmtree` deletes a directory's CONTENTS before failing to
                remove the directory itself, so the caches were emptied and
                every fetcher missed. Proof it was a real re-fetch, not a
                cache-serve: the log shows "Downloading 2015 QCEW ZIP (~140 MB)"
                through 2025 and "Cached {year}: ... saved" for all eleven years
                — lines that only appear on a cache miss.

                **Do not read that as "the warning is harmless."** It means the
                clear is succeeding by accident, one filesystem call short of
                failing. If the lock ever lands on the file deletions rather
                than the directory removal, every `--sources X` run silently
                degrades to serve-from-cache with no error — and the ONLY thing
                standing between that and stale published output is the
                per-fetcher freshness guard in cache_freshness.py plus the
                vintage-keyed cache filenames. That is exactly what saved the
                JOLTS refresh on 2026-09-02. The message wording is also
                misleading: "could not clear" reads as "nothing happened", when
                in fact the contents did go.

                Worth fixing properly at some point: have clear_caches() delete
                contents explicitly and treat a surviving-but-empty directory as
                success, so the common case stops emitting a warning that a
                reader has to know folklore to interpret. Not done here —
                it is unrelated to a data refresh and wants its own diff.

                One unrelated pre-existing warning, unchanged: run_forecast.py
                line 314 emits the KDOL UI notice. That path is the abandoned UI
                claims source (fetch_kdol_ui.py returns empty by design; the
                labour-force export replaced it), and it warns on every run.
