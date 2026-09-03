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

> **Step 1 is doing less work than it looks like it is.** Establishing "the
> vintage currently in `data/outputs/`" only catches a stale source if the
> fetcher can actually advance past it. Twice now — CBP, then JOLTS — the
> vintage in outputs matched what the fetcher was configured to want, so
> nothing looked wrong while the source sat years behind the agency. Run
> `python scripts/audit_cache_freshness.py` to see which sources currently
> have that failure mode available to them; see "Two checks that make this
> class of defect loud" below.

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
| 2 | QCEW | Quarterly (~5 mo lag), but **this layer consumes the ANNUAL file** | Q1 2026 → **2026-08-28**; Q2 2026 → **2026-12-02**. News release and full data same date since Q4 2024. **Annual averages 2015–2025 adopted 2026-09-03**; `2025_annual_by_area.zip` → 200 (115 MB), `2026` → 404. | `ks-refresh-qcew` | `0 7 3,29 3,6,9,12 *` | 3rd + 29th of Mar/Jun/Sep/Dec |
| 3 | LAUS (county) | Monthly | County/metro: Jun 2026 → **2026-07-29**; Jul 2026 → **2026-09-02**. (State-level lands earlier, ~3rd Friday.) The layer uses **annual averages** (period M13), so the usable vintage lags a full year behind the monthly release. **2024 + 2025 adopted 2026-09-02.** | `ks-refresh-laus` | `0 7 4 * *` | 4th monthly |
| 4 | JOLTS | Monthly | Jul 2026 → **2026-09-01** 10:00 ET. Dec 2025 data slipped 2026-02-03 → 2026-02-05 (appropriations lapse). | `ks-refresh-jolts` | `0 7 2 * *` | 2nd monthly |
| 5 | IPEDS completions | Annual (provisional) | Provisional ≈9 months after the fall collection closes (collection closes mid-October). **Collection year 2024 adopted 2026-09-02** (`C2024_A.zip` + `HD2024.zip` both live; 2025 still 404). | `ks-refresh-ipeds` | `0 7 15 8,9,10 *` | Aug/Sep/Oct 15 |
| 6 | CBP | Annual (~18 mo lag) | 2023 CBP released **2025-06-26** (adopted 2026-08-27). 2024 CBP **not out as of 2026-08-27** — confirmed twice that day: the census.gov CBP updates page advertises 2023 as newest, and `api.census.gov/data.json` lists vintages 1986…2023 with no 2024 endpoint. | `ks-refresh-cbp` | `0 7 27 6,7,8 *` | Jun/Jul/Aug 27 |
| 7 | LODES | Annual | LODES 8.3 (2022 data) released **2024-11-19**. Tech doc rev 8.4 dated 2025-12-03 — and **2023 data is in fact published**, confirmed by direct request 2026-09-02. **2022 + 2023 adopted 2026-09-02**; 2024 returns 404. | `ks-refresh-lodes` | `0 7 20 11,12 *` | Nov 20, Dec 20 |
| 8 | OES/OEWS | Annual — **part-manual** | May 2025 estimates released **2026-05-15** (delayed by the 2025-10-01→11-12 shutdown). Normal cadence is early April. **Fully unblocked 2026-09-03 — both layers adopted through 2025** (state 2015–2025, sector 2021–2025). Every `oesm25*` pattern 403s and `oesm24{st,in4,nat}` too, so each layer falls through its own three-tier chain ending at `data/oes_manual/all_data_M_{year}.xlsx`. The same change fixed a 3–4x aggregation double-count in the sector layer; see §4. | `ks-refresh-oes` | `0 7 4,16 4,5,6 *` | 4th + 16th of Apr/May/Jun |
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
| 11 | KSDE / CCD | Annual | Via the Urban Institute Education Data API, which lags the NCES CCD collection. Release date unconfirmed. **Collection year 2024 adopted 2026-09-02.** Note the 2025 `directory` endpoint answers 200 with **count=0** — endpoint existence is not publication here, so check row counts before bumping. | `ks-refresh-ksde` | `0 7 18 2 *` | Feb 18 |
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

- **JOLTS (#4)** was the fourth instance, found 2026-09-02 — and the worst of
  them, because it is the only **monthly** source in this list. It sat at
  reference month **2023-12 for 31 months** while the routine fired on the 2nd
  of every month and reported success each time. **Both halves are now fixed
  permanently; no year bump is needed next cycle.**

  1. *Fixed — the frozen year list.* `JOLTS_YEARS = list(range(2015, 2024))`,
     and `run_forecast.py` passes no `years`, so `--sources jolts` re-requested
     the same nine years forever. Replaced with `default_jolts_years()`, which
     computes 2015 → current calendar year. Asking for a partly-published year
     is harmless: the API returns the months that exist.
  2. *Fixed — the cache served on sight.* This is the half that made the
     staleness **silent**, and it is the more dangerous of the two. The cache
     filename `jolts_{seasonal}.parquet` carries **no vintage**, and the code
     returned that parquet whenever the file merely existed, without asking
     whether it held the requested years. It now compares held years against
     requested years and re-fetches when short — the same guard CBP received
     2026-08-27.

  **The guard earned itself on the very first run.** The cache clear *failed*
  (`[WARN] jolts_cache — could not clear: [WinError 5] Access is denied`, a
  OneDrive lock), so `--sources jolts` fell back to serving cache. The refresh
  advanced only because the new freshness check invalidated the stale parquet.
  Without it that run would have reported success and changed nothing — which
  is precisely what the previous 31 runs did.

  **The operational lesson generalises past JOLTS:** `--sources X` silently
  degrades to "serve from cache" for **any** source whose cache directory is
  locked by OneDrive and whose fetcher lacks a freshness guard. A cache clear
  is a best-effort operation on this machine, so it cannot be the only thing
  standing between a refresh and stale output.

  A third change, methodological: incomplete years are now excluded from the
  annual averages. JOLTS 2026 had 7 of 12 months and `compute_annual_averages`
  took a plain mean over whatever was present, so a Jan–Jul mean would have
  published as "2026" — and the dashboard takes `year.max()` as its **headline**
  vacancy rate and feeds each year to the trend-slope regression as an
  equally-weighted point. `complete_years()` now gates it and prints what it
  dropped. The monthly `jolts.parquet` still carries every month; nothing is
  discarded, it just cannot masquerade as an annual figure.

- **QCEW (#2)** was the fifth instance, found 2026-09-03. **Fixed permanently;
  no year bump is needed next cycle.** `QCEW_YEARS = list(range(2015, 2025))`
  with no `years` passed from `run_forecast.py`, so the request set could not
  advance past 2024 while BLS had 2025 published. Replaced with
  `default_qcew_years()` (2015 → last calendar year).

  **The trap specific to this source: its routine watches the wrong release.**
  QCEW's headline cadence is *quarterly*, and that is what the cron window and
  the routine's own instructions track — but `fetch_qcew.py` downloads
  `{year}_annual_by_area.zip`. A new quarter does not advance this layer at all.
  The annual averages for year *Y* publish alongside the Q4 *Y* file (~June of
  *Y+1*), so **the check that settles this source's vintage is a probe of the
  annual ZIP, not the quarterly release date.** Confirming Q1 2026 shipped on
  schedule told us nothing about the 2025 annual file that had been sitting
  there since roughly June.

  Because the year list is now computed, the fetcher asks for last calendar year
  even early in Q1 when it may not exist yet. `_in_publication_window()` makes
  that safe **asymmetrically**: a 404 on one of the two most recent calendar
  years is "not out yet" and skips; a 404 on anything older re-raises. Do not
  relax the older-year half into a blanket skip — OES lost its 2024+ files to a
  silent per-file 403 exactly there, and a blanket skip would quietly shrink the
  history the sector trend regression fits on instead of failing.

- **OES (#8)** was the sixth instance, found and fixed 2026-09-03 — and the only
  one where the year list was *honestly* short rather than silently frozen.
  `OES_YEARS` deliberately stopped at 2023 with a BLOCKER comment, because the
  URL the module used had died and a bumped list would have re-downloaded the
  whole series every run and still landed on 2023. So the config never claimed a
  vintage the data lacked. Resolved by making acquisition fall through tiers
  instead of trusting one URL.

  **The generalisable lesson is about the manual tier.** OES is the first source
  here where *some* years come from an API and one year cannot. The manual
  workbook therefore had to live outside `data/oes_cache/`, because
  `--sources oes` `rmtree`s that directory — storing it there would have made the
  first refresh after the fix delete the only copy of 2025. Any future partial
  fallback needs the same treatment: **a hand-placed file must never sit in a
  directory the refresh driver clears.**

- **KSDE (#11)** is likewise conservative: `ksde_cache` is preserved by the
  monthly refresh on purpose.

### Two checks that make this class of defect loud (added 2026-09-02)

Six instances of the same defect in three weeks — ACS, CBP, BLS projections,
JOLTS, QCEW, OES — is a pattern, not a run of bad luck. Both halves are invisible from the
outside: the fetch succeeds, the pipeline succeeds, validation passes, and the
dashboard serves years-old numbers under a current timestamp. Two checks now
exist so the fifth instance is found by running a command rather than by
someone noticing a suspicious chart.

**1. `scripts/audit_cache_freshness.py` — audits the cache contract.**

```bash
python scripts/audit_cache_freshness.py            # human-readable
python scripts/audit_cache_freshness.py --json     # machine-readable
python scripts/audit_cache_freshness.py --strict   # exit 1 on UNVALIDATED
```

It classifies every `.exists()`-guarded cache read in every `fetch_*.py`:

| Verdict | Meaning |
|---|---|
| `VINTAGE-KEYED` | Filename interpolates a year, so a new vintage is a new filename and therefore a cache miss. Safe by construction. |
| `VALIDATED` | Fixed filename, but contents are checked against the requested years and re-fetched when short. |
| `UNVALIDATED` | Fixed filename returned on sight. **Can serve stale data indefinitely.** |

It then cross-checks the years each fetcher *requests* against the years
actually in `data/outputs/`, and flags any source where **both halves are
present** — an `UNVALIDATED` cache *and* a year list ending well before the
present.

**Read the flag logic carefully, because the intuitive reading is backwards.**
For a frozen source, requested years and output years *agree perfectly* — both
are stuck at the same place. A matching pair is therefore **not** evidence of
currency, and the absence of a shortfall proves nothing. That is why the
contract column exists alongside the year columns.

**What a clean run does and does not mean.** It means the *cache* cannot hide
staleness. It says nothing about whether the data is current, because a frozen
year list still can — that was the ACS and CBP failure exactly. The tool reads
the fetcher's own defaults, which is the thing that was wrong in all four
instances, so **only the agency's release page settles the vintage question.**
It is also a heuristic AST scan, not a proof: it recognises the cache-read
shapes this codebase uses today, and a novel shape may be missed.

**2. `scripts/validate_outputs.py` now checks JOLTS.** Nothing in the validator
looked at JOLTS at all, which is why nothing failed for 31 months. It now
asserts, on each run: the newest monthly reference month is within
`JOLTS_MAX_STALE_DAYS` (120 — loose enough to absorb a release slip, tight
enough that a layer frozen for *years* fails immediately); the annual file
contains only 12-month-complete years; no complete year present monthly is
missing from the annual averages; all five dashboard sectors are present; and
rates are non-null and within 0–25%. Absence of the files is **not** a failure,
since `run_forecast.py` writes them only under `--jolts` and the dashboard
degrades to "not loaded".

A recency assertion of this shape would have caught JOLTS in month two instead
of month 31. **It is the cheapest check in this repo and it is worth extending
to the other layers** — the machinery is in `_failures_for_jolts()` and the
per-source thresholds are the only new input required.

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
| OES/OEWS (2025 only) | `data/oes_manual/all_data_M_{year}.xlsx` (e.g. `all_data_M_2025.xlsx`) | `bls.gov/oes/tables.htm` → "All data" workbook |

**OES is a PARTIAL fallback, unlike the four above.** Those sources have no
public API at all; OES fetches 2015–2024 live and needs a hand-placed file only
for years BLS will not serve (currently 2025 alone). Two consequences:

- **The directory matters.** It is `data/oes_manual/`, *not* `data/oes_cache/`.
  `oes_cache` is in `ANNUAL_API_CACHES`, which `clear_caches()` removes with
  `shutil.rmtree` — a workbook stored there would be deleted by the first
  `--sources oes` refresh, silently taking 2025 with it.
- **It serves both layers.** One workbook supplies the state rows
  (`AREA_TYPE 2`) and the national industry rows (`AREA_TYPE 1`), and the
  parsed slices are cached separately, so the 80 MB file is read once.

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
| QCEW Q3/Q4 dates | BLS lists Q3 and Q4 2026 releases as "to be determined in 2027". The two-fire cron window is a hedge against that. **Note the quarterly date is not what gates this layer** — see the QCEW entry in §2's code-change list. The Q4 date is the one worth knowing, because the annual averages ride along with it. | `bls.gov/cew/release-calendar.htm` |
| QCEW 2025 suppression jump | Whether the 5–9 point rise in suppressed county-sector records for 2025 (KS 39.3%→44.8%, CO 23.6%→31.2%, MO 27.8%→36.3%, NE 37.3%→41.5%, OK 25.8%→34.9%, observed 2026-09-03) is normal newest-vintage conservatism that relaxes on revision, or a standing BLS disclosure-methodology change. Statewide totals are fully disclosed and plausible, and the model fills every county, so this is a data-quality question rather than a defect. Re-check the same figures after the next annual revision. | `bls.gov/cew/questions-and-answers.htm` (disclosure methodology); re-probe `s{fips}_2025.parquet` suppression share after the next revision |
| ~~**OES/OEWS (#8) state layer — BLOCKED**~~ | **Resolved 2026-09-03 — state layer adopted through 2025.** The 403 pattern is real and unchanged on re-probe, so it was worked around rather than fixed: acquisition is now three-tiered (state-only ZIP → all-areas ZIP → hand-placed workbook). 2024 comes from `oesm24all.zip`; 2025 comes from `data/oes_manual/all_data_M_2025.xlsx`, because **every** `oesm25*` pattern 403s (`st`, `all`, `in4`, `nat`) and no URL for it was found. The `AREA_TYPE == 2` fix mattered exactly as predicted — measured 2,922 MSA rows in the 2024 file whose CBSA codes begin "20" (Dothan AL, Duluth MN-WI, Dubuque IA…) that the old AREA-prefix mask would have counted as Kansas. One correction to the old note: all three tiers share an identical 32-column schema and AREA for a state row is a bare 2-char FIPS, **not** the 7-char `2000000` the code's comment claimed — so exact FIPS equality replaced the prefix match. Do not "fix" a 403 here by spoofing a browser UA; a browser UA gets 403 for every year including 2023. | `bls.gov/oes/tables.htm`; `bls.gov/oes/special.requests/` |
| ~~**OES/OEWS (#8) sector layer — capped at 2023 + granularity double-count**~~ | **Resolved 2026-09-03 — sector layer adopted through 2025 and the double-count fixed.** Root cause was not "in4 mixes granularity in one file": `oesm{yy}in4.zip` ships **eight** workbooks pre-split by level (`natsector`, `nat3d`, `nat4d`, `nat5d_6d`, three `*_owner_*`, `file_descriptions`) and the old reader concatenated **all of them**, so the nesting came from stitching together files BLS had deliberately separated. Worse, the largest-xlsx fallback would have picked `nat4d` — 4-digit rows with no sector totals at all. Now the `natsector` member is named explicitly. Three axes had to be collapsed, not one: **industry** (`I_GROUP=='sector'`), **occupation** (`O_GROUP=='detailed'` — 52% of rows were minor/broad/major groups, and the top-N tables were ranking aggregates against their own children, with Registered Nurses appearing twice as broad 29-1140 and detailed 29-1141), and **ownership** (`OWN_CODE` values are overlapping aggregates — 57 ⊃ 5, 123 ⊃ 235 — so they must never be summed). Ownership needed no filter, because BLS publishes exactly one aggregate per sector (62→58, 71/72→57, rest→5), but that is now asserted rather than assumed. | resolved; see the `_parse_industry_oes` comments |
| OES May 2026 vintage | Whether May 2026 estimates restore a working `oesm26st.zip`, or whether the 403 pattern persists and 2026 also needs a hand-placed workbook. The three-tier fetch will pick up a restored URL automatically — no code change needed if BLS starts serving it again. | `bls.gov/oes/special.requests/` (probe `oesm26st.zip` / `oesm26all.zip`) |
| ~~LAUS (#3) vintage~~ | **Resolved 2026-09-02 — adopted through 2025.** Verified by querying `LAUCN201730000000003` (Sedgwick County) over 2022–2026: annual averages (M13) returned for 2022–2025; 2026 had 7 monthly observations and no annual average yet, as expected. | `bls.gov/lau/` |
| ~~LODES (#7) post-8.3 release~~ | **Resolved 2026-09-02 — adopted through 2023, and 2023 turned out to exist.** Requesting `LODES8/ks/od/ks_od_main_JT00_{year}.csv.gz` returned 200 for 2021 (6.23 MB), 2022 (6.42 MB) **and 2023 (6.57 MB)**; 2024 returned 404. This calendar had recorded 2022 (LODES 8.3) as newest, so **two** vintages were unadopted, not one — tech-doc rev 8.4 dated 2025-12-03 was the hint, and it was right. Supersedes the old "post-8.3 release" question. | `lehd.ces.census.gov/data/` |
| ~~IPEDS (#5) vintage~~ | **Resolved 2026-09-02 — adopted through collection year 2024.** `C2024_A.zip` (4.68 MB) and `HD2024.zip` (1.09 MB) both returned 200; `C2025_A.zip` and `HD2025.zip` both 404. Both files are required — HD supplies the institution-to-county map. Does not answer the provisional-release-*date* question below, only which vintage is downloadable now. | `nces.ed.gov/ipeds/` |
| ~~KSDE (#11) vintage~~ | **Resolved 2026-09-02 — adopted through collection year 2024.** `ccd/enrollment/{year}/grade-9/?fips=20` returned 200 for 2022 (count 320), 2023 (286) and 2024 (290); 2025 returned **HTTP 500**. `ccd/directory` returned 337 Kansas districts for 2024 and **count=0** for 2025 — so 2025 exists as an endpoint but carries no data, which is a trap for anyone bumping the list on endpoint existence alone. | `educationdata.urban.org` |

Note that several 2026 dates above reflect the **2025-10-01 → 2025-11-12
appropriations lapse**, which pushed OEWS from April to May and ACS from
December to late January. Treat 2026 dates as slipped rather than as the new
normal, and expect the underlying cadence to reassert itself.
