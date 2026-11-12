# County workforce pressure release

(The filename keeps "pilot" because other documents link to it. The scope has been statewide since 2026-11-10.)

## Current release

Adopted release: `20261112T193557_923d1ce9` (2026-11-12). Rollback target: `20261112T154042_8f2556d5`. Evidence folder: `data/pressure_sources/2026-11-12-oews-wichita` (git-ignored, local only). Dashboard commit `0df157f` was pushed to working/deployment branches; Chris restarted the hosted app. Hosted verification confirms this release, all 486 Wichita options, published-inventory scope, three occupation wage checks, lower-bound/hourly-only explanations and Registered Nurses in Workforce Outlook. Each source update is recorded in `docs/data-refresh-log.md`. It is not a statewide shortage score; no composite is calculated.

Coverage, all 105 Kansas counties unless noted:

- **Resident supply:** ACS 2020–2024 (B23025, S2301); BLS LAUS monthly, January–September 2026.
- **Employer activity:** BLS QCEW 2025Q1 and 2026Q1, nine sectors, private. Where QCEW withholds a county's sector jobs, Census QWI and County Business Patterns 2023 appear as separately labeled alternatives. They never fill QCEW cells, growth, pay or rankings.
- **Hiring:** Census QWI, nine sectors, 2024Q1–2026Q1, all from one LEHD release (R2026Q4). The release ID is read from the saved `qwi-version.txt`. Every quarter must be re-pulled together when LEHD publishes a new release.
- **Commuting:** LODES8 2023 JT01. Inbound uses Kansas main+aux. Resident outbound uses Kansas main plus every other state's aux; Alaska and Michigan publish no 2023 files, and that gap is disclosed on every measure (Chris's decision, 2026-11-10).
- **Training:** IPEDS 2025 provisional awards at reporting institutions (36 counties have one).
- **Regional occupation outlook:** KDOL 2022–2032, all seven projection regions. Each county maps to its region in `crosswalks/county_areas_20261020.csv`.
- **Occupation pay:** BLS OEWS May 2025 for all eight Kansas pay areas, mapping all 105 counties once. Wichita now has all 485 detailed occupations plus the all-occupations total from the held official workbook; 480 rows have published median annual wages. Kansas City, Topeka, Lawrence, Manhattan, St. Joseph, Joplin and Kansas nonmetro retain the existing API inventories. Nonmetro area `2000006` covers 85 counties; it uses substate API type `M`, not national type `N`. Cross-state metro estimates include Missouri members. See `crosswalks/oews-areas-source.md` for verified definitions and acquisition scope.
- **Kansas City metro panel:** shown for the five Kansas metro counties. It compares all 14 counties of OEWS area 28140 with the same sources: ACS, LAUS, QCEW and working-age forecasts. Both states' forecasts use the same cohort model and the same NCES CCD single-grade youth update. The builder refuses forecasts whose seed or base year differ.

## Rebuild the current release

For the Wichita expansion, preprocess/profile the official `data/oes_manual/all_data_M_2025.xlsx` through the vault's existing preprocessing tool. Copy the reviewed all-Kansas source bundle to a new folder, then run `scripts/stage_oews_area.py --area 48620 --slug wichita --out NEW --workbook data/oes_manual/all_data_M_2025.xlsx`. This streams the exact source area into a CSV with all original columns and records the workbook hash. The adapter prefers that CSV over the historical sample, selects cross-industry/all-covered ownership detailed rows plus the total, and never appends duplicate sample rows. Keep the sample for historical reproducibility. Profiled workbook schema and source-slice provenance are recorded with the release.

To reproduce the November 12 extension, copy the November 10 Kansas City metro evidence into a new dated folder and run `scripts/stage_oews_area.py --area CODE --slug NAME --out NEW` for Topeka (`45820`, `topeka`), Lawrence (`29940`, `lawrence`), Manhattan (`31740`, `manhattan`), St. Joseph (`41140`, `stjoseph`), Joplin (`27900`, `joplin`), and Kansas nonmetro (`2000006`, `kansasnonmetro`). Preserve the reviewed area-definition/probe records with that bundle. Completed API batches are cached for resumable staging. Build, preview and validate before adopting; do not rerun population forecasts for a pay-area update.

Each staging script resumes when rerun and never overwrites an adopted release. Run them from this repository (not a worktree), in this order, into a new dated evidence folder:

```powershell
.venv/Scripts/python.exe scripts/stage_statewide_sources.py --base data/pressure_sources/2026-10-20 --out data/pressure_sources/NEW
.venv/Scripts/python.exe scripts/stage_oews_area.py --area 28140 --slug kansascity --out data/pressure_sources/NEW
.venv/Scripts/python.exe scripts/stage_lodes_outbound.py --year 2023 --evidence-out data/pressure_sources/NEW --main-parquet PATH_TO_county_v2_20_main_JT01_2023.parquet
.venv/Scripts/python.exe scripts/stage_qwi.py --out data/pressure_sources/NEW --periods 2024-Q1 ... 2026-Q1
.venv/Scripts/python.exe scripts/stage_cbp.py --out data/pressure_sources/NEW
.venv/Scripts/python.exe scripts/stage_kc_metro.py --base data/pressure_sources/NEW --out data/pressure_sources/NEW-kc-metro
.venv/Scripts/python.exe build_county_pressure.py --evidence-dir data/pressure_sources/NEW-kc-metro
```

Notes for the steps above:

- Save `qwi-version.txt` from https://lehd.ces.census.gov/data/qwi/latest_release/ks/version_qwi.txt before the QWI pull, and replace the copied QWI files rather than adding beside them.
- Builds are statewide by default; `--pilot` builds the old seven-county scope.
- Preview a staged release with `WORKFORCE_PRESSURE_PREVIEW_RELEASE=ID`, then adopt with the `--adopt` flag (or `refresh_dashboard.py --pressure-only DIR --adopt-pressure`, which runs the tests first).
- BLS OEWS bulk files and web pages return 403 to scripts. Read OEWS area definitions in a browser and use the registered BLS API for data.

## Chart presentation

The resident labor-force trend chart has been replaced by **Resident workforce pipeline: 18-year demographic outlook**. The age-18+ view follows ACS resident age/sex cohorts from the 2024 reference baseline through 2042, with fixed age-specific effective civilian participation shares and SSA survival. It includes older workers, annual age-18/65 cohort bars, optional migration/participation scenarios, source details and CSV export. It is separate from age-16+ LAUS observations and the original age-18–64 models. See `docs/demographic-pipeline.md`. The age violin now uses county-calibrated age-specific fertility with Census national schedule changes and Low/Central/High sensitivity paths. Births respond to female cohorts and migration. A 20-year history and separate calendar-period historical comparisons remain visible; these do not establish 18-year accuracy. Its detail expander shows assumptions, proxies and downloads; actual inputs validate for all 105 Kansas counties, with seven counties carrying disclosed state-share proxies. Runtime checks cover four counties, scenario controls and all three affected dashboard modes.

As of 2026-10-21, the default **Workforce Outlook** unifies the existing forecasts with observed county evidence in five steps: outlook, current conditions, occupation/training pathways, training and regional reach, and scenarios. County Brief, Executive Narrative and Full Explorer remain available. The narrative compares model changes over shared years only. Responsive columns stack below 850 pixels.

Forecasts retain their adopted baselines. Resident population, approximate participating residents and exact-match industry employment have separate panels and units. Baseline observations appear as diamonds; model paths are dashed. An optional index compares changes from a common model year without calculating a worker shortage. County employment models disclose older history and demographic proxies; allocated state bounds do not claim to capture allocation uncertainty. Age-range exits replace retirement labels throughout the original presentation.

The occupation selector connects native regional projections and exact available area wages to county institution awards using the official NCES CIP 2020–SOC 2018 crosswalk. It is independent of the industry selector. Related CIPs filter award rows without multiplying them through many-to-many joins. Awards remain credentials, not occupation supply or local placements. See `crosswalks/cip2020_soc2018-source.md` for provenance and verification.

Scenario controls apply a constant participation percentage-point change and additive annual net age-18–64 residents from the first displayed model year. This is an arithmetic sensitivity, not a cohort rerun or a probabilistic forecast. It assumes no additional aging/mortality response; placement and training-retention effects are not invented. Zero assumptions reproduce the adopted baseline exactly.

Validation at the 2026-10-21 Workforce Outlook milestone: 123 automated tests passed. Runtime checks covered Sedgwick, Cowley, Harper and Greeley; manufacturing, health care and construction; welding/electrician pathways; indexed growth; scenario changes; and all original modes. The data release and stored forecast outputs remain unchanged.

The earlier **County Brief** view uses large metric cards, time-series sparklines, county comparison strips and focused charts, following the communication approach at https://skilledtradesreport.com/KS/all. Change County in the sidebar and Industry focus above the section tabs. Workforce, Employment and pay, Training, Movement and Regional outlook expose the detailed charts. Source records remain in expandable panels; Full Explorer retains the existing analytical views.

Industry filters apply only to employment and industry pay. Other views retain their stated universes. Peer strips show the selected county, the unweighted median, and the middle half of counties with published observations in the exact same period and universe. ACS uncertainty and forecast intervals remain distinct. Missing observations never become zero or silently fall back to older values. Plotly chart toolbars support PNG downloads for presentations.

At the earlier County Brief milestone, 101 automated tests passed. The renderer was exercised across four contrasting counties, industry changes, training selections and both old/new modes. Data release and model outputs are unchanged by this presentation update.

## Run and validate

From the dashboard repository:

```powershell
.venv/Scripts/python.exe -m streamlit run dashboard/app.py
.venv/Scripts/python.exe scripts/validate_outputs.py --state 20 --pressure
.venv/Scripts/python.exe -m pytest tests -q --basetemp=.tmp/pytest-new-run
```

The server renders local validated outputs. It does not call agency APIs during page load. `pressure-current.json` is an atomic adopted manifest. `data/pressure_releases/<id>/` contains immutable parquet files and hashes. The app captures one manifest per rerun and includes it in its cache key. Files not overridden resolve to original outputs. Older source files and all original outputs remain unchanged.

## Stage a source update independently of population simulations

Save unmodified verified public source artifacts in a new dated folder under `data/pressure_sources/`. Do not overwrite an adopted release. Live adapter entry points are in `fetch_qwi.py`, `fetch_qcew.py`, `fetch_acs.py`, `fetch_laus.py`, `fetch_oes.py`, `fetch_lodes.py`, and `fetch_ipeds.py`. Source retrieval is explicit and separate from adoption; successful HTTP does not establish a valid response body.

```powershell
.venv/Scripts/python.exe refresh_dashboard.py --pressure-only data/pressure_sources/NEW-kc-metro --dry-run
.venv/Scripts/python.exe refresh_dashboard.py --pressure-only data/pressure_sources/NEW-kc-metro
```

Default pressure refresh runs the unit tests, builds a staged release and leaves the pointer unchanged. Inspect the staged coverage, schemas, hashes and UI preview before adoption. Set `WORKFORCE_PRESSURE_PREVIEW_RELEASE` to the staged ID to preview it; remove the variable to return to the adopted release. A preview reads the staged manifest without re-checking file hashes (adoption does check them), so use it only locally.

```powershell
.venv/Scripts/python.exe refresh_dashboard.py --pressure-only data/pressure_sources/NEW-DATE --adopt-pressure
```

The core command `build_county_pressure.py` provides the same build/adopt mechanism without automatically running tests. Supply `--correction-dir PATH` (or refresh driver's `--pressure-corrections PATH`) only for reviewed sector-model replacement tables. `demand_pressure.recompute_sector_from_cache(outputs, cache_dir)` rebuilds those tables without network calls or population simulation. Validate the impact and attach quality metadata before adopting. Later source-only builds automatically retain already-adopted sector corrections. New annual sector inputs require an explicit reviewed rebuild; quarterly observation refreshes do not silently rerun that model. Each new release contains complete overridden tables, not a dependency chain to older release files.

Ordinary legacy `--sources` refreshes do not adopt new pressure releases. Use `--pressure` validation for adopted tables; the legacy validator intentionally detects old any-region flags in superseded original files.

## Roll back

```powershell
.venv/Scripts/python.exe build_county_pressure.py --rollback VALIDATED_RELEASE_ID
.venv/Scripts/python.exe build_county_pressure.py --rollback legacy
```

Rollback validates the selected retained release before changing the pointer. `legacy` retires the pointer and exposes original files; it also disables pressure panels and hides legacy participation/commuting layers that lack the corrected method/coverage markers. Original flawed values are preserved for audit, not certified by this fallback. Concurrent adoption uses a lock and stale-base check. Never remove an active lock; investigate its owner after an interrupted process.

## Source contract and geographic safeguards

`data_contracts.py` keys observations by source, measure, geography, period and applicable universe dimensions, including QWI seasonal adjustment, firm age and firm size. Published estimates must be finite. Suppressed/unavailable values must be null. ACS margins of error and OEWS RSE remain separate from model prediction intervals. Latest release and latest usable measure can differ.

`geography.py` keeps KDOL projection regions, OEWS pay areas and the metro list separate. For example, Linn is in the Kansas City pay area but the Southeast projection region. Area estimates are selected once at native area grain. `inbound_labor_shed` requires one year and complete main+aux JT01 coverage; it includes the threshold-crossing origin county. Resident outbound publishes only when every destination state was retrieved; states that publish no file for the year are named on each measure, and any download failure leaves outbound unavailable. IPEDS awards reconcile by reporting institution; CIP and award levels are preserved and cannot be treated as unique people.

## Verification and remaining rollout gates

At the 2026-10-20 pilot milestone, 95 tests passed, five-county Streamlit runtime checks and institution/program/industry/shed controls passed, the adopted manifest validated, and all 139 original outputs matched the pre-implementation baseline hashes. Correction impacts and source verification from that milestone are in the maintainer's local project notes, which are not part of this repository.

As of 2026-11-08 the full suite is 180 tests, all passing, and both `validate_outputs.py --state 20` and `--pressure` pass. That day's updates changed some original outputs on purpose (population forecast, IPEDS, JOLTS, KDOL demand flags); each change is recorded in `docs/data-refresh-log.md`.

As of 2026-11-10 the full suite is 209 tests, all passing, and `validate_outputs.py --state 20 --pressure` passes on the adopted release. Runtime checks covered metro and non-metro counties (Johnson, Linn, Wyandotte, Sedgwick, Greeley and others) in Workforce Outlook and County Brief.

As of 2026-11-12 the full suite is 213 tests, all passing. Twelve runtime checks cover Shawnee, Douglas, Riley, Doniphan, Cherokee and Greeley in both County Brief and Workforce Outlook, including exact-occupation pay selection. The adopted release passes output validation. Non-OEWS observations, the other 13 release tables, existing Wichita/Kansas City observations and all 141 original output files are unchanged. Only the occupation-pay extension and its release provenance changed. No hosted publication, commit or push was performed.

Still gated or unavailable:

- Wichita's expanded annual wages are unavailable for six occupation rows because of non-release, hourly-only reporting or a published lower bound. Those rows retain their source marker/explanation rather than an invented point estimate.
- 155 county-sector job cells withheld in every source.
- Alaska and Michigan commuting.
- The newest quarter's QWI separations and earnings.
- Apprenticeship, PSEO and proprietary feeds.

The hosted Streamlit app shows a release only after it is redeployed. Source files should be refreshed and their release identities reverified before future adoption. Do not label a successful cache rebuild as a new agency publication.

Wichita expansion validation, 2026-11-12: 216 tests passed, 16 runtime checks across all four member counties in County Brief and Workforce Outlook, and the adopted output validator passed. Registered Nurses, Aircraft Mechanics and Software Developers were checked, along with censored and hourly-only occupations. The original four occupations' 12 values match exactly. Other areas/observations and 13 other release tables have unchanged values/schema; all 141 original output files retain their hashes.

## Local UI fixes, November 12, 2026

Visual-review V01-V03 are fixed locally: the county map uses an explicit state-boundary extent with Mercator; metric rows reflow with available content width; Executive Narrative has a distinct tab identity from Full Explorer. Local verification: 222 tests, eight AppTest checks, eleven browser checks at five desktop/tablet/phone sizes, and adopted output validation passed. All 105 Kansas map shapes remain visible and county values/hover data are retained. Data release 20261112T193557_923d1ce9 is unchanged. Initial UI commit e989897 was pushed to working/deployment branches. A fresh hosted session exposed a remaining first-open map defect despite correct Reset/resize behavior. The map now mounts only when Population is selected, using state-tracked tabs. Six map tests and eight AppTest checks pass again; local fresh/repeated Population entry displays all 105 counties without Reset. Hosted verification of this correction is pending. Remaining review findings cover dark-theme secondary text, other long titles and occupation/SOC labels. Full evidence is in the maintainer vault's raw/output/kansas-dashboard-visual-fixes-2026-11-12/.

Hosted V01-V03 acceptance completed November 12 on code cf3051a: 15 checks at five desktop/tablet/phone sizes pass. The final county map uses its bundled Plotly renderer in a responsive iframe, mounted only in Population. Fresh desktop and phone sessions open all 105 counties without Reset; hover, Reset and resize work. Cards keep values intact and narrative transitions select the intended tab. Final 222 tests, eight AppTest checks, four tab lifecycle checks and release validation pass. Adopted data release unchanged. V04-V06 and cross-mode county-selection decision U01 remain open. Hosted evidence is retained with the maintainer vault report.
