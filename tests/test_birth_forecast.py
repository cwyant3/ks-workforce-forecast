import numpy as np
import pandas as pd
import pytest

from birth_forecast import project_births, compare_models, _fit_predict
from demographic_pipeline import project_cohorts


def history(values=None):
    return pd.DataFrame({'year':range(2006,2026), 'births':np.full(20,100.) if values is None else values})


def test_stationary_births_select_simple_benchmark_and_preserve_history():
    result=project_births(history())
    assert result.attrs['model']=='last'
    assert result.attrs['observations']==20
    assert result.births.tolist()==[100.]*37
    assert result[result.status.eq('Census estimate')].year.tolist()==list(range(2006,2026))
    assert result[result.status.eq('Modeled births')].year.tolist()==list(range(2026,2043))


def test_models_use_same_rolling_origins_and_equal_horizon_weight():
    ranking,checks=compare_models(history(np.linspace(300,100,20)))
    assert set(checks.horizon)=={1,3,5}
    assert ranking['Held-out comparisons'].nunique()==1
    assert ranking['Held-out comparisons'].iloc[0]==24
    first=ranking.iloc[0]
    assert first['Mean absolute error']==pytest.approx(np.mean([first['1-year MAE'],first['3-year MAE'],first['5-year MAE']]))
    earlier=checks[(checks.origin==2015)&(checks.horizon==1)].prediction.tolist()
    altered=history(np.linspace(300,100,20))
    altered.loc[altered.year>2015,'births']=9999
    _,again=compare_models(altered)
    assert again[(again.origin==2015)&(again.horizon==1)].prediction.tolist()==earlier


def test_damped_log_path_flattens_without_negative_births():
    values=np.expm1(np.linspace(2,3,20))
    forecast,_,_=_fit_predict(values,100,.8)
    assert forecast[-1]-forecast[-2]<forecast[1]-forecast[0]
    assert (project_births(history(np.zeros(20))).births==0).all()


@pytest.mark.parametrize('defect',['missing','duplicate','negative','fractional'])
def test_invalid_history_cannot_silently_enter_birth_model(defect):
    data=history()
    if defect=='missing': data=data.drop(index=4)
    elif defect=='duplicate': data.loc[1,'year']=2006
    elif defect=='negative': data.loc[1,'births']=-1
    else:
        data['year']=data.year.astype(float)
        data.loc[1,'year']+=.5
    with pytest.raises(ValueError): project_births(data)


def test_birth_cohorts_survive_once_and_never_enter_adult_workforce_in_horizon():
    pop=np.zeros((2,120)); pop[:,30]=100
    rates=np.full_like(pop,.5); survival=np.ones_like(pop)
    survival[:,0]=.81; survival[:,1]=.64
    births=dict.fromkeys(range(2025,2043),0.)
    births[2025]=100.
    baseline=project_cohorts(pop,rates,survival)
    summary,ages=project_cohorts(pop,rates,survival,births_by_year=births,birth_sex_share=[.5,.5],return_age_states=True)
    assert ages[1,:,0].sum()==pytest.approx(90)
    assert ages[2,:,1].sum()==pytest.approx(100*.81*.8)
    assert (summary.labor_force_18_plus==baseline.labor_force_18_plus).all()
    assert summary.births.iloc[1]==100
    assert summary.newborn_survivors.iloc[1]==pytest.approx(90)
    assert summary.iloc[-1].residents_under_18>0
    assert summary.iloc[0].residents_all_ages==200


def test_incomplete_birth_path_is_not_treated_as_zero():
    pop=np.ones((2,120)); survival=np.ones_like(pop)
    with pytest.raises(ValueError,match='every projection year'):
        project_cohorts(pop,survival*.5,survival,births_by_year={2025:10})
