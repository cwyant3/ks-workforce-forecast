import gzip
from pathlib import Path
import pandas as pd
import pytest
from fetch_lodes import fetch_lodes_scoped, compute_commute_metrics, read_od_partition
from geography import SOUTH_CENTRAL_COUNTIES, WICHITA_OEWS_COUNTIES, county_area_relations, inbound_labor_shed, select_area_observations


def partition(root, sf='20', part='main', job_type='JT01', rows=None):
    p=root/f'ks_od_{part}_{job_type}_2023.csv.gz'
    pd.DataFrame(rows,columns=['w_geocode','h_geocode','S000']).to_csv(p,index=False,compression='gzip')
    return p


def test_main_and_aux_needed_for_true_inbound_and_primary_universe(tmp_path):
    partition(tmp_path,rows=[('201730000000001','201730000000001',60),('201730000000001','200150000000001',30)])
    partition(tmp_path,part='aux',rows=[('201730000000001','400010000000001',10)])
    flows,coverage=fetch_lodes_scoped(['20'],[2023],tmp_path,download=False)
    assert flows.jobs.sum()==100
    assert flows.job_type.eq('JT01').all() and flows.inbound_complete.all()
    assert not flows.outbound_complete.any()
    metrics=compute_commute_metrics(flows).iloc[0]
    assert metrics.total_jobs_at_worksite==100 and metrics.jobs_workers_imported==40
    assert metrics.pct_workers_live_in_county==60
    assert metrics.inbound_coverage_complete and not metrics.outbound_coverage_complete


def test_missing_aux_is_not_a_complete_total_or_labor_shed(tmp_path):
    partition(tmp_path,rows=[('201730000000001','201730000000001',60)])
    flows,coverage=fetch_lodes_scoped(['20'],[2023],tmp_path,download=False)
    assert coverage.availability_status.tolist()==['published','access_failed']
    metrics=compute_commute_metrics(flows).iloc[0]
    assert pd.isna(metrics.total_jobs_at_worksite)
    assert metrics.observed_jobs_at_worksite==60
    assert not metrics.inbound_coverage_complete
    with pytest.raises(ValueError): inbound_labor_shed(flows,['20173'])


def test_legacy_unscoped_rows_do_not_gain_complete_coverage():
    flow=pd.DataFrame({'w_county_fips':['20173'],'h_county_fips':['20173'],'jobs':[60],'year':[2023]})
    result=compute_commute_metrics(flow).iloc[0]
    assert not result.inbound_coverage_complete and not result.outbound_coverage_complete
    assert pd.isna(result.total_jobs_at_worksite)


def test_legacy_jt00_cache_cannot_satisfy_primary_request(tmp_path):
    partition(tmp_path,job_type='JT00',rows=[('201730000000001','201730000000001',100)])
    flows,coverage=fetch_lodes_scoped(['20'],[2023],tmp_path,download=False)
    assert flows.empty and coverage.availability_status.eq('access_failed').all()


def test_partition_semantics_and_leading_zero_state(tmp_path):
    p=partition(tmp_path,part='aux',rows=[('201730000000001','201730000000001',4)])
    with pytest.raises(ValueError,match='aux'): read_od_partition(p,'20',2023,'JT01','aux')
    p=tmp_path/'al.csv.gz'
    pd.DataFrame([['010010000000001','010030000000001',5]],columns=['w_geocode','h_geocode','S000']).to_csv(p,index=False,compression='gzip')
    assert read_od_partition(p,'01',2023,'JT01','main').w_county_fips.iloc[0]=='01001'


def test_custom_shed_threshold_includes_crossing_row_and_keeps_named_regions_separate():
    flow=pd.DataFrame({'w_county_fips':['20173']*3,'h_county_fips':['20173','20015','40001'],'jobs':[60,25,15],'year':[2023]*3,'job_type':['JT01']*3,'inbound_complete':[True]*3})
    shed=inbound_labor_shed(flow,['20173'],.8)
    assert shed.h_county_fips.tolist()==['20173','20015']
    assert len(SOUTH_CENTRAL_COUNTIES)==7 and len(WICHITA_OEWS_COUNTIES)==4
    assert set(WICHITA_OEWS_COUNTIES)<set(SOUTH_CENTRAL_COUNTIES)
    context=select_area_observations(pd.DataFrame({'geography_id':['48620'],'estimate':[100]}),SOUTH_CENTRAL_COUNTIES)
    assert len(context)==1 and context.estimate.sum()==100


def test_origin_filtered_scope_is_not_full_inbound(tmp_path):
    partition(tmp_path,rows=[('201730000000001','201730000000001',60)])
    partition(tmp_path,part='aux',rows=[('201730000000001','400010000000001',10)])
    flows,_=fetch_lodes_scoped(['20'],[2023],tmp_path,resident_state='20',download=False)
    assert flows.jobs.sum()==60 and not flows.inbound_complete.any() and not flows.outbound_complete.any()


def test_invalid_partition_is_cannot_compute_not_access_failed(tmp_path):
    # A main file holding an out-of-state home was retrieved but fails validation.
    partition(tmp_path,rows=[('201730000000001','400010000000001',60)])
    partition(tmp_path,part='aux',rows=[('201730000000001','400010000000001',10)])
    flows,coverage=fetch_lodes_scoped(['20'],[2023],tmp_path,download=False)
    status=dict(zip(coverage.partition,coverage.availability_status))
    assert status=={'main':'cannot_compute','aux':'published'}
    assert not flows.inbound_complete.any()


def test_crosswalk_maps_every_kansas_county_to_one_projection_region():
    from geography import county_region
    relations = county_area_relations()
    assert len(relations) == 105 and relations.county_fips5.is_unique
    assert set(relations.projection_region_id) == {"KS-KC", "KS-NC", "KS-NE", "KS-NW", "KS-SC", "KS-SE", "KS-SW"}
    assert set(SOUTH_CENTRAL_COUNTIES) == set(relations.loc[relations.projection_region_id.eq("KS-SC"), "county_fips5"])
    region_id, name, members = county_region("20091")
    assert (region_id, name) == ("KS-KC", "Kansas City") and "Johnson" in members and len(members) == 4
    assert county_region("20999") == ("", "", [])
    # Only the Wichita metropolitan OEWS area is adopted; other counties never inherit it.
    assert set(relations.loc[relations.oews_area.ne(""), "county_fips5"]) == set(WICHITA_OEWS_COUNTIES)
