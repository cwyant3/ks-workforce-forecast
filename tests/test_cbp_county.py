import pytest
from fetch_cbp import parse_cbp_county_payload

HEAD = ['NAME', 'NAICS2017_LABEL', 'ESTAB', 'EMP', 'EMP_N', 'EMP_F', 'PAYANN', 'PAYANN_N', 'PAYANN_F', 'LFO', 'EMPSZES', 'NAICS2017', 'state', 'county']


def row(county, emp, flag=None, lfo='001', size='001', estab='10'):
    return ['x', 'Construction', estab, emp, '0', flag, '1', '0', None, lfo, size, '23', '20', county]


def test_withheld_cells_are_never_published_zeros():
    payload = [HEAD, row('091', '20203'), row('091', '0', 'N', size='210'), row('209', '0', 'D'),
               row('001', '0', 'S'), row('003', '0', 'N')]
    obs = parse_cbp_county_payload(payload, ['20091', '20209', '20001', '20003', '20005'], naics='23', year=2023)
    emp = obs[obs.measure_id.eq('cbp_employment')].set_index('geography_id')
    assert emp.loc['20091', 'estimate'] == 20203 and emp.loc['20091', 'availability_status'] == 'published'
    assert emp.loc['20209', 'availability_status'] == 'suppressed' and emp.loc['20001', 'availability_status'] == 'suppressed'
    assert emp.loc['20003', 'availability_status'] == 'not_reported' and emp.loc['20005', 'availability_status'] == 'not_reported'
    assert emp.loc[['20209', '20001', '20003', '20005'], 'estimate'].isna().all()


def test_mismatched_naics_fails():
    with pytest.raises(ValueError):
        parse_cbp_county_payload([HEAD, row('091', '5')], ['20091'], naics='62', year=2023)
