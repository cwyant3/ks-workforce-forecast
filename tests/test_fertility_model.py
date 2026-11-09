import numpy as np
import pandas as pd
import pytest

from demographic_pipeline import project_cohorts
from fertility_model import calibrated_schedule, fertility_schedules, project_fertility_paths


def inputs():
    pop=np.ones((2,120))*100
    share=np.ones_like(pop)*.5
    survival=np.ones_like(pop)
    local=np.zeros(120); local[14:55]=.04
    national={year:local.copy() for year in range(2023,2043)}
    return pop,share,survival,local,national


def test_births_are_sum_of_female_exposure_times_age_rates():
    pop,share,survival,local,_=inputs()
    pop[1]=0; pop[1,29]=100; pop[1,30]=200
    rate=np.zeros(120); rate[29]=.1; rate[30]=.2
    result=project_cohorts(pop,share,survival,horizon=1,fertility_by_year={2025:rate})
    # Exposure29=50, exposure30=150 after annual aging.
    assert result.births.iloc[-1]==pytest.approx(35)
    assert result.newborn_survivors.iloc[-1]==pytest.approx(35)


def test_observed_anchor_common_paths_order_and_adult_workforce_unchanged():
    pop,share,survival,local,national=inputs()
    paths,schedules,bridge=project_fertility_paths(pop,share,survival,local,national,200)
    assert bridge>0
    for summary,ages in paths.values():
        assert summary.births.iloc[1]==200
        assert np.isfinite(ages).all() and (ages>=0).all()
        assert summary.iloc[-1].residents_under_18>0
    assert schedules['Low'][2042][30]/schedules['Central'][2042][30]==pytest.approx(.8)
    assert schedules['High'][2042][30]/schedules['Central'][2042][30]==pytest.approx(1.2)
    assert (paths['Low'][0].births.iloc[2:]<paths['Central'][0].births.iloc[2:]).all()
    assert (paths['High'][0].births.iloc[2:]>paths['Central'][0].births.iloc[2:]).all()
    assert np.allclose(paths['Low'][0].labor_force_18_plus,paths['High'][0].labor_force_18_plus)


def test_migration_changes_births_from_following_year_and_participation_does_not():
    pop,share,survival,local,national=inputs()
    baseline,_,_=project_fertility_paths(pop,share,survival,local,national,200)
    migration,_,_=project_fertility_paths(pop,share,survival,local,national,200,annual_net_migrants=100)
    labor,_,_=project_fertility_paths(pop,share,survival,local,national,200,participation_pp=10)
    assert migration['Central'][0].births.iloc[1]==baseline['Central'][0].births.iloc[1]
    assert migration['Central'][0].births.iloc[2]>baseline['Central'][0].births.iloc[2]
    assert np.allclose(labor['Central'][0].births.iloc[1:],baseline['Central'][0].births.iloc[1:])


def test_newborn_female_cohorts_eventually_contribute_maternal_exposure():
    pop,share,survival,_,_=inputs()
    rate=np.zeros(120); rate[14]=.1
    schedule={y:rate for y in range(2025,2043)}
    a=project_cohorts(pop,share,survival,fertility_by_year=schedule,births_by_year={2025:1000})
    b=project_cohorts(pop,share,survival,fertility_by_year=schedule,births_by_year={2025:0})
    assert a.loc[a.year.eq(2039),'births'].iloc[0]>b.loc[b.year.eq(2039),'births'].iloc[0]


def test_suppression_uses_state_rates_not_inferred_counts_and_overlap_rejected():
    pop,share,survival,local,national=inputs()
    rows=[]
    for county in ['20','20173']:
        for low,high in [(10,14),(15,17),(18,19),(20,24),(25,29),(30,34),(35,54)]:
            rows.append(dict(county_fips=county,age_low=low,age_high=high,age_label=f'{low}-{high}',
                             births=100. if county=='20' else np.nan,measure='births_by_maternal_age',
                             start_year=2020,end_year=2024))
    rates,detail=calibrated_schedule('20173',rows,pop,pop*10,national)
    assert detail.Method.str.contains('suppressed').all()
    assert detail['Pooled births 2020–2024'].isna().all()
    assert rates[30]>0
    with pytest.raises(ValueError,match='duplicated'):
        calibrated_schedule('20173',rows+[rows[-1]],pop,pop*10,national)


def test_missing_fertility_year_and_new_positive_zero_base_rejected():
    pop,share,survival,local,national=inputs()
    with pytest.raises(ValueError,match='every projection year'):
        project_cohorts(pop,share,survival,fertility_by_year={2025:local})
    national[2024][54]=national[2025][54]=0
    with pytest.raises(ValueError,match='undefined'):
        fertility_schedules(local,national)
