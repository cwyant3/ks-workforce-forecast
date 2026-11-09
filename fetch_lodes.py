"""LODES OD partitions are destination-state files.

main = homes and workplaces in the destination state; aux = homes outside it.
JT00 counts all jobs; JT01 counts primary jobs. A state's main+aux supports
complete inbound metrics, but complete resident outbound needs every destination
state (including its auxiliary file). Missing partitions are never zero filled.
"""
from pathlib import Path
import gzip
import io
import json
import requests
import pandas as pd

LODES_YEARS = list(range(2015, 2024))
LODES_BASE = 'https://lehd.ces.census.gov/data/lodes/LODES8'
FIPS_TO_ABBR = dict(zip('01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56'.split(), 'al ak az ar ca co ct de dc fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy'.split()))
_HTTP_HEADERS = {'User-Agent': 'workforce-forecast/2.0 public aggregate research'}
FLOW_COLUMNS = ['w_county_fips','h_county_fips','jobs','state_fips','year','job_type','partition','source_url','inbound_complete','outbound_complete']


def _state_abbr(state_fips):
    return FIPS_TO_ABBR[str(state_fips).zfill(2)]


def _od_url(state_abbr, year, job_type='JT00', part='main'):
    if job_type not in ('JT00','JT01') or part not in ('main','aux'):
        raise ValueError('Unsupported job universe or OD partition')
    return f'{LODES_BASE}/{state_abbr}/od/{state_abbr}_od_{part}_{job_type}_{year}.csv.gz'


def _aggregate_to_county(od_df, state_fips, year):
    od = od_df.copy()
    for col in ('w_geocode','h_geocode'):
        if not od[col].astype(str).str.fullmatch(r'\d{15}').all():
            raise ValueError('LODES block geocodes must contain 15 digits')
    od['w_county_fips'] = od.w_geocode.str[:5]
    od['h_county_fips'] = od.h_geocode.str[:5]
    od['S000'] = pd.to_numeric(od.S000, errors='raise')
    if od.S000.isna().any() or (od.S000 < 0).any():
        raise ValueError('Invalid LODES job count')
    sf = str(state_fips).zfill(2)
    if not od.w_county_fips.str.startswith(sf).all():
        raise ValueError('OD partition contains unexpected destination state')
    out = od.groupby(['w_county_fips','h_county_fips'],as_index=False).S000.sum().rename(columns={'S000':'jobs'})
    return out.assign(state_fips=sf,year=int(year))


def read_od_partition(path, state_fips, year, job_type, part):
    """Stream public block-pair counts into county aggregates; do not retain block rows."""
    pieces=[]
    for chunk in pd.read_csv(path,compression='gzip',dtype=str,usecols=['w_geocode','h_geocode','S000'],chunksize=200000):
        if part == 'main' and not chunk.h_geocode.str.startswith(str(state_fips).zfill(2)).all():
            raise ValueError('main contains interstate homes')
        if part == 'aux' and chunk.h_geocode.str.startswith(str(state_fips).zfill(2)).any():
            raise ValueError('aux contains in-state homes')
        pieces.append(_aggregate_to_county(chunk,state_fips,year))
    if not pieces:
        out=pd.DataFrame(columns=['w_county_fips','h_county_fips','jobs','state_fips','year'])
    else:
        out=pd.concat(pieces).groupby(['w_county_fips','h_county_fips','state_fips','year'],as_index=False).jobs.sum()
    return out.assign(job_type=job_type,partition=part,source_url=_od_url(_state_abbr(state_fips),year,job_type,part))


def fetch_lodes_scoped(destination_states, years, cache_dir, job_type='JT01', parts=('main','aux'), resident_state=None, download=True):
    """Return (county flows, partition coverage). Cache keys include universe and scope.

    resident_state filters origins only after collecting destination partitions.
    Download failures remain explicit coverage rows, never complete-cache claims.
    """
    root=Path(cache_dir); root.mkdir(parents=True,exist_ok=True)
    destinations=sorted({str(s).zfill(2) for s in destination_states})
    frames=[]; coverage=[]
    for sf in destinations:
        for year in sorted(set(years)):
            for part in parts:
                url=_od_url(_state_abbr(sf),year,job_type,part)
                raw=root/url.rsplit('/',1)[-1]
                cache=root/f'county_v2_{sf}_{part}_{job_type}_{year}.parquet'
                status='published'; error=''
                try:
                    if cache.exists():
                        frame=pd.read_parquet(cache)
                    else:
                        if not raw.exists():
                            if not download: raise FileNotFoundError('Partition not cached')
                            response=requests.get(url,headers=_HTTP_HEADERS,timeout=300)
                            response.raise_for_status()
                            temp=raw.with_suffix(raw.suffix+'.tmp'); temp.write_bytes(response.content); temp.replace(raw)
                        frame=read_od_partition(raw,sf,year,job_type,part)
                        frame.to_parquet(cache,index=False)
                    frames.append(frame)
                except (requests.RequestException, OSError) as exc:
                    status='access_failed'; error=f'{type(exc).__name__}: {exc}'
                except ValueError as exc:
                    # The partition was retrieved but failed validation (bad geocodes,
                    # wrong home state). That is not an access problem.
                    status='cannot_compute'; error=f'{type(exc).__name__}: {exc}'
                coverage.append(dict(destination_state=sf,year=year,partition=part,job_type=job_type,availability_status=status,source_url=url,quality_note=error))
    cov=pd.DataFrame(coverage)
    flows=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(columns=FLOW_COLUMNS)
    if not flows.empty:
        complete={(sf,y): all(((cov.destination_state==sf)&(cov.year==y)&(cov.partition==p)&(cov.availability_status=='published')).any() for p in ('main','aux')) for sf in destinations for y in years}
        flows['inbound_complete']=[complete[(s,y)] for s,y in zip(flows.state_fips,flows.year)]
        complete_national={y: all(complete.get((s,y),False) for s in FIPS_TO_ABBR) for y in years}
        flows['outbound_complete']=flows.year.map(complete_national)
        if resident_state is not None:
            flows=flows.loc[flows.h_county_fips.str.startswith(str(resident_state).zfill(2))].copy()
            # Origin-filtered extracts no longer cover all inbound jobs.
            flows['inbound_complete']=False
    return flows.reset_index(drop=True),cov


def fetch_lodes(state_fips='20', years=None, cache_dir=None, job_type='JT00'):
    """Legacy inbound entry point retains JT00 meaning, now includes main and aux."""
    if cache_dir is None: raise ValueError('cache_dir is required')
    flows,_=fetch_lodes_scoped([state_fips],years or LODES_YEARS,cache_dir,job_type)
    return flows


def compute_commute_metrics(lodes_df,state_fips='20',top_n_feeders=3):
    sf=str(state_fips).zfill(2); df=lodes_df.loc[lodes_df.w_county_fips.str.startswith(sf)].copy()
    if 'job_type' in df and df.job_type.nunique()>1: raise ValueError('Do not mix primary and all-job universes')
    results=[]
    nan = float('nan')
    for (county,year),grp in df.groupby(['w_county_fips','year']):
        # Totals and shares are published only when inbound coverage (main+aux) is
        # complete; otherwise the observed sum is kept but marked incomplete.
        complete='inbound_complete' in grp and bool(grp.inbound_complete.all())
        total=grp.jobs.sum()
        local=grp.loc[grp.h_county_fips==county,'jobs'].sum()
        feeds=(grp.loc[grp.h_county_fips!=county]
               .groupby('h_county_fips',as_index=False).jobs.sum()
               .sort_values(['jobs','h_county_fips'],ascending=[False,True]))
        share_ok = complete and total
        outbound_complete = 'outbound_complete' in grp and bool(grp.outbound_complete.all())
        results.append(dict(
            state_fips=sf, county_fips=county[-3:], w_county_fips=county, year=year,
            total_jobs_at_worksite=total if complete else nan,
            observed_jobs_at_worksite=total,
            jobs_workers_live_in_county=local if complete else nan,
            jobs_workers_imported=total-local if complete else nan,
            pct_workers_live_in_county=round(local/total*100,2) if share_ok else nan,
            pct_workers_imported=round((total-local)/total*100,2) if share_ok else nan,
            top_feeder_counties=','.join(feeds.head(top_n_feeders).h_county_fips) if complete else '',
            availability_status='published' if complete else 'incomplete',
            job_type=grp.job_type.iloc[0] if 'job_type' in grp else 'JT00',
            outbound_availability_status='published' if outbound_complete else 'incomplete',
        ))
    out=pd.DataFrame(results)
    if not out.empty:
        out['inbound_coverage_complete']=out.availability_status.eq('published')
        out['outbound_coverage_complete']=out.outbound_availability_status.eq('published')
    return out


def latest_commute_snapshot(lodes_df,state_fips='20'):
    out=compute_commute_metrics(lodes_df,state_fips)
    return out.loc[out.year==out.year.max()].copy() if not out.empty else out


def commuting_observations(flows, counties):
    """Normalize inbound county context and explicitly unavailable full outbound."""
    from data_contracts import normalize_observations
    metrics=compute_commute_metrics(flows)
    records=[]
    for county in counties:
        sub=metrics.loc[metrics.w_county_fips==county] if not metrics.empty else pd.DataFrame()
        years=sorted(flows.year.unique()) if not flows.empty else [2023]
        for year in years:
            row=sub.loc[sub.year==year]
            for name,unit in [('total_jobs_at_worksite','primary jobs'),('jobs_workers_imported','primary jobs'),('pct_workers_imported','percent')]:
                value=row.iloc[0][name] if not row.empty else float('nan')
                status=row.iloc[0].availability_status if not row.empty else 'not_reported'
                records.append(dict(geography_id=county,measure_id=name,estimate=value,unit=unit,availability_status=status,period_start=f'{year}-01-01',period_end=f'{year}-12-31',reference_label=f'LODES {year} primary jobs'))
            records.append(dict(geography_id=county,measure_id='resident_outbound_jobs',estimate=float('nan'),unit='primary jobs',availability_status='incomplete',period_start=f'{year}-01-01',period_end=f'{year}-12-31',reference_label=f'LODES {year} primary jobs',quality_note='Full outbound calculation is not enabled in this pilot; complete destination-state coverage is required.'))
    if not flows.empty and set(flows.job_type)!={'JT01'}: raise ValueError('Normalized pilot commuting requires primary jobs JT01')
    return normalize_observations(pd.DataFrame(records),source_id='lodes',defaults=dict(location_basis='workplace; origins from residence',frequency='annual',job_universe='JT01 primary jobs',source_status='published aggregate',method_type='county aggregation of published OD counts',source_url=f'{LODES_BASE}/ks/od/',release_id='LODES8',coverage_note='Inbound requires destination main+aux; availability status governs completeness. Full resident outbound is not enabled. Not a count of extra available workers.'))


def build_commuting_indicators(stagingdir, year=2023):
    """Read pilot partition caches and return normalized South Central observations.

    No network access or production writes. stagingdir is .tmp/agent-training,
    whose lodes/ child holds original Kansas JT01 main+aux archives.
    """
    from geography import SOUTH_CENTRAL_COUNTIES
    flows, _ = fetch_lodes_scoped(['20'], [year], Path(stagingdir)/'lodes', download=False)
    return commuting_observations(flows, SOUTH_CENTRAL_COUNTIES)
