# County workforce pressure pilot

Local release: `20261109T034237_d5325ec1`, adopted 2026-11-08 (supersedes `20261020T215651_b4a47fdc`; it carries the KSDE single-grade youth update in `projections_effective_s20` and IPEDS 2015-2024 history rebuilt without CIP 99 double counting; see `docs/data-refresh-log.md`). This is a seven-county South Central demand pilot, with broader ACS, institution and inbound commuting coverage. It is not a statewide shortage score. On 2026-11-08 the maintainer approved committing it and deploying it to the project's Streamlit Community Cloud app.

## Statewide staging (2026-11-10)

A statewide release, `20261110T170437_16937bcf`, was adopted on 2026-11-10 (previous: `20261109T034237_d5325ec1`, rollback target). It is local until committed and deployed. It covers all 105 counties for QCEW, QWI, LAUS, LODES and IPEDS, and the Outlook and Brief pick each county's KDOL projection region from `crosswalks/county_areas_20261020.csv`. Occupation pay stays Wichita-only. Rebuild with `scripts/stage_statewide_sources.py` then `build_county_pressure.py --evidence-dir data/pressure_sources/2026-11-10 --statewide`; preview locally with `WORKFORCE_PRESSURE_PREVIEW_RELEASE`.

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
.venv/Scripts/python.exe refresh_dashboard.py --pressure-only data/pressure_sources/2026-10-20 --dry-run
.venv/Scripts/python.exe refresh_dashboard.py --pressure-only data/pressure_sources/2026-10-20
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

`geography.py` keeps South Central's seven counties distinct from Wichita OEWS's four. Area estimates are selected once at native area grain. `inbound_labor_shed` requires one year and complete main+aux JT01 coverage; it includes the threshold-crossing origin county. Outbound totals stay unavailable without complete destinations. IPEDS awards reconcile by reporting institution; CIP and award levels are preserved and cannot be treated as unique people.

## Verification and remaining rollout gates

At the 2026-10-20 pilot milestone, 95 tests passed, five-county Streamlit runtime checks and institution/program/industry/shed controls passed, the adopted manifest validated, and all 139 original outputs matched the pre-implementation baseline hashes. Correction impacts and source verification from that milestone are in the maintainer's local project notes, which are not part of this repository.

As of 2026-11-08 the full suite is 180 tests, all passing, and both `validate_outputs.py --state 20` and `--pressure` pass. That day's updates changed some original outputs on purpose (population forecast, IPEDS, JOLTS, KDOL demand flags); each change is recorded in `docs/data-refresh-log.md`.

Missing QWI historical quarters, complete outbound commuting, nonmetropolitan OEWS, expanded demand coverage, apprenticeship and proprietary feeds remain gated. Hosted deployment was approved on 2026-11-08. Source files should be refreshed and their release identities reverified before future adoption. Do not label a successful cache rebuild as a new agency publication.
