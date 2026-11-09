# Superseded dashboard view

The statistical chart described below was replaced on 2026-10-21 by the age-cohort view documented in `demographic-pipeline.md`. The statistical model remains for historical tests; the user-facing resident outlook no longer renders it.

# Resident labor-force forecast

Implemented 2026-10-21 at the user's request for a dotted ten-year projection and 80% prediction interval.

## Scope and display

The independent annual LAUS model covers the civilian resident labor force age 16+, not the approximate age 18–64 population/participation model. It appears below current conditions in Workforce Outlook, in County Brief's Workforce tab, and in Available Workforce in the original views. The last monthly observation is an isolated diamond; it never becomes an annual baseline or silently changes the fit.

Annual history is 2015–2025 from the existing BLS LAUS output. County/year/labor-force columns were verified equal to the source cache across the full state. Each series requires consecutive, positive, finite annual values and a distinct record per year. Missing or stale history withholds the forecast. No stored model outputs or adopted source observations are overwritten.

The display extends through the current calendar year plus 10, currently 2036. The annual 2026 projection is followed by 10 future calendar years; it is 11 steps from the latest completed 2025 annual baseline. Annual averages are placed at year-end for plotting. The 2026 monthly observation is separate and is not used as an annual-model nowcast input.

## Method

Let x be log annual labor-force counts, d the mean of n observed annual log changes, and s their sample standard deviation (ddof 1). For horizon h, the log median is x_last+h*d. The predictive half-width is t(0.9,n-1)*s*sqrt(h+h*h/n). Exponentiating the lower/upper log bounds produces the nominal 80% pointwise prediction interval. The exponentiated location is a median, not an expected count.

This exploratory model assumes independent normally distributed annual log changes with constant drift and variance. Bounds include future increment variability and drift/variance estimation uncertainty. They exclude source-estimate/revision uncertainty and unmodeled future structural changes. They are not simultaneous trajectory bounds, an official BLS projection, or a demographic cohort rerun. No prediction interval is shown for zero estimated variation.

Primary methodology reference: [Forecasting: Principles and Practice, prediction intervals](https://otexts.com/fpp2/prediction-intervals.html). Source universe: [BLS LAUS concepts](https://www.bls.gov/opub/hom/lau/concepts.htm). Checked 2026-10-21. The Student-t extension uses finite-sample normal-increment prediction theory with n-1 variance degrees of freedom.

## Validation

133 automated checks passed, including numeric interval calculations, widening widths, shared county codes across states, missing/duplicate/invalid years, stale/short/constant histories and monthly/annual separation. All 105 Kansas inputs forecast successfully through 2036. Runtime checks covered Sedgwick, Harper and Greeley and all relevant modes. Rolling checks use only earlier training observations and report one-,three-,five- and ten-year sample counts, errors, interval coverage/width, and a no-drift point benchmark. With 11 annual observations and a six-year minimum retrospective fit, this gives 5/3/1/0 forecasts respectively. These small samples do not establish calibration.

## Items Requiring Verification

- [VERIFY] Ten-year empirical prediction-interval coverage is unvalidated. Longer consistent annual history and rolling-origin evaluation are required before treating nominal 80% as established coverage.
- [VERIFY] Future structural change and source revisions are not modeled; planning decisions require updated data and employer/regional evidence.
