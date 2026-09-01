# Data-source release calendar and refresh routines

Every upstream agency behind this dashboard publishes on its own calendar. This
file is the authority on **when each source publishes** and **which scheduled
routine refreshes it**, so the dashboard picks up new data within a day of
release instead of drifting until someone remembers to run a manual refresh.

Created 2026-08-20. Companion to [README §5](../README.md#5-data-sources) (what
each source is) and [README §8.4](../README.md#84-monthly-refresh-and-manual-sources)
(how the refresh driver works).

---

## 1. How the routines work

Each source has one scheduled Claude Code routine under
`~/.claude/scheduled-tasks/ks-refresh-*/`. Every routine follows the same shape:

1. **Check before working.** Establish the vintage currently in `data/outputs/`,
   then verify against the agency's own release page whether the next vintage has
   actually published. If it has not, the routine appends a `no-op` line to
   `docs/data-refresh-log.md` and stops. This matters because clearing a cache
   forces a full re-download — QCEW and LODES pull large bulk files, so firing
   blindly is expensive.
2. **Refresh only that source.** `refresh_dashboard.py --sources <name>` clears
   only that source's cache, so every other layer is served from cache.

   > **It does NOT isolate the git diff.** Measured on the 2026-08-27 CBP run:
   > `--sources cbp` still invokes `run_forecast.py --all`, which rewrites every
   > output for every state — 111 modified parquets, of which only 10 were CBP.
   > 92 were byte-identical rewrites (parquet is not byte-reproducible), but
   > **6 were genuine content drift in unrelated layers** that re-fetched or
   > regenerated along the way. Always diff by content, not by `git status`:
   >
   > ```bash
   > python -c "import io,subprocess,pandas as pd; ..."   # see the 2026-08-27 log entry
   > ```
   >
   > Budget review time for the whole tree, not just the named source.
3. **Refresh every deployed state.** `--states bloc` covers KS, CO, MO, NE, OK —
   the five states whose outputs are tracked in git and therefore served by the
   app. Refreshing Kansas alone leaves the neighbour states on an older vintage
   of the same series, which is exactly the kind of silent skew the dashboard's
   comparisons cannot show.
4. **Validate, log, and stop.** `scripts/validate_outputs.py` gates the result.
   The routine appends an entry to `docs/data-refresh-log.md` and leaves the
   changes **uncommitted** for review.

> **Routines never commit or push.** The Streamlit Cloud deployment builds from
> git, so new data does not reach the public dashboard until a human reviews the
> diff and pushes. This is deliberate: an automated push would publish a revised
> forecast with no one having looked at it. Expect to run `git add -A data/ &&
> git commit` yourself after reviewing a routine's report.

Because the cron windows below are generous (often two or three fires around a
release), the "check before working" step is what keeps them cheap. A fire that
finds nothing new costs one web check.

---

## 2. The calendar

Cron expressions are 5-field, evaluated in **local (Central) time**. "Fires"
is chosen to land one day after the expected release; where an agency's date
moves year to year, the window carries an extra fire or two to absorb the slip.

| # | Source | Cadence | Observed release evidence | Routine | Cron | Fires |
|--:|--------|---------|---------------------------|---------|------|-------|
| 1 | ACS 5-year | Annual | 2020–2024 released **2026-01-29**; PUMS 2026-03-05. Historically mid-December. | `ks-refresh-acs` | `0 7 12 12,1,2 *` | Dec 12, Jan 12, Feb 12 |
| 2 | QCEW | Quarterly (~5 mo lag) | Q1 2026 → **2026-08-28**; Q2 2026 → **2026-12-02**. News release and full data same date since Q4 2024. | `ks-refresh-qcew` | `0 7 3,29 3,6,9,12 *` | 3rd + 29th of Mar/Jun/Sep/Dec |
| 3 | LAUS (county) | Monthly | County/metro: Jun 2026 → **2026-07-29**; Jul 2026 → **2026-09-02**. (State-level lands earlier, ~3rd Friday.) | `ks-refresh-laus` | `0 7 4 * *` | 4th monthly |
| 4 | JOLTS | Monthly | Jul 2026 → **2026-09-01** 10:00 ET. Dec 2025 data slipped 2026-02-03 → 2026-02-05 (appropriations lapse). | `ks-refresh-jolts` | `0 7 2 * *` | 2nd monthly |
| 5 | IPEDS completions | Annual (provisional) | Provisional ≈9 months after the fall collection closes (collection closes mid-October). | `ks-refresh-ipeds` | `0 7 15 8,9,10 *` | Aug/Sep/Oct 15 |
| 6 | CBP | Annual (~18 mo lag) | 2023 CBP released **2025-06-26** (adopted 2026-08-27). 2024 CBP **not out as of 2026-08-27** — confirmed twice that day: the census.gov CBP updates page advertises 2023 as newest, and `api.census.gov/data.json` lists vintages 1986…2023 with no 2024 endpoint. | `ks-refresh-cbp` | `0 7 27 6,7,8 *` | Jun/Jul/Aug 27 |
| 7 | LODES | Annual | LODES 8.3 (2022 data) released **2024-11-19**. Tech doc rev 8.4 dated 2025-12-03. | `ks-refresh-lodes` | `0 7 20 11,12 *` | Nov 20, Dec 20 |
| 8 | OES/OEWS | Annual | May 2025 estimates released **2026-05-15** (delayed by the 2025-10-01→11-12 shutdown). Normal cadence is early April. | `ks-refresh-oes` | `0 7 4,16 4,5,6 *` | 4th + 16th of Apr/May/Jun |
| 9 | SSA OASDI-SC | Annual — **manual** | 2024 edition released **August 2025**; 2025 edition in hand **2026-09-01** (release date itself unconfirmed). Each edition reports data as of December of its reference year, so the 2025 edition is data year 2024. **Adopted 2026-09-01.** | `ks-refresh-ssa` | `0 8 15 8,9 *` | Aug 15, Sep 15 |
| 10 | KDOL labor force | Monthly — **manual** | Jul 2026 KS labor report → **2026-08-21** (3rd Friday, same day as the BLS state release). Annual benchmark revision released 2026-05-22. | `ks-refresh-kdol-labforce` | `0 8 22 * *` | 22nd monthly |

### KDOL labor force vs. LAUS — these are the same program, not duplicates

The KLIC report (#10) lives under **LAUS** in KLIC's report builder, and that is
correct: KDOL is Kansas's LAUS partner agency, so the KLIC numbers *are* the
state's LAUS estimates. It does not duplicate the BLS LAUS layer (#3) because the
grain differs — KLIC publishes **monthly** county estimates through last month,
while the BLS county series the fetcher pulls is **annual**. That monthly recency
is the entire reason this layer exists: it is the dashboard's current pulse.

Two consequences worth knowing:

- Pulling the "labor force" report out of KLIC's LAUS section is the right move.
  Downloading from **bls.gov** instead is not — the schema is different and the
  parser expects KDOL's 23-column export (`Areaname … Laborforce, Emplab, Unemp,
  Unemprate, Clfprate, Emppopratio … prelim`).
- KLIC emits that report as **either** HTML-as-`.xls` (Telerik report builder) or
  a real `.xlsx`. Both are accepted: the parser sniffs the file's magic bytes
  rather than trusting the extension, and normalizes the code columns, because
  the `.xlsx` path types `Areatype` as integers (`4`) where the HTML path gives
  strings (`"04"`). The driver picks whichever file was downloaded most recently,
  since the filename carries a fixed `99999999` sentinel instead of a vintage.
| 11 | KSDE / CCD | Annual | Via the Urban Institute Education Data API, which lags the NCES CCD collection. Release date unconfirmed. | `ks-refresh-ksde` | `0 7 18 2 *` | Feb 18 |
| 12 | BLS national projections | Annual — **manual** | 2024–34 released **2025-08-28**; **2025–35 released 2026-08-27** (both the last Thursday of August). **2025–35 adopted 2026-09-01.** | `ks-refresh-bls-projections` | `0 8 29 8,9 *` | Aug 29, Sep 29 |
| 13 | KDOL KS projections | Biennial cycle — **manual** | 2024–2034 workbooks currently adopted (industry, occupational, and the companion demand book). Next cycle date unconfirmed. | `ks-refresh-kdol-projections` | `0 8 15 9,10,11 *` | Sep/Oct/Nov 15 |
| 14 | Projections Central | Annual, rolling by state | States publish their long-term cycle on their own timetables, so there is no single national date. | `ks-refresh-projections-central` | `0 7 10 2,5,8,11 *` | Feb/May/Aug/Nov 10 |

### Sources that need a code change, not just a cache clear

- **ACS (#1)** is *notify-only*. `fetch_acs.py` reads a hardcoded `ACS_YEARS`
  list, so a new 5-year vintage does not enter the model until that list is
  edited by hand. The routine therefore reports the new vintage and stops rather
  than clearing `acs_cache` — clearing it without editing `ACS_YEARS` just
  re-downloads the same years. `--sources acs` exists for the run *after* the
  edit.
- **CBP (#6)** is the same shape as ACS and was **missing from this list until
  2026-08-27**, which is how the 2023 vintage (published 2025-06-26) sat unadopted
  for over a year. `fetch_cbp.py` reads a hardcoded `CBP_YEARS` list — bumped to
  `range(2015, 2024)` on 2026-08-27 to adopt 2023 — so `--sources cbp`
  re-downloads only the years that list names. Before the bump it re-pulled 2015–2022 years no
  matter what Census had published. Bump `CBP_YEARS` first, then refresh.
  `_NAICS_VAR` needed no edit for 2023 (CBP 2023 still uses NAICS2017); re-check
  it when 2024 lands. Since 2026-08-27 a cache missing a requested year
  self-invalidates instead of being served short, so the bump alone now forces
  the re-fetch.

  The lesson generalises: **compare the newest published year against the vintage
  actually in `data/outputs/`, never against what the fetcher is configured to
  want.** A hardcoded year list makes those two silently diverge, and only the
  first check catches it.

- **BLS national projections (#12)** was the third instance, found 2026-08-29 when
  the 2025–35 cycle published. It had two hardcodings and neither errored. **One
  is fixed permanently; one remains by design.**

  1. *Fixed 2026-09-01 — year-literal column hints.* `_BASE_EMP_HINTS` /
     `_PROJ_EMP_HINTS` matched "employment, 2024" / "employment, 2034" as literal
     strings, so a 2025–35 workbook matched neither, `_parse_proj_df` returned an
     empty frame, and the caller printed a warning rather than raising. Replaced
     with `_EMP_YEAR_RE` (matches "Employment, &lt;4 digits&gt;", anchored to the whole
     column name) plus `detect_cycle()`, which takes the earliest matching year as
     base and the latest as projection target. **No edit is needed here for future
     cycles.** The anchor is load-bearing: unanchored, it would also match the
     adjacent "Employment distribution, percent, YYYY" and "Employment change,
     numeric, YYYY–YY" columns.
  2. *Still required each cycle — the year defaults.*
     `fetch_national_projections()` defaults `base_year` / `proj_year` (currently
     2025 / 2035) and `run_forecast.py` passes no years. The cache filename is
     cycle-keyed (`bls_proj_national_{base}_{proj}.parquet`), so the held parquet
     is served on sight and a freshly downloaded workbook is never opened until
     those defaults move. Bumping them changes the cache key, and that is what
     forces the reread. Deleting the parquet without bumping just reparses the old
     cycle under the old label.

  So the order is now: bump the two defaults, drop the workbook in as
  `bls_proj_national_{base}_{proj}.xlsx`, then refresh. A **cycle-mismatch guard**
  added 2026-09-01 makes the remaining failure mode loud: if the workbook's
  detected years disagree with the requested ones, the fetch raises rather than
  writing a parquet labelled with a cycle it does not contain.

- **KSDE (#11)** is likewise conservative: `ksde_cache` is preserved by the
  monthly refresh on purpose.

### Manual sources

Four sources have no working public API and need a file placed by hand before
their routine can do anything (see README §8.4 for the exact paths). Those
routines check whether a fresh file has appeared; if not, they report the
download URL and stop.

| Source | Place the file at | From |
|--------|-------------------|------|
| KDOL labor force | `data/kdol_cache/labforce__*.xls` **or** `.xlsx` | `klic.dol.ks.gov` → LAUS labor force report |
| SSA disability | `data/ssa_cache/oasdi_sc{YY}.xlsx` (e.g. `oasdi_sc25.xlsx`) | SSA Policy Statistics (OASDI-SC) |
| BLS national projections | `data/bls_proj_cache/bls_proj_national_{base}_{proj}.xlsx` (e.g. `bls_proj_national_2025_2035.xlsx`) | BLS Employment Projections |
| KDOL KS projections | `data/kdol_proj/*.xlsx` (published filename is fine) | KDOL LMIS employment projections |

**Name each file for its vintage, and leave the old one in place.** Every one of
these is selected by a glob that takes the newest match, so a new edition
dropped in beside the old one wins automatically — and the year in the filename
is what the parser reads the vintage from. `oasdi_sc25.xlsx` is parsed as the
2025 edition (data year 2024); `bls_proj_national_2025_2035.xlsx` as the 2025–35
cycle. Two consequences, both learned the hard way on 2026-09-01:

- **Do not overwrite an old edition with a new one under the old name.** The
  glob would still pick it, but it would be labelled with the wrong year.
- **Do not use the legacy vintage-less `bls_proj_national_manual.xlsx` for new
  downloads.** It is still accepted as a last-resort fallback, but a
  vintage-named workbook always wins over it, and a file under the legacy name
  cannot say which cycle it holds.

---

## 3. Staleness thresholds

`refresh_dashboard.py` flags manual sources whose file has aged past a
threshold. The default is 100 days, sized for the annual publications. The KDOL
labor force file publishes monthly and carries its own 40-day window — under the
old flat default it drifted three monthly vintages behind while still reporting
"ok".

---

## 4. Items Requiring Verification

Release dates below could not be confirmed against a primary source and are
inferred from cadence. Each routine re-checks its own source at run time, so a
wrong date here costs a wasted no-op fire, not bad data — but they are worth
confirming.

| Item | What needs confirming | Suggested source |
|------|----------------------|------------------|
| CBP 2024 vintage | Whether 2024 CBP has published, and its date. **Checked 2026-08-27: not out.** Newest published vintage is 2023 (released 2025-06-26), now adopted. The API catalog is the cheaper of the two checks. | `api.census.gov/data.json` (vintage list) / `census.gov/programs-surveys/cbp/news-updates.html` |
| LODES post-8.3 release | Whether a 2023-data LODES release shipped after 8.3 (2024-11-19). Tech doc rev 8.4 is dated 2025-12-03, which hints at a release but does not confirm one. | `lehd.ces.census.gov/data/` |
| IPEDS provisional date | The exact 2024-25 completions provisional release date. Only the ≈9-month-after-collection rule was confirmed. | `nces.ed.gov/ipeds/survey-components/data-release-schedule` |
| SSA OASDI-SC 2025 edition | Its **release date** only. The edition itself is confirmed to exist and was adopted 2026-09-01 (data year 2024), but it was downloaded by hand without recording a publication date, so the August cadence is still inferred from the 2024 edition alone. | `ssa.gov/policy/docs/statcomps/oasdi_sc/` |
| KDOL next projections cycle | When KDOL publishes the cycle following 2024–2034. | `dol.ks.gov/lmis/employment-projections` |
| Projections Central cadence | Whether Kansas and the neighbour states publish their long-term cycle on a predictable month. | `projectionscentral.org` |
| KSDE / CCD via Urban Institute | When the Urban Institute Education Data API refreshes CCD enrollment each year. | `educationdata.urban.org` |
| QCEW Q3/Q4 dates | BLS lists Q3 and Q4 2026 releases as "to be determined in 2027". The two-fire cron window is a hedge against that. | `bls.gov/cew/release-calendar.htm` |

Note that several 2026 dates above reflect the **2025-10-01 → 2025-11-12
appropriations lapse**, which pushed OEWS from April to May and ACS from
December to late January. Treat 2026 dates as slipped rather than as the new
normal, and expect the underlying cadence to reassert itself.
