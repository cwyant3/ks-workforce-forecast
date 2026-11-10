import json
import pytest
from demand_pressure import build_demand_indicators

HEAD = ['Emp', 'HirA', 'Sep', 'EarnS', 'sEmp', 'sHirA', 'sSep', 'sEarnS', 'sex', 'agegrp', 'industry',
        'ownercode', 'seasonadj', 'firmsize', 'firmage', 'time', 'state', 'county']


def payload(industry, hires, period='2025-Q4'):
    return [HEAD, ['100', str(hires), '10', '4000', '1', '1', '1', '1', '0', 'A00', industry, 'A05', 'U', '0', '0', period, '20', '091']]


VERSION = 'QWI_F KS 20 1993:1-2026:1 V4.14.0 R2026Q4 qwipu_ks_20261105_0748\n'


def test_each_qwi_file_keeps_its_own_industry(tmp_path):
    (tmp_path / 'qwi-version.txt').write_text(VERSION)
    (tmp_path / 'qwi-2025-Q4-ks-62.json').write_text(json.dumps(payload('62', 30)))
    (tmp_path / 'qwi-2025q4-ks-manufacturing.json').write_text(json.dumps(payload('31-33', 12)))
    out = build_demand_indicators(tmp_path, ['20091'])
    hires = out[out.measure_id.eq('qwi_hires')].set_index('industry_code').estimate
    assert hires['62'] == 30 and hires['31-33'] == 12
    assert out[out.source_id.eq('qwi')].release_id.eq('R2026Q4').all()


def test_duplicate_industry_quarter_evidence_fails(tmp_path):
    (tmp_path / 'qwi-version.txt').write_text(VERSION)
    (tmp_path / 'qwi-2025-Q4-ks-62.json').write_text(json.dumps(payload('62', 30)))
    (tmp_path / 'qwi-copy-62.json').write_text(json.dumps(payload('62', 31)))
    with pytest.raises(ValueError, match='Duplicate QWI evidence'):
        build_demand_indicators(tmp_path, ['20091'])


def test_qwi_release_must_be_documented_and_single(tmp_path):
    (tmp_path / 'qwi-2025-Q4-ks-62.json').write_text(json.dumps(payload('62', 30)))
    with pytest.raises(ValueError, match='qwi-version.txt'):
        build_demand_indicators(tmp_path, ['20091'])
    (tmp_path / 'qwi-version.txt').write_text(VERSION + 'QWI_FA KS 20 1993:1-2025:3 V4.14.0 R2026Q3 x\n')
    with pytest.raises(ValueError, match='exactly one release'):
        build_demand_indicators(tmp_path, ['20091'])
