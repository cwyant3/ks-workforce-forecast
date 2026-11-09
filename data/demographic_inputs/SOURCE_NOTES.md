# Demographic source inputs

These public aggregate inputs support a separate demographic model. They do not overwrite the dashboard's adopted observations or forecasts.

## Files and schemas

- `acs2024_5yr_B01001_response.json`: original Census JSON array. First row contains column names; next 105 rows contain Kansas county records. Full age-sex estimates, margins of error, and annotations are preserved.
- `acs2024_5yr_B23001_response.json`: same structure for age-sex employment status, all 105 Kansas counties.
- `acs2024_5yr_B23001_state_response.json`: header plus the Kansas statewide record, for an explicitly labeled fallback when a county denominator is zero.
- `acs2024_5yr_B01001_labels.json` and `acs2024_5yr_B23001_labels.json`: complete official variable dictionaries. Labels, rather than positional assumptions, govern normalization.
- `population_age_sex_bands.json`: 4,830 records. Fields include `county_fips` (five-digit string), `county_name`, `sex`, `age_start`, `age_end` (null means open-ended), `age_label`, `estimate`, `moe90`, `availability_status`, `source_variable`, `source_label`, original value strings, and ACS reference period.
- `labor_force_age_sex_bands.json`: 7,560 records with the same identity fields and `measure` equal to `population`, `civilian_labor_force`, or `armed_forces`. This is a long table, not a rate table.
- `survival_age_sex.json`: 120 records with `age` 0–119 and `male_qx`, `female_qx`, `male_lx`, `female_lx`. Published probabilities and rounded survivor counts are preserved.
- `ssa2023_web_response.json`: the complete exact text returned by the official-source web retrieval, with retrieval time and source URL. Direct SSA HTML downloads returned HTTP 403. This file is not represented as a downloaded HTML response.
- `source_manifest.json`: credential-free source URLs, retrieval times and SHA-256 hashes of source files. SSA also records the hash of its original web-tool response text and the acquisition limitation.
- `validation.json`: reconciliation, coverage, missingness and normalized-file hashes.

The raw Census responses are byte-exact HTTP response bodies. Census API keys are neither saved in source URLs nor copied into the bundle.

## Source and modeling distinctions

The [Census B01001 dictionary](https://api.census.gov/data/2024/acs/acs5/groups/B01001.html) describes population by sex and age. The [B23001 dictionary](https://api.census.gov/data/2024/acs/acs5/groups/B23001.html) describes employment status for ages 16 and over. These are ACS 2020–2024 period estimates, not a point-in-time count on December 31, 2024. Treating the ending year as a cohort-model baseline is a modeling convention.

For younger B23001 age bands, civilian labor force is a child of the total labor-force branch. Older bands omit the Armed Forces branch and report labor force directly. Dividing civilian labor force by the full age-sex population produces an effective civilian share compatible with a total-resident projection. It must not be presented as the official civilian noninstitutional labor-force participation rate. B23001's 16–19 band cannot directly identify ages 18–19 without an allocation assumption. Eight county age-sex population denominators are zero in this extract; statewide fallback is a model choice that must remain visible.

The [SSA actuarial table](https://www.ssa.gov/oact/STATS/table4c6.html) is the 2023 period table used in the 2026 Trustees Report for the Social Security area population. It does not provide Kansas county-specific survival or future mortality improvements. Holding its rates constant is a model assumption. Published survivor counts are rounded and become zero at extreme ages. Published death probability at age 119 is 0.926604, not 1. A model that truncates or closes the oldest age needs its own explicit terminal-age convention.

Uniform allocation within ACS age bands and use of SSA survivor proportions for the open-ended 85+ band are model assumptions, not observed single-age county counts. No single-age allocation is performed by this builder. Margins of error are preserved per source variable and are not automatically propagated into a forecast interval.

## Reproduction

From the project directory, run the source builder to retrieve current copies of the fixed 2024 ACS product. The default SSA HTML route requires preprocessing through the vault's shared `preprocess.py` before parsing. When that route is unavailable, the preserved official web response can be reused:

```powershell
& .venv/Scripts/python.exe scripts/build_demographic_inputs.py --ssa-web-response data/demographic_inputs/ssa2023_web_response.json
```

Offline normalization and validation only:

```powershell
& .venv/Scripts/python.exe scripts/build_demographic_inputs.py --normalize-only --ssa-web-response data/demographic_inputs/ssa2023_web_response.json
```

The project supports a simple local `CENSUS_API_KEY=value` assignment or the process environment. Optional matching quotes are removed; configuration is never evaluated as shell code. No dependency installation is required.

## Items Requiring Verification

- Resolved 2026-11-08 — SSA values verified. Scripted SSA HTML requests still return HTTP 403, so the parsed table came from the preserved web-tool response. On 2026-11-08 the SSA page was rendered in a desktop browser and its table read directly: all 120 ages of male and female death probability and number of lives match `survival_age_sex.json` exactly. That browser copy is preserved as `ssa2023_period_life_table_verified.json`. Byte-exact HTML is still not archived.
- Model calibration, local mortality suitability, single-age allocation, static participation, oldest-age handling, and statewide fallback accuracy are not established by source validation. They require separately documented model assumptions and sensitivity checks.
