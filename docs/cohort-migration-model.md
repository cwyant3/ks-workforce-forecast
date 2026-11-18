# Cohort model: migration estimate and model ranges

Revised 2026-11-17 on branch `fix/cohort-migration-uncertainty`. Code: `cohort_model.py`
(`fit_migration_model`, `CountyCohortModel.project`). Check: `scripts/backtest_cohort_migration.py`.
The forecast tables for Kansas, Missouri, Colorado, Nebraska and Oklahoma were regenerated on
this branch for review, and the Kansas county sector table was rebuilt on the revised
population. Preview release
`20261117T221214_8929b5f9` holds the dependent Kansas tables and supersedes
`20261117T214752_1e1cf231`. Nothing is adopted; that is a separate release decision. The app's migration and range text follows the forecast on file
(`pooled_migration` in `dashboard/app.py`), so it describes whichever model built it.

**Update 2026-11-18.** Colorado, Missouri, Nebraska and Oklahoma now run a refined
estimator with a third historical period; see "Refined estimator for the four neighbouring
states" at the end. Kansas is unchanged and everything above that section still describes
it. Branch `fix/neighbour-state-caveats`. Chris's instruction on 2026-11-18 was to publish
the four states to `main` and `master`; he gave it knowing the work had no independent
review, that Missouri's backtest coverage fell from 59% to 55%, and that the Kansas City
metro panel in the Kansas release still holds the earlier Missouri forecast.

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

1. A size-dependent mean in place of the population-weighted statewide rate. *Done for the
   four neighbouring states on 2026-11-18, as a tilt around the statewide rate.*
2. Separate reversing survey error from non-reversing small-county volatility, using the
   published ACS margins to fix the survey part. *Done for the same four states.*
3. Refit when the next non-overlapping vintage (2025–2029) arrives; until then the
   persistent share is weakly identified. *The 2005–2009 vintage was added for the four
   states instead, which gives a third period now.*

## Refined estimator for the four neighbouring states (2026-11-18)

Applies to Colorado (08), Missouri (29), Nebraska (31) and Oklahoma (40), listed in
`forecast_settings.py`. Code: `cohort_model.fit_refined_migration_model`,
`fetch_acs.working_age_survey_cv`, `fetch_ksde._fetch_full_virtual_schools`. Tests:
`tests/test_refined_forecast.py`. Kansas is not in the list: its forecast sits behind
adopted release `20261117T221214_8929b5f9`, its tables are byte-identical, and what the
same settings would do to it is recorded below. Chris decided on 2026-11-18 to leave
Kansas as adopted.

### What changed

1. **A third period.** The 2005–2009 ACS vintage is added, so the chain is
   2009 → 2015 → 2019 → 2024 (spans of 6, 4 and 5 years). The statewide spread now rests
   on three periods, and the backtest from 2019 fits two periods instead of running
   assumed defaults.
2. **Survey error is read, not fitted.** Each county-vintage's sampling variance comes
   from the published 90% margins. The ACS controls a county's total population, so the
   working-age count varies only through the age split: with `w` the summed squared
   standard errors of the working-age cells and `c` that of the other cells, the variance
   used is `w·c / (w + c)`. Cells the Census Bureau reports as controlled count as exact.
   The fitted value had been zero in Missouri and about four times Kansas's in Colorado.
3. **Small counties are more volatile, and it does not reverse.** The transient variance
   is `omega2 + nu / N`. Annual shocks are therefore larger in smaller counties.
4. **The mean tilts with county size.** Counties are pulled toward
   `state(k) + slope · z`, where `z` is log working-age population centred at its
   population-weighted mean. The population-weighted mean of that pattern is still the
   statewide rate, so the tilt moves counties relative to each other and leaves the state
   total anchored. A freely fitted line was tried and rejected: it put Missouri's forecast
   rate above every period in its history (St. Louis County +0.11% a year observed,
   +0.70% used).
5. **Youth update.** Colorado, Nebraska and Oklahoma now get the school-enrollment update
   Kansas and Missouri had, and all four states leave out schools NCES classes as fully
   virtual, because a statewide online school is counted in its district office's county.

### Fit

Points per year unless noted. "Kept" is the share of a county's own rate that is used.

| Quantity | CO | MO | NE | OK |
|---|---|---|---|---|
| Statewide rate, 2009–15 | +1.33 | +0.37 | +0.71 | +0.97 |
| Statewide rate, 2015–19 | +1.72 | +0.29 | +0.41 | +0.46 |
| Statewide rate, 2019–24 | +1.21 | +0.48 | +0.58 | +0.54 |
| Statewide rate used | +1.39 | +0.38 | +0.59 | +0.69 |
| Statewide sd | 0.31 | 0.11 | 0.17 | 0.32 |
| Persistent county sd | 0.50 | 0.38 | 0.30 | 0.59 |
| Transient sd, large county | 0.42 | 0.70 | 0.30 | 0.43 |
| Size volatility `nu` | 0.715 | 0.047 | 0.100 | 0.254 |
| Size slope per log unit | 0.193 | 0.147 | 0.201 | 0.306 |
| Annual shock sd, median | 1.72 | 1.28 | 1.08 | 1.10 |
| Annual shock sd, largest | 6.93 | 1.68 | 3.86 | 2.64 |
| Kept, median county | 0.42 | 0.43 | 0.35 | 0.70 |
| Kept, largest | 0.79 | 0.47 | 0.74 | 0.85 |

Counties keep less of their own history than before (medians were 0.71 to 0.91). With
three periods the fit finds less of the county-to-county spread persisting, and the size
tilt explains part of what looked like persistence.

### Backtest, before and after

"Before" is the 2026-11-17 estimator on the tables then stored. "80% holds" is the share
of counties whose later ACS estimate fell inside the displayed 80% range; "like for like"
adds that estimate's own sampling error.

From the 2015–2019 ACS to the 2020–2024 ACS:

| State | Error before | after | 80% holds before | after |
|---|---|---|---|---|
| Colorado | 4.5% | 3.5% | 69% | 77% |
| Missouri | 3.4% | 3.3% | 59% | 55% |
| Nebraska | 3.1% | 3.3% | 62% | 69% |
| Oklahoma | 3.5% | 3.5% | 65% | 69% |

Like for like, after: Colorado 80%, Missouri 57%, Nebraska 76%, Oklahoma 71%. Colorado's
2024 state total now falls inside its range; the other three already did.

From the 2011–2015 ACS to the 2015–2019 ACS, which the added vintage makes possible
("before" here is the 2026-11-17 estimator given the same added vintage):

| State | Error before | after | 80% holds before | after |
|---|---|---|---|---|
| Colorado | 3.8% | 3.4% | 86% | 70% |
| Missouri | 1.8% | 1.8% | 90% | 91% |
| Nebraska | 2.9% | 1.9% | 87% | 87% |
| Oklahoma | 3.7% | 2.6% | 87% | 92% |

Reading it:

- **The window matters more than the estimator.** Under either estimator the 80% range
  held 55–77% of counties across the 2020 Census re-base and 70–92% in the window without
  one. Averaged over both windows the refined ranges hold 73–80%.
- **The change bought accuracy more reliably than coverage.** Median county error fell or
  held in seven of eight state-windows (Nebraska 2019–2024 rose 0.15 points).
- **Missouri is not fixed.** Its 2019–2024 misses are concentrated in mid-size Ozark and
  Bootheel counties whose 2020–2024 estimates came in 10–19% below the forecast (Ripley,
  DeKalb, Oregon, Wayne, Mississippi). That pattern is what a census re-base correcting a
  decade of drift in the population estimates would produce, but the cause was not
  verified here.
- **Colorado's ranges were too narrow in the calmer window** (70%), where the earlier
  estimator held 86%. Its fit moves the most between periods.
- Two windows are still two windows. Nothing here is a validated prediction interval.

### Effect on the forecasts

Same seed (20260424), 2,000 draws, 2026–2035. Each step was run alone and the last step
reproduces the stored tables exactly (maximum difference 0).

| State | 2035 median before | after | Change before | after |
|---|---|---|---|---|
| Colorado | 4,088,200 | 4,060,415 | +10.00% | +9.25% |
| Missouri | 3,660,085 | 3,635,587 | −1.12% | −1.78% |
| Nebraska | 1,211,625 | 1,220,195 | +4.17% | +4.90% |
| Oklahoma | 2,495,283 | 2,535,106 | +3.71% | +5.37% |

| State | State 80% range before | after |
|---|---|---|
| Colorado | 3.871M–4.332M | 3.896M–4.229M |
| Missouri | 3.582M–3.736M | 3.555M–3.719M |
| Nebraska | 1.188M–1.236M | 1.190M–1.253M |
| Oklahoma | 2.456M–2.534M | 2.426M–2.643M |

Median county 80% half-width in 2035, and counties down more than 10%:

| State | Half-width before | after | Down 10% before | after |
|---|---|---|---|---|
| Colorado | 8.9% | 11.2% | 2 | 14 |
| Missouri | 4.8% | 7.8% | 30 | 23 |
| Nebraska | 6.0% | 7.1% | 13 | 19 |
| Oklahoma | 5.2% | 8.1% | 14 | 12 |

Where the state medians moved, the added vintage did it, not the estimator: Oklahoma's
2009–2015 rate was +0.97% a year against about +0.5% since, which lifts the rate used
from +0.51% to +0.69%. Oklahoma's earlier range (±1.6%) rested on two nearly equal
periods and excluded the new median; it is now ±4.3%. Colorado's state range narrowed
because its three periods agree better than its two did. The youth update moved state
totals by less than 0.2 points and a few counties by 2 to 4 points.

### Kansas under the same settings (not applied)

**Decision, 2026-11-18.** Chris, relayed through the dashboard review session: "Let's
leave Kansas as adopted then". Kansas keeps the estimator described above and release
`20261117T221214_8929b5f9`. Whether to apply the virtual-school exclusion to Kansas alone
is still open.

| Measure | Adopted | With these settings |
|---|---|---|
| 2035 median | 1,731,277 | 1,743,167 |
| Change from 2024 | −0.91% | −0.23% |
| State 80% range | 1.673M–1.788M | 1.683M–1.804M |
| Counties down over 10% | 18 | 26 |
| Largest county decline | −22.6% | −17.5% |
| Counties moving over 5 points | – | 11 |
| Largest county move | – | 11.8 points |
| Backtest error, 2019–2024 | 2.8% | 2.7% |
| Backtest 80% holds, 2019–2024 | 75% | 74% |

Leaving out fully virtual schools alone would move the Kansas median by −641 and no
county by more than 2.0 points; 25 county age bands change by 25 residents or more, and
Morton County's ages 15–17 ratio goes from the 1.15 cap to 0.89. Almost all of the rest
comes from the added vintage. Kansas gains little in the backtest, so the case for moving
it was consistency with its neighbours, not accuracy.

One setting at a time, same seed and draws (the first row reproduces the adopted table
with a maximum difference of 0):

| Setting changed | 2035 median | Step |
|---|---|---|
| Adopted | 1,731,277 | – |
| Virtual schools left out | 1,730,636 | −641 |
| 2005–2009 vintage added | 1,743,364 | +12,728 |
| Refined estimator | 1,743,167 | −197 |

- The added period is the strongest of the three: statewide +0.39% a year for 2009–2015,
  against +0.01% and +0.27%. The rate used would rise from +0.16% to +0.25% a year.
- With three periods the fit finds less persistence (`tau` 0.68 → 0.51, `omega`
  0.41 → 0.65), so the share of own history kept falls from 0.39–0.84 to 0.45–0.65.
  Johnson County's rate would fall from +0.78% to +0.64% a year (about −5,300 by 2035);
  Geary County's would rise from −1.11% to +0.04% (+1.4% → +13.4% by 2035).
- The refined step leaves the total alone and redistributes: kept shares fall to
  0.21–0.51, the size slope is 0.13 points per log unit and the fitted size volatility is
  zero. Fifteen small counties (median 1,667 working-age residents) cross −10%, taking
  the count from 11 to 26.
- **Geary and Riley, decision 2026-11-18.** Chris: "Lets treat the Geary and Riley gains
  as a one time change at Fort Riley". Their 2009–2015 gains are therefore not a pattern
  to project forward. The adopted Kansas forecast already leaves that period out, so
  nothing changes now. If Kansas ever adds the 2005–2009 vintage, those two counties'
  2009–2015 rates should be left out of it or handled separately. This is a working
  assumption on Chris's direction; no installation or Census source was checked here.
- Not verified: how much of the 2009–2015 rate reflects the 2010 Census re-base.

### Still assumed or unreliable in the four states

- The `w·c / (w + c)` survey variance assumes the age split behaves like a simple sample
  of a fixed total. Missouri's data prefer no reversing error at all; the published
  margins were used anyway because zero survey error is not physically possible.
- The size tilt is linear in log population. It is anchored to the statewide rate, but
  the largest counties need not follow it.
- Size-related volatility accumulates over the horizon. For the smallest Colorado
  counties the 80% range reaches ±40% by 2035, which is honest about how little is known
  and not useful as a planning figure.
- The 2005–2009 vintage had a smaller sample than later ones and sits on 2000-based
  population controls; the 2009 → 2015 residual spans the 2010 re-base as 2019 → 2024
  spans the 2020 one.
- Fully virtual status is as NCES reports it. Hybrid and supplemental virtual schools
  stay in, and districts are still counted in their office county.
- φ, mortality, aging and participation are unchanged and carry no uncertainty.
