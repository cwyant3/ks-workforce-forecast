import pandas as pd
import pytest

from dashboard.training_pathways import (load_crosswalk, normalize_crosswalk,
    occupation_pay_rows, regional_occupation_rows, related_program_awards)


def details():
    return pd.DataFrame({
        'geography_id':['20173']*4+['20015'],
        'reporting_unit':['156107']*3+['999999','000001'],
        'program_code':['48.0508','48.0508','48.0508','15.0614','48.0508'],
        'award_level':[3,21,3,3,3], 'year':[2025,2025,2024,2025,2025],
        'completions':[10,20,900,5,800], 'institution_name':['A']*3+['B','C']})


def test_official_mapping_preserves_cip_zeroes_and_exact_soc():
    crosswalk=load_crosswalk()
    assert not crosswalk.duplicated(['program_code','occupation_code']).any()
    assert crosswalk.program_code.str.fullmatch(r'\d{2}\.\d{4}').all()
    assert crosswalk.occupation_code.str.fullmatch(r'\d{2}-\d{4}').all()
    weld=crosswalk.loc[crosswalk.occupation_code.eq('51-4121')]
    assert set(weld.program_code)=={'15.0614','48.0508'}
    assert ((crosswalk.program_code=='46.0302') & (crosswalk.occupation_code=='47-2111')).any()
    assert crosswalk.program_code.str.startswith('01.').any()


def test_normalizer_excludes_no_match_and_reverse_duplicates():
    raw=pd.DataFrame({'CIP2020Code':['01.0000','01.0000','99.9999','24.0101'],
                      'CIP2020Title':['Agriculture','Agriculture','NO MATCH','Liberal Arts'],
                      'SOC2018Code':['19-1011','19-1011','11-1011','99-9999'],
                      'SOC2018Title':['Animal Scientist','Animal Scientist','Chief Executive','NO MATCH']})
    normalized=normalize_crosswalk(raw)
    assert normalized[['program_code','occupation_code']].values.tolist()==[['01.0000','19-1011']]


def test_many_to_many_relation_does_not_multiply_awards_or_mix_periods_counties():
    crosswalk=load_crosswalk()
    # Duplicate relationship rows and extra occupations still cannot multiply awards.
    repeated=pd.concat([crosswalk,crosswalk],ignore_index=True)
    result=related_program_awards(pd.concat([details(),details().iloc[[0]]]), repeated, '20173','51-4121')
    assert len(result)==3 and result.completions.sum()==35
    assert set(result.award_level)=={3,21}
    assert result.year.eq(2025).all() and result.geography_id.eq('20173').all()
    assert result.related_occupation_count.min()>=1
    assert related_program_awards(details(),crosswalk,'20173','00-0000').empty


def test_conflicting_award_identity_is_rejected_not_summed():
    conflict=details().iloc[[0]].copy();conflict['completions']=11
    with pytest.raises(ValueError,match='Conflicting duplicate'):
        related_program_awards(pd.concat([details(),conflict]),load_crosswalk(),'20173','51-4121')


def test_region_retains_native_soc_detail_and_excludes_other_regions():
    regional=pd.DataFrame({'geography_id':['KS-SC','KS-SC','KS-SC','KS-KC'],
        'occupation_code':['51-4121','51-0000','51-2090','51-4121'],
        'occupation_level':[6,2,5,6],'estimate':[100,1000,200,900]})
    result=regional_occupation_rows(regional,'20173')
    assert len(result)==1 and result.geography_id.iloc[0]=='KS-SC'
    assert result.estimate.iloc[0]==100
    assert regional_occupation_rows(regional,'20035').equals(result)
    johnson=regional_occupation_rows(regional,'20091')  # Johnson County: Kansas City region, never South Central
    assert johnson.geography_id.tolist()==['KS-KC'] and johnson.estimate.iloc[0]==900
    assert regional_occupation_rows(regional,'20999').empty  # not a Kansas county


def test_pay_requires_exact_occupation_and_adopted_area_not_broad_proxy():
    frame=pd.DataFrame({'source_id':['oews']*3,'occupation_code':['51-4121','00-0000','51-4121'],
        'measure_id':['oews_median_annual_wage']*3,'geography_id':['48620','48620','12345'],
        'estimate':[50000,60000,70000]})
    assert occupation_pay_rows(frame,'20173','51-4121').estimate.tolist()==[50000]
    assert occupation_pay_rows(frame,'20173','47-2111').empty
    assert occupation_pay_rows(frame,'20035','51-4121').empty


def test_missing_detail_or_unreported_county_does_not_fabricate_zero():
    assert related_program_awards(pd.DataFrame(),load_crosswalk(),'20173','51-4121').empty
    assert related_program_awards(details(),load_crosswalk(),'20077','51-4121').empty
