# Kansas maternal-age birth inputs

Retrieved 2026-10-21. These inputs contain public aggregate statistics only.

## Available data

The [KDHE KIC Birth Statistics query](https://kic.kdhe.ks.gov/birth.php) currently offers births through 2024. The saved inputs contain county and direct statewide observations for calendar years 2006–2024, plus a separate direct query pooling 2020–2024. This is 19 annual observations, not 20. With ACS five-year county exposure estimates available from 2010, a historical matched series begins in 2010.

The [official source notes](https://kic.kdhe.ks.gov/OHA/kicnote.html#Birth) define resident live births during a calendar year regardless of where the birth occurred. These are KDHE birth-history counts. They differ from the Census Population Estimates Program July–June estimated births already saved for the dashboard. Do not substitute one time basis for the other without labeling it.

- `fertility_pooled_births_2020_2024.json`: 954 rows, 105 counties and a direct Kansas record, each with seven maternal-age categories, unknown age, and all-age total. The 190 suppressed county cells remain null. All nine pooled state values are published.
- `fertility_county_births_2006_2024.json`: 18,126 rows, the same 106 geographies and nine categories in each of 19 years.
- `fertility_birth_totals_2006_2024.json`: 2,014 independently published county/state annual all-birth totals, with no suppressed values. These come from a separate public county-by-year query, not subtraction or reconstruction of age cells. Use as aggregate forecast outcomes. The maternal-age table's suppression remains unchanged.
- `fertility_kic_county_*.html` and `fertility_kic_state_*.html`: exact original public responses. Shared preprocess companions were created before reading/parsing.
- `fertility_manifest.json`: source URLs, exact public query parameters, retrieval date, original response SHA256 values, and interpretation notes.
- `fertility_validation.json`: unique keys, status checks, nonnegative counts, published zeros, suppression totals, and reconciliation of completely published tables.

The native KIC bands are 10–14, 15–17, 18–19, 20–24, 25–29, 30–34, and 35+. Output preserves those bands. `age_high=54` for 35+ is an explicit model alignment assumption; `source_age_high=null` preserves the open-ended source definition. Neither 35–39 nor 40–44 nor 45+ was independently observed in this acquisition. Any allocation within 35+ requires a disclosed modeled pattern. If using a Census curve beginning at age14, applying it to the source10–14 total also requires a disclosed tail assumption.

## Schema and handling

`county_fips` is a string: five digits for counties and `20` for the direct state query. Annual rows have `year`; pooled rows have `year=null` and `start_year=2020,end_year=2024`. `period_start` and `period_end` are calendar dates. `measure` separates `births_by_maternal_age`, `births_unknown_age`, and `births_total`. `births` is an integer or null. `status` is `published` or `suppressed`; `raw_value` preserves the original numeric token or `#`.

KIC applies both small-cell and secondary suppression. Its [confidentiality notes](https://kic.kdhe.ks.gov/OHA/kicnote.html) describe suppression for counts below six and additional table disclosure rules. Therefore `#` does not mean a known count between one and five. Published zero is a genuine source zero; suppressed cells are never zero-filled, differenced from totals, or reconstructed. Several direct state annual tables also suppress totals and unknown-age values. State observations come from direct state queries, not sums of partially suppressed county observations.

Validation found 4,439 suppressed annual age-table cells and 190 suppressed pooled cells. All 338 annual and 23 pooled tables whose nine cells are fully published reconcile exactly. No missing value was needed for those checks. The independently published all-birth totals can evaluate an aggregate birth forecast; do not use them to infer hidden maternal-age cells.

The pooled statewide table reports 171,451 live births, of which 15 have unknown maternal age. Unknown age is retained separately. Where a county cell is suppressed, any state-rate fallback belongs to the forecast model, must be labeled as a proxy, and must not replace the source null.

## Exposure and historical evaluation limits

Divide a five-year pooled birth count by five times a matched ACS five-year female population estimate only as an approximate average annual age-specific rate. ACS five-year estimates are period estimates, not measured exact five-year person-years. ACS margins of error and overlapping windows introduce additional uncertainty. For annual backtests, historical birth counts must be paired with exposures and calibration data available at the training cutoff. Using the 2020–2024 county pattern for every past cutoff would leak future information.

No historical female exposure series is acquired by this script. The separate ACS acquisition supplies those inputs. These birth files alone cannot establish a 20-year ASFR backtest or validate a long-horizon fertility forecast.

Rebuild from official endpoints: `.venv/Scripts/python.exe scripts/build_fertility_inputs.py`. Reparse and validate without network: append `--cache-only`. The script invokes the shared vault preprocessing runner and does not alter existing forecast outputs.

## Items Requiring Verification

- [VERIFY] Finer county age categories above35 were not acquired. KIC advertises age drilldowns, but the obtained county-age table has no such link. The official 2024 report download returned HTTP403 from this runtime. Use a verified published table before claiming finer observed county rates.
- [VERIFY] The fraction of source35+ births above54 and source10–14 births below14 is not identified by these native tables. The model must disclose its tail allocation assumptions.
- [VERIFY] Comparable annual female person-year exposures and the historical data vintages needed for a real-time backtest are not established by the birth acquisition. Historical ACS five-year releases can support an explicitly approximate retrospective comparison after their availability and cutoff handling are checked.
