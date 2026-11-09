# Twenty complete annual county birth intervals

`births_county_2006_2025.json` contains 2,100 observations: 105 Kansas counties for every year from 2006 through 2025. Each year means July 1 of the preceding year through June 30 of the named year. No partial April–June interval is included.

The source segments are:

| Annual intervals | Source vintage | Official source and layout |
|---|---|---|
| 2006–2010 | 2010 evaluation estimates | [Current corrected CSV](https://www2.census.gov/programs-surveys/popest/datasets/2010/2010-eval-estimates/co-est2010-alldata.csv), [layout](https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2000-2010/co-est2010-alldata.pdf) |
| 2011–2020 | 2020 estimates | [CSV](https://www2.census.gov/programs-surveys/popest/datasets/2010-2020/counties/totals/co-est2020-alldata.csv), [layout](https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2010-2020/co-est2020-alldata.pdf) |
| 2021–2025 | 2025 estimates | [CSV](https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv), [layout](https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2020-2025/CO-EST2025-ALLDATA.pdf) |

The [Census correction notice](https://www.census.gov/programs-surveys/popest/technical-documentation/research/evaluation-estimates.2010.html) states that the original 2010 components file had an error for the 2009–2010 interval and was replaced on April 6, 2012. This bundle uses the current linked archive. The 2010 source is an evaluation series, not an intercensal series.

## Counts and rates

Every row retains `county_fips`, `county_name`, `year`, `births`, interval dates, `vintage`, source variable, source URL, series type and availability. Birth values are Census estimated resident births, not raw birth-registry records.

`population_prior_july1` and `population_current_july1` are from the same vintage as the birth count. `population_exposure` is their arithmetic mean. `rate_per_1000` is 1,000 times births divided by this exposure. `published_rate_per_1000` preserves Census's `RBIRTH{year}` value. All 2,100 derived rates match the published rates within rounding; the largest difference is less than 0.000000006 births per 1,000 residents. These are crude birth rates for the total resident population, not fertility rates for women of reproductive age.

The bundle has no missing birth counts, missing exposures, negative counts, zero counts, duplicate county-years or annual gaps. Kansas county birth estimates sum to 39,841 in the 2006 interval and 34,416 in the 2025 interval. All annual sums are recorded in `births_manifest_20yr.json`.

## Comparability and provenance

This combines three vintages, not one harmonized intercensal series. Rows mark the source transitions at 2011 and 2021 with `vintage_boundary=true`. Revised population bases and estimation methods can introduce discontinuities, especially in denominators. A model should assess these boundaries rather than interpret every change as a behavioral shift. Backtests using these revised files are retrospective, not evaluations of the data available at each historical forecast date.

Original CSV/PDF bytes, URLs, retrieval timestamps and SHA-256 hashes are retained in `births_manifest_20yr.json`. Shared preprocessing ran before scoped parsing. Reversible UTF8 copies supported CSV preprocessing; normalization streams the original files and selects only Kansas county rows. Shared page-preserving PDF companions verify both the birth intervals and July 1 population dates. The original five-year bundle is unchanged.

Rebuild the expanded inputs with:

```powershell
& .venv/Scripts/python.exe scripts/build_birth_inputs.py --history20 --download-only
```

Run the vault's shared `preprocess.py` on each newly downloaded UTF8 CSV and PDF, then:

```powershell
& .venv/Scripts/python.exe scripts/build_birth_inputs.py --normalize-only --history20 --companion-dir ../.cache/extracted
```

## Items Requiring Verification

No unresolved source-format, period-definition or county-coverage issues remain. Whether a twenty-year model predicts better than a recent-window model requires backtesting. Converting future birth rates to future birth counts requires an explicit future population denominator. Vintage-boundary sensitivity and long-term probability calibration remain model-review tasks.
