import json
import pandas as pd
import pytest

from data_contracts import validate_observations
from fetch_oes import area_series_ids, build_oews_observations, parse_area_bulk_csv


def bulk_rows():
    base = dict(AREA='48620', AREA_TITLE='Wichita, KS', AREA_TYPE='4', NAICS='000000',
                I_GROUP='cross-industry', OWN_CODE='1235', O_GROUP='detailed',
                TOT_EMP='100', EMP_PRSE='4.2', A_MEAN='68000', MEAN_PRSE='2.1',
                A_MEDIAN='65000', HOURLY='')
    return [dict(base, OCC_CODE='00-0000', OCC_TITLE='All occupations', O_GROUP='total'),
            dict(base, OCC_CODE='47-2111', OCC_TITLE='Electricians'),
            dict(base, OCC_CODE='29-1211', OCC_TITLE='Anesthesiologists', A_MEAN='#', A_MEDIAN='#'),
            dict(base, OCC_CODE='27-2042', OCC_TITLE='Musicians', TOT_EMP='**', EMP_PRSE='**',
                 A_MEAN='*', A_MEDIAN='*', MEAN_PRSE='*', HOURLY='TRUE'),
            dict(base, OCC_CODE='47-0000', OCC_TITLE='Construction group', O_GROUP='major'),
            dict(base, OCC_CODE='47-2111', OCC_TITLE='Private electricians', OWN_CODE='5')]


def write_bulk(path, rows=None):
    pd.DataFrame(bulk_rows() if rows is None else rows).to_csv(path, index=False)


def test_bulk_area_filters_overlapping_totals_and_keeps_uncertainty_and_limits(tmp_path):
    path = tmp_path / 'oews-wichita-2025.csv'
    write_bulk(path)
    obs = parse_area_bulk_csv(path, '48620', 'Wichita, KS', 'Published inventory')
    assert validate_observations(obs) == []
    assert obs.occupation_code.nunique() == 4 and len(obs) == 12
    assert not obs.occupation_code.eq('47-0000').any()
    electric = obs.loc[obs.occupation_code.eq('47-2111')].set_index('measure_id')
    assert electric.loc['oews_employment', 'rse'] == 4.2
    assert electric.loc['oews_mean_annual_wage', 'rse'] == 2.1
    assert pd.isna(electric.loc['oews_median_annual_wage', 'rse'])
    censored = obs.loc[obs.occupation_code.eq('29-1211') & obs.measure_id.str.contains('wage')]
    assert censored.availability_status.eq('cannot_compute').all() and censored.estimate.isna().all()
    assert censored.quality_note.str.contains('>= USD 239,200').all()
    hourly = obs.loc[obs.occupation_code.eq('27-2042') & obs.measure_id.str.contains('wage')]
    assert hourly.availability_status.eq('not_applicable').all() and hourly.estimate.isna().all()


def test_complete_area_replaces_sample_without_duplicates_and_old_sample_still_loads(tmp_path):
    sample = {'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [
        {'seriesID': sid, 'data': [{'year': '2025', 'period': 'A01', 'value': '100', 'footnotes': [{}]}]}
        for sid in area_series_ids('48620', ['00-0000'])]}}
    (tmp_path / 'oews-wichita-2025-sample.json').write_text(json.dumps(sample))
    old = build_oews_observations(tmp_path)
    assert old.occupation_code.nunique() == 1 and old.coverage_note.str.contains('sample').all()
    write_bulk(tmp_path / 'oews-wichita-2025.csv')
    expanded = build_oews_observations(tmp_path)
    assert expanded.occupation_code.nunique() == 4 and len(expanded) == 12
    assert not expanded.coverage_note.str.contains('sample').any()


def test_bulk_slice_rejects_wrong_area_or_duplicate_published_ownership(tmp_path):
    path = tmp_path / 'bulk.csv'
    rows = bulk_rows()
    write_bulk(path, [dict(rows[0], AREA='28140')])
    with pytest.raises(ValueError, match='unreviewed area'):
        parse_area_bulk_csv(path, '48620', 'Wichita', '')
    write_bulk(path, rows + [rows[1]])
    with pytest.raises(ValueError, match='unique'):
        parse_area_bulk_csv(path, '48620', 'Wichita', '')
