"""Public institutional awards, never available workers or local placements."""
from pathlib import Path
import pandas as pd
from data_contracts import normalize_observations
from geography import SOUTH_CENTRAL_COUNTIES
from fetch_ipeds import _read_csv_from_zip, prepare_hd, parse_completions, attach_response_flags, IMPUTATION_LABELS, COMPLETIONS_URL, HD_URL, FLAGS_URL


def build_training_indicators(source_dir: Path, counties=SOUTH_CENTRAL_COUNTIES, year=2025):
    """Return (normalized county totals + provider/program observations, all-KS detail).

    source_dir contains unmodified C{year}_A.zip and HD{year}.zip. Only published
    aggregate records are read. Missing geography stays in detail for QA, never
    assigned to a fabricated county. No occupational allocation is performed.
    """
    source_dir=Path(source_dir)
    hd=prepare_hd(_read_csv_from_zip((source_dir/f'HD{year}.zip').read_bytes(),f'hd{year}.csv'))
    detail=parse_completions(_read_csv_from_zip((source_dir/f'C{year}_A.zip').read_bytes(),f'c{year}_a.csv'),hd,year,'20')
    checks=detail.attrs.pop('reconciliation')
    flags_path=source_dir/f'FLAGS{year}.zip'
    if flags_path.exists():
        detail=attach_response_flags(detail,_read_csv_from_zip(flags_path.read_bytes(),f'flags{year}.csv'))
    else:
        detail['flags_availability_status']='not_reported'
        detail['response_flag_status']='FLAGS archive not supplied'
        detail['parent_reporting_status']='Unknown; FLAGS archive not supplied'
        detail['imputation_status']=detail.imputation_flag.map(IMPUTATION_LABELS).fillna('Unknown or unavailable')
    detail=detail.merge(checks[['unitid','award_level','published_total','reconciled']],on=['unitid','award_level'],how='left',validate='many_to_one')
    detail['source_url']=COMPLETIONS_URL.format(year=year)
    detail['directory_url']=HD_URL.format(year=year)
    detail['flags_url']=FLAGS_URL.format(year=year)
    detail['reference_label']=f'Academic year {year-1}-{str(year)[-2:]}'
    detail['source_status']='provisional' if year==2025 else 'release status not verified'
    detail['reporting_unit']=detail.unitid.astype(str)
    detail['program_code']=detail.cip6
    detail['geography_id']=detail.county_fips5
    detail['location_basis']='institution reporting address'
    detail['geography_valid']=detail.county_fips5.astype(str).str.fullmatch(r'\d{5}')
    detail['coverage_note']='Reporting institutions only; awards may exceed people; institution location is not graduate residence or employment.'
    selected=detail.loc[detail.geography_id.isin(counties)].copy()
    rows=[]
    for county in counties:
        group=selected.loc[selected.geography_id==county]
        valid=not group.empty and group.completions.notna().all() and group.reconciled.eq(True).all()
        rows.append(dict(measure_id='credentials_awarded',geography_id=county,estimate=group.completions.sum() if valid else float('nan'),unit='awards',availability_status='published' if valid else ('not_reported' if group.empty else 'incomplete'),provider_count=group.unitid.nunique(),quality_note='' if valid else 'No reported provider detail or incomplete institutional reconciliation'))
    totals=pd.DataFrame(rows)
    programs=selected.assign(measure_id='credential_awards_by_program',estimate=selected.completions,unit='awards')
    programs['quality_note']='IPEDS total flag: '+programs.imputation_flag.astype(str)+' ('+programs.imputation_status+'); '+programs.parent_reporting_status
    defaults=dict(period_start=f'{year-1}-07-01',period_end=f'{year}-06-30',frequency='academic year',location_basis='institution reporting address',source_status='provisional' if year==2025 else 'release status not verified',method_type='reported or imputed administrative count',source_url=COMPLETIONS_URL.format(year=year),release_id=f'IPEDS-C{year}_A-PD' if year==2025 else f'IPEDS-C{year}_A',reference_label=f'Academic year {year-1}-{str(year)[-2:]}',coverage_note='Awards at reporting institutions; not distinct people, available workers, residence, placement or retention.')
    observations=normalize_observations(pd.concat([totals,programs],ignore_index=True),source_id='ipeds',defaults=defaults)
    return observations,detail
