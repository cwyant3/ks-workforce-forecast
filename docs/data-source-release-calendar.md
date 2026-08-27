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
| 6 | CBP | Annual (~18 mo lag) | 2023 CBP released **2025-06-26**. 2024 CBP not yet out as of 2026-08-20. | `ks-refresh-cbp` | `0 7 27 6,7,8 *` | Jun/Jul/Aug 27 |
| 7 | LODES | Annual | LODES 8.3 (2022 data) released **2024-11-19**. Tech doc rev 8.4 dated 2025-12-03. | `ks-refresh-lodes` | `0 7 20 11,12 *` | Nov 20, Dec 20 |
| 8 | OES/OEWS | Annual | May 2025 estimates released **2026-05-15** (delayed by the 2025-10-01→11-12 shutdown). Normal cadence is early April. | `ks-refresh-oes` | `0 7 4,16 4,5,6 *` | 4th + 16th of Apr/May/Jun |
| 9 | SSA OASDI-SC | Annual — **manual** | 2024 edition released **August 2025**; each edition reports data as of December of its reference year. | `ks-refresh-ssa` | `0 8 15 8,9 *` | Aug 15, Sep 15 |
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
| 12 | BLS national projections | Annual — **manual** | 2024–34 released **2025-08-28**. 2025–35 not yet out as of 2026-08-20 — expected imminently. | `ks-refresh-bls-projections` | `0 8 29 8,9 *` | Aug 29, Sep 29 |
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
  for over a year. `fetch_cbp.py` reads a hardcoded `CBP_YEARS` list, currently
  `range(2015, 2023)`, so `--sources cbp` re-downloads the same 2015–2022 years no
  matter what Census has published. Bump `CBP_YEARS` first, then refresh.
  `_NAICS_VAR` needs no edit for 2023 (CBP 2023 still uses NAICS2017).

  The lesson generalises: **compare the newest published year against the vintage
  actually in `data/outputs/`, never against what the fetcher is configured to
  want.** A hardcoded year list makes those two silently diverge, and only the
  first check catches it.

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
| SSA disability | `data/ssa_cache/oasdi_sc{YY}.xlsx` | SSA Policy Statistics (OASDI-SC) |
| BLS national projections | `data/bls_proj_cache/bls_proj_national_manual.xlsx` | BLS Employment Projections |
| KDOL KS projections | `data/kdol_proj/*.xlsx` (published filename is fine) | KDOL LMIS employment projections |

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
| CBP 2024 vintage | Whether 2024 CBP has published, and its date. As of 2026-08-20 the newest confirmed vintage is 2023 (released 2025-06-26). | `census.gov/programs-surveys/cbp/news-updates.html` |
| LODES post-8.3 release | Whether a 2023-data LODES release shipped after 8.3 (2024-11-19). Tech doc rev 8.4 is dated 2025-12-03, which hints at a release but does not confirm one. | `lehd.ces.census.gov/data/` |
| IPEDS provisional date | The exact 2024-25 completions provisional release date. Only the ≈9-month-after-collection rule was confirmed. | `nces.ed.gov/ipeds/survey-components/data-release-schedule` |
| SSA OASDI-SC 2025 edition | The 2025 edition's release date. Only "2024 edition → August 2025" was confirmed. | `ssa.gov/policy/docs/statcomps/oasdi_sc/` |
| BLS 2025–35 projections | Whether the 2025–35 cycle has published and on what date. 2024–34 was 2025-08-28. | `bls.gov/emp/` |
| KDOL next projections cycle | When KDOL publishes the cycle following 2024–2034. | `dol.ks.gov/lmis/employment-projections` |
| Projections Central cadence | Whether Kansas and the neighbour states publish their long-term cycle on a predictable month. | `projectionscentral.org` |
| KSDE / CCD via Urban Institute | When the Urban Institute Education Data API refreshes CCD enrollment each year. | `educationdata.urban.org` |
| QCEW Q3/Q4 dates | BLS lists Q3 and Q4 2026 releases as "to be determined in 2027". The two-fire cron window is a hedge against that. | `bls.gov/cew/release-calendar.htm` |

Note that several 2026 dates above reflect the **2025-10-01 → 2025-11-12
appropriations lapse**, which pushed OEWS from April to May and ACS from
December to late January. Treat 2026 dates as slipped rather than as the new
normal, and expect the underlying cadence to reassert itself.
