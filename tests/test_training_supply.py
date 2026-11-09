import pandas as pd
import pytest
from fetch_ipeds import prepare_hd,parse_completions,attach_response_flags


def hd():
    return prepare_hd(pd.DataFrame({'UNITID':['156107'],'INSTNM':['Test College'],'STABBR':['KS'],'COUNTYCD':['20173'],'FIPS':['20']}))


def comp():
    return pd.DataFrame({'UNITID':['156107']*6,'CIPCODE':['01.8301','01.8301','01.8301','99','01','01.83'],'MAJORNUM':['1','1','2','1','1','1'],'AWLEVEL':['3','21','3','3','3','3'],'CTOTALT':['10','20','5','10','10','10'],'XCTOTALT':['R']*6})


def test_cip_total_hierarchy_second_major_and_award_level_no_double_count():
    detail=parse_completions(comp(),hd(),2025)
    assert detail.cip6.tolist()==['01.8301','01.8301']
    assert detail.completions.sum()==30
    assert set(detail.award_level)=={3,21}
    assert detail.imputation_flag.eq('R').all()
    assert detail.county_fips5.eq('20173').all()


def test_missing_county_does_not_become_state_fips_county():
    raw=pd.DataFrame({'UNITID':['1'],'INSTNM':['Unknown County'],'STABBR':['KS'],'COUNTYCD':[None],'FIPS':['20']})
    result=prepare_hd(raw)
    assert pd.isna(result.county_fips3.iloc[0])
    assert pd.isna(result.countycd.iloc[0])


def test_null_counts_survive_and_duplicate_reporting_rows_refused():
    raw=comp().iloc[:2].copy();raw.loc[0,'CTOTALT']=''
    result=parse_completions(raw,hd(),2025)
    assert pd.isna(result.completions.iloc[0]) and result.availability_status.iloc[0]=='not_reported'
    with pytest.raises(ValueError,match='Duplicate'): parse_completions(pd.concat([raw,raw]),hd(),2025)


def test_official_total_mismatch_is_explicit():
    raw=comp();raw.loc[3,'CTOTALT']='999'
    detail=parse_completions(raw,hd(),2025)
    checks=detail.attrs['reconciliation']
    assert not checks.loc[checks.award_level==3,'reconciled'].iloc[0]


def test_parent_branch_merge_must_be_many_to_one():
    raw=pd.DataFrame({'UNITID':['1','1'],'INSTNM':['A','B'],'STABBR':['KS']*2,'COUNTYCD':['20173']*2})
    with pytest.raises(ValueError,match='Duplicate'): prepare_hd(raw)


def test_response_flags_preserve_imputation_and_refuse_child_duplication():
    detail=parse_completions(comp(),hd(),2025)
    detail.attrs={}
    flags=pd.DataFrame({'UNITID':['156107'],'STAT_C':['1'],'LOCK_C':['8'],'PRCH_C':['1'],'IDX_C':['156107'],'PCC_F':['.5'],'IMP_C':['-2']})
    result=attach_response_flags(detail,flags)
    assert result.completions.sum()==30
    assert result.parent_reporting_status.eq('Includes child campuses').all()
    assert result.imputation_status.eq('Reported').all()
    flags['PRCH_C']='2'
    with pytest.raises(ValueError,match='Child campus'): attach_response_flags(detail,flags)
