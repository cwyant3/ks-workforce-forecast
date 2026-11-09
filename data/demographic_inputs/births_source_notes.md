# Kansas county birth inputs

The [official Census Vintage 2025 county file](https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv) supplies five complete annual birth estimates for 2021–2025. The [file layout](https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2020-2025/CO-EST2025-ALLDATA.pdf) identifies the release as March 2026 and defines the intervals. These are Census estimated resident births, not unprocessed birth-registry records or births occurring at county hospitals.

`births_county_2021_2025.json` contains 525 records, one per Kansas county and year. Fields are `county_fips` (five-digit string), `county_name`, `year`, integer `births`, `period_start`, `period_end`, `source_variable`, `vintage`, `availability_status`, and `unit`. All 105 counties have all five estimates. There are no missing, negative, zero, or duplicate county-year values.

Each year labels the July–June interval ending in that year. Thus 2025 covers July 1, 2024 through June 30, 2025. `BIRTHS2020` covers only April–June 2020 and is deliberately excluded. All observations share Vintage 2025; a future update should replace the whole history with one consistent vintage.

Kansas county sums are 34,178, 34,404, 34,681, 33,957, and 34,416 for the five intervals, respectively. These are sums of the extracted county estimates, not an independently downloaded state series.

`births_manifest.json` records source URLs, retrieval timestamps, original-response hashes, normalized-file hash, period definitions, selection method and validation. Original CSV and PDF files remain intact. The shared preprocessor could not decode the original CSV's Latin-1 names. A reversible UTF8-BOM copy was preprocessed successfully; its exact reverse conversion is checked against the original bytes. Extraction then streamed the original CSV, selecting only `STATE=20`, `SUMLEV=050` and the five requested birth columns. The truncated Markdown companion was not used as the dataset.

Reproduce downloads with `scripts/build_birth_inputs.py --download-only`. Run the vault's shared `preprocess.py` on `births_co-est2025-alldata.utf8.csv` and `births_co-est2025-layout.pdf`, then normalize with:

```powershell
& .venv/Scripts/python.exe scripts/build_birth_inputs.py --normalize-only --csv-companion ../.cache/extracted/births_co-est2025-alldata.utf8.csv.md --layout-companion ../.cache/extracted/births_co-est2025-layout.pdf.md
```

This builder produces source inputs only. It does not create forecast distributions or change existing dashboard outputs.

## Items Requiring Verification

No unresolved source-format or county-coverage issues remain. Five annual observations alone do not establish a validated long-term birth forecast or probability distribution; forecast assumptions and uncertainty require separate model review.
