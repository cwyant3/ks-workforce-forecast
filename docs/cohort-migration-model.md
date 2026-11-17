# Cohort model: migration estimate and model ranges

Revised 2026-11-17 on branch `fix/cohort-migration-uncertainty`. Code: `cohort_model.py`
(`fit_migration_model`, `CountyCohortModel.project`). Check: `scripts/backtest_cohort_migration.py`.
The Kansas and Missouri forecast tables were regenerated on this branch for review, and the
Kansas county sector table was rebuilt on the revised population. Preview release
`20261117T221214_8929b5f9` holds the dependent Kansas tables and supersedes
`20261117T214752_1e1cf231`. Nothing is adopted; that is a separate release decision. The app's migration and range text follows the forecast on file
(`pooled_migration` in `dashboard/app.py`), so it describes whichever model built it.

An independent review on 2026-11-17 re-derived the estimator, reproduced the figures below
and found no arithmetic or indexing defect. Its findings on sensitivity are in the last two
sections and should be read before the county figures are relied on.

## What was wrong

A review on 2026-11-17 found four defects in the 2026–2035 working-age forecast.

1. **The documented estimator never ran.** `run_all_counties` passed each county's history
   without its age cohorts, so every county fell back to total working-age growth plus a
   flat 0.3%. The stored rates matched that fallback exactly. Aging was then applied again in
   the projection.
2. **One observation per county.** Only neighbouring ACS vintages were compared, so with
   2015, 2019, 2021 and 2024 on file, only 2015→2019 passed the overlap rule. 2019→2024,
   which does not overlap at all, was never tried.
3. **The spread was a constant.** With one observation there was no spread to measure, so
   all 105 counties used the 0.5% floor. The long-run rate itself was treated as known.
4. **Counties were simulated independently**, which made the state range about ±0.8% in 2035.

Backtest of the old model, 2019 base to 2024, 105 Kansas counties: median county error 4.6%;
the 80% band held the 2024 estimate in 21% of counties (22 of 105). Aging with no migration
at all had a median error of 2.3%.

## The estimator now

**Residual rate.** For each pair of non-overlapping ACS 5-year vintages, age the county's
earlier age structure forward with no migration and compare the expected working-age
population with the later estimate. The annualized gap is the residual rate. The chain of
vintages ends at the latest one and walks back, skipping any vintage that overlaps the next
by more than one year: 2015 → 2019 → 2024 for the current data.

**Pooling.** A county's residual mixes a lasting local pattern, a one-off period effect and
ACS sampling error. All counties in the state are fitted together, in annual rates:

    rate(county i, period k) = state(k) + county(i) + transient(i, k) + sampling error

- `state(k)` is the population-weighted mean rate in period k.
- `county(i)` persists; its variance across counties is `tau2`.
- `transient(i, k)` does not persist; its variance is `omega2`.
- Sampling error has variance `kappa / N_i` per vintage, where `N_i` is the county's
  working-age population. Neighbouring periods share a vintage, so the same error enters
  them with opposite signs; the fit accounts for that.

The three variances are fitted by Gaussian maximum likelihood. The forecast rate is the
statewide mean plus the best linear unbiased prediction of `county(i)`.

Kansas fit (ACS 2015, 2019, 2024; 105 counties):

| Quantity | Value |
|---|---|
| Statewide rate by period | +0.01%, +0.27% per year |
| Statewide rate used | +0.16% per year |
| Persistent county sd (`tau`) | 0.68 points |
| Transient period sd (`omega`) | 0.41 points |
| Annual shock sd | 0.69 points |
| Share of own history kept | 0.39 to 0.84 |

The largest counties keep about 85% of their own rate. Seven counties keep less than half;
the median for the smaller half of counties is 0.58. Morton County, the old extreme: own
residual −2.42% a year, used −1.27% (old model −4.93%).

## The simulated range now

Each of 2,000 simulations combines four sources.

1. **The county's long-run rate**, drawn once per simulation from its estimate and
   standard error, and held for the whole horizon.
2. **A statewide shift** drawn once per simulation and shared by every county, sized from
   the spread between historical periods. County simulations therefore add up to a state
   range with correlated migration.
3. **Annual shocks**, AR(1) with φ = 0.3, sized so that their multi-year average matches
   the fitted transient variance.
4. **Baseline sampling error.** The ACS baseline is a survey estimate; each simulation
   scales the county by a draw from its sampling error. The median is unchanged.

## Backtest

Fit on vintages through 2019, forecast 2024, compare with the 2020–2024 ACS.
"Like for like" adds the 2024 estimate's own sampling error to the forecast; the band a
user sees does not include it, so "80% holds" is the figure that describes the display.

| State | Median error | 80% holds | Like for like |
|---|---|---|---|
| Kansas | 2.8% | 75% | 79% |
| Colorado | 4.5% | 69% | 73% |
| Missouri | 3.4% | 59% | 59% |
| Nebraska | 3.1% | 62% | 73% |
| Oklahoma | 3.5% | 65% | 71% |

Across all 454 counties the 80% range held 66%.

Reading it:

- Kansas county error fell from 4.6% to 2.8% and 80% coverage rose from 21% to 75%.
- **The backtest does not test the production settings.** With history through 2019 there
  is one period, so it runs the assumed defaults listed below. The production fit has two
  periods and is narrower: statewide shift sd 0.23 against 0.35, annual shock sd 0.69
  against 0.86. Applying the production spreads to the same window gives about 68% coverage
  in Kansas, and that figure is flattering because those spreads were estimated with the
  2024 data.
- Coverage is largely set by the two assumed defaults. Varying them over their plausible
  ranges moves Kansas 80% coverage between 45% and 82%.
- In Kansas the model was 0.4 points less accurate than aging alone (2.3%). County
  migration history did not help there in this window. Elsewhere the picture is mixed:

  | State | Aging only | Model | Raw county rate |
  |---|---|---|---|
  | Colorado | 5.0% | 4.6% | 3.5% |
  | Missouri | 3.7% | 3.3% | 3.4% |
  | Nebraska | 3.7% | 3.3% | 3.4% |
  | Oklahoma | 3.4% | 3.6% | 3.4% |

- This is one five-year window that spans the pandemic and the 2020 Census re-base.

## Effect on the Kansas forecast

Same inputs (ACS plus the cached single-grade youth update), same seed, 2,000 draws.
Running the previous model on these inputs reproduces the adopted forecast exactly.
Regenerated on this branch; not adopted.

| Measure | Adopted | Revised |
|---|---|---|
| Kansas 2035 median | 1,724,635 | 1,731,277 |
| Kansas 2035, P10–P90 | 1.711M–1.739M | 1.673M–1.788M |
| Counties declining by 2035 | 92 | 91 |
| Counties down more than 10% | 62 | 18 |
| Median county 80% half-width | 2.5% | 7.0% |
| Largest county decline | −45.1% | −22.6% |
| Wichita MSA 2035 median | 386,927 | 386,053 |

## Assumptions that are not estimated

- φ = 0.3 for annual shocks.
- With one historical period: half of the non-sampling spread persists
  (`DEFAULT_PERSISTENT_SHARE`), and the statewide rate varies by 0.25 points
  (`DEFAULT_STATE_SD`).
- The statewide spread rests on two periods, which is one degree of freedom, and is treated
  as normal and held for the whole horizon. It supplies about 79% of the variance of the
  2035 state total. An 80% confidence interval for that spread runs from roughly 0.6 to
  8 times the estimate, so the state range is indicative only.
- Sampling error scales as `1 / N`. The fitted Kansas value implies a typical baseline CV
  about 0.6 times the root-sum-of-squares of the published age-band margins, which
  overstate the margin of a controlled total. (The review did not check this figure.)
- Mortality, the aging step and participation carry no uncertainty.
- The residual is applied only to ages 18–64, in proportion to each cohort.

## Sensitivity found in the independent review

County rates for the two periods are essentially uncorrelated in the raw data (−0.02), so
the persistent spread of 0.68 points is inferred from the model's structure, not seen
directly. Two structural choices drive it. Both were tested against alternatives.

**How size-related noise behaves.** The model treats all noise that scales with county
size as survey error that reverses in the next period. An alternative in which that noise
does not reverse (as real small-county volatility would not) fits the Kansas data somewhat
better. Under it, the persistent sd is 0.37–0.52 and small counties keep about half as
much of their own history:

| County | Rate as coded | Rate, alternative |
|---|---|---|
| Morton | −1.27% | −0.56% |
| Lane | −1.16% | −0.29% |

So "largest decline −22.6%" and "18 counties down more than 10%" depend on this choice.
Two periods cannot settle it.

**What the counties are pulled toward.** The statewide rate is population-weighted, and
Johnson and Sedgwick are 38% of the weight. Most counties sit below it (77% in the first
period). Pulling toward the typical county instead fits better in-sample, lowers the
persistent sd to about 0.54, and lowers small-county rates by 0.16 points a year on
average, about 1.8% on a 2035 level and 1.2% on the state total. In the one out-of-sample
window, however, the population-weighted rate was the closer guide to what small counties
then did. A mean that varies with county size is the obvious refinement.

**Stability.** A bootstrap over counties gives 95% ranges of 0.52–0.82 for the persistent
sd, 0.08–0.58 for the transient sd and 0.97–2.6 for `kappa`. Forecast rates are stable for
the largest counties (Johnson +0.66 to +0.88, Sedgwick +0.23 to +0.26) and not for small
ones (Morton −1.57 to −0.89). Residuals are heavy-tailed and the extremes cluster in
southwest Kansas, which a single statewide shock does not capture. In other states the
two-period fit is fragile: Missouri's transient sd fits at 0.05 points.

## Limits

- The ranges are model ranges conditional on the above, not validated prediction intervals.
- Two periods identify the variance split weakly; it will move as vintages are added.
- The 2019 → 2024 residual spans the 2020 Census re-base. A model with extra noise in the
  2024 vintage fits better, consistent with that.
- Baseline error and rate error are drawn independently; under the model they are
  negatively related, which understates the combined spread by 7–16% at eleven years for
  small and mid-size counties.
- A rate applied only to residents already aged 18–64 reads back as about 0.95 of itself
  over five years, so residuals are slightly attenuated.
- Not simulated: uncertainty in the fitted variances and φ, sampling error in the youth
  cohorts, and regional correlation.
- County age cohorts of small counties carry large sampling error that the aging step
  passes through; only the working-age total's error is modeled.

## Suggested next refinements

1. A size-dependent mean in place of the population-weighted statewide rate.
2. Separate reversing survey error from non-reversing small-county volatility, using the
   published ACS margins to fix the survey part.
3. Refit when the next non-overlapping vintage (2025–2029) arrives; until then the
   persistent share is weakly identified.
