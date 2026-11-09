"""Cache public KDHE KIC aggregate maternal-age tables, preserving suppression.

Only public aggregate county/state birth tables are requested. Run from any cwd.
The shared vault preprocess runner is invoked before parsing table companions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT.parent
DEST = ROOT / "data/demographic_inputs"
URL = "https://kic.kdhe.ks.gov/birth.php"
AGES = [(10, 14), (15, 17), (18, 19), (20, 24), (25, 29), (30, 34), (35, None), (None, None), (None, None)]
LABELS = ['10 to 14', '15 to 17', '18 to 19', '20 to 24', '25 to 29', '30 to 34', '35 plus', 'Unknown', 'All Ages']

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def params(years, state=False):
    d = dict(pasto='1',screen_id='',configfile='configs/birth_config.php',pasag='1',bw='0',flip='0',menu='0',dwn='0',sort='0',seladj='3',uadj='1',prob='0',grpa='0',row='1',col='4',et='3',ag='8',ma='4',ra='4',edu='3',biror='5',wicp='3',birpl='8',paysrc='10',areas='on',geo='0',submitbutton='Submit')
    d['yr[]'] = [str(y) for y in years]
    d['ct[]'] = ['920'] if state else [f'{i:03}' for i in range(1,210,2)]
    d['to[]'] = ['00']
    return d

def preprocess(path):
    subprocess.run([str(VAULT/'.claude/scripts/vault-run.cmd'),'preprocess.py',str(path)],cwd=VAULT,check=True,capture_output=True,text=True)
    companion = VAULT/'.cache/extracted'/f'{path.name}.md'
    text = companion.read_text(encoding='utf-8')
    if 'extraction_status: clean' not in text:
        raise ValueError(f'Preprocess did not fully extract {path.name}')
    return text

def parse_table(text, years, state, source_name, county_map):
    if '|  | ' + ' | '.join(LABELS) + ' |' not in text:
        raise ValueError(f'{source_name}: unexpected maternal-age columns')
    if 'Years=' + ','.join(map(str,years)) not in text:
        raise ValueError(f'{source_name}: returned years differ from request')
    rows = []
    for line in text.splitlines():
        if not line.startswith('| '): continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) != 10: continue
        name = cells[0]
        if state:
            if name not in ('State of Kansas','Kansas'): continue
            fips='20'
        else:
            if name not in county_map: continue
            fips=county_map[name]
        for i, token in enumerate(cells[1:]):
            clean = token.replace(',','')
            value = int(clean) if re.fullmatch(r'\d+',clean) else None
            if value is None and token != '#': raise ValueError(f'Unrecognized token {token!r}')
            low, high = AGES[i]
            rows.append(dict(county_fips=fips,county_name=name,year=years[0] if len(years)==1 else None,
                start_year=min(years),end_year=max(years),period_start=f'{min(years)}-01-01',period_end=f'{max(years)}-12-31',
                age_low=low,age_high=54 if low==35 else high,source_age_high=high,age_label=LABELS[i],
                measure='births_by_maternal_age' if i<7 else ('births_unknown_age' if i==7 else 'births_total'),
                births=value,status='published' if value is not None else 'suppressed',raw_value=token,
                source_url=URL,source_file=source_name,age_alignment_note='35+ source is open-ended; model cap54 is a proxy' if low==35 else 'native age band'))
    expected=9 if state else 945
    if len(rows)!=expected: raise ValueError(f'{source_name}: expected {expected}, found {len(rows)}')
    return rows

def validate(rows):
    keys=set(); groups={}
    for row in rows:
        key=(row['county_fips'],row['start_year'],row['end_year'],row['age_label'])
        if key in keys: raise ValueError(f'Duplicate observation {key}')
        keys.add(key)
        assert (row['births'] is None)==(row['status']=='suppressed')
        assert row['births'] is None or row['births']>=0
        groups.setdefault(key[:3],[]).append(row)
    reconciled=0
    for group in groups.values():
        assert len(group)==9
        if all(r['status']=='published' for r in group):
            total=next(r['births'] for r in group if r['measure']=='births_total')
            assert sum(r['births'] for r in group if r['measure']!='births_total')==total
            reconciled+=1
    return dict(rows=len(rows),groups=len(groups),complete_groups_reconciled=reconciled,
                suppressed_cells=sum(r['status']=='suppressed' for r in rows),
                published_zero_cells=sum(r['births']==0 for r in rows))

def build_totals(session,county_map,start,end,cache_only=False):
    """Independent published all-birth totals; never infer age-cell values."""
    years=list(range(start,end+1));rows=[];sources=[]
    for state in (False,True):
        request=params(years,state);request['col']='7'
        path=DEST/f'fertility_kic_{"state" if state else "county"}_totals_{start}_{end}.html'
        if not path.exists():
            if cache_only: raise FileNotFoundError(path)
            r=session.post(URL,data=request,timeout=120);r.raise_for_status();path.write_bytes(r.content)
        text=preprocess(path)
        expected_header='|  | '+' | '.join(map(str,years))+' | Total for Selection |'
        if expected_header not in text: raise ValueError('Unexpected total-table year header')
        before=len(rows)
        for line in text.splitlines():
            if not line.startswith('| '):continue
            cells=[c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells)!=len(years)+2:continue
            name=cells[0]
            if state and name in ('Kansas','State of Kansas'):fips='20'
            elif not state and name in county_map:fips=county_map[name]
            else:continue
            for year,token in zip(years,cells[1:-1]):
                clean=token.replace(',','');value=int(clean) if re.fullmatch(r'\d+',clean) else None
                if value is None and token!='#':raise ValueError(f'Unexpected total token {token}')
                rows.append(dict(county_fips=fips,county_name=name,year=year,start_year=year,end_year=year,
                    period_start=f'{year}-01-01',period_end=f'{year}-12-31',age_low=None,age_high=None,
                    source_age_high=None,age_label='All Ages',measure='births_total',births=value,
                    status='published' if value is not None else 'suppressed',raw_value=token,
                    source_url=URL,source_file=path.name,query_basis='independent public all-birth county-year table'))
        assert len(rows)-before==len(years)*(1 if state else 105)
        sources.append(dict(file=path.name,sha256=digest(path),url=URL,request_method='POST',request_parameters=request,
                            retrieval_date=datetime.now(timezone.utc).date().isoformat(),rows=len(rows)-before))
    assert len({(r['county_fips'],r['year']) for r in rows})==len(rows)
    (DEST/f'fertility_birth_totals_{start}_{end}.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
    return rows,sources

def build(start=2006,end=2024,pooled_only=False,cache_only=False):
    DEST.mkdir(parents=True,exist_ok=True)
    session=requests.Session()
    if not cache_only:
        response=session.get(URL,timeout=90);response.raise_for_status()
        if not (DEST/'fertility_kic_form.html').exists():
            (DEST/'fertility_kic_form.html').write_bytes(response.content)
        notes=DEST/'fertility_kic_notes.html'
        if not notes.exists():
            response=session.get('https://kic.kdhe.ks.gov/OHA/kicnote.html',timeout=90);response.raise_for_status();notes.write_bytes(response.content)
    preprocess(DEST/'fertility_kic_form.html')
    preprocess(DEST/'fertility_kic_notes.html')
    form=(DEST/'fertility_kic_form.html').read_text(encoding='utf-8')
    county_map={name:'20'+code for code,name in re.findall(r'<option value="(\d{3})">([^<]+ County)</option>',form)}
    assert len(county_map)==105
    sources=[]; annual=[];pooled=[]
    jobs=[(list(range(2020,2025)),False),(list(range(2020,2025)),True)]
    if not pooled_only: jobs += [([y],state) for y in range(start,end+1) for state in (False,True)]
    for years,state in jobs:
        label=str(years[0]) if len(years)==1 else f'{years[0]}_{years[-1]}'
        path=DEST/f'fertility_kic_{"state" if state else "county"}_{label}.html'
        request=params(years,state)
        if not path.exists():
            if cache_only: raise FileNotFoundError(path)
            r=session.post(URL,data=request,timeout=120);r.raise_for_status()
            if b'Births: Residents' not in r.content: raise ValueError(f'Query returned no result: {path.name}')
            path.write_bytes(r.content)
        parsed=parse_table(preprocess(path),years,state,path.name,county_map)
        (annual if len(years)==1 else pooled).extend(parsed)
        sources.append(dict(file=path.name,sha256=digest(path),url=URL,request_method='POST',request_parameters=request,
                            retrieval_date=datetime.now(timezone.utc).date().isoformat(),rows=len(parsed)))
        print(f'{path.name}: {len(parsed)} rows; {sum(r["status"]=="suppressed" for r in parsed)} suppressed',flush=True)
        if len(years)>1:
            (DEST/'fertility_pooled_births_2020_2024.json').write_text(json.dumps(pooled,indent=2)+'\n',encoding='utf-8')
    if annual:(DEST/f'fertility_county_births_{start}_{end}.json').write_text(json.dumps(annual,indent=2)+'\n',encoding='utf-8')
    totals=[]
    if not pooled_only:
        totals,total_sources=build_totals(session,county_map,start,end,cache_only)
        sources.extend(total_sources)
    supporting=[]
    for name,url in [('fertility_kic_form.html',URL),('fertility_kic_notes.html','https://kic.kdhe.ks.gov/OHA/kicnote.html#Birth')]:
        path=DEST/name
        if path.exists(): supporting.append(dict(file=name,url=url,sha256=digest(path)))
    validation=dict(annual=validate(annual),pooled=validate(pooled),independent_totals=dict(rows=len(totals),suppressed_cells=sum(r['status']=='suppressed' for r in totals)))
    (DEST/'fertility_validation.json').write_text(json.dumps(validation,indent=2)+'\n',encoding='utf-8')
    manifest=dict(source='Kansas KDHE KIC resident live births',sources=sources,supporting_sources=supporting,calendar_years=True,
        suppression='Public # confidentiality marker retained as null; no reconstruction or zero imputation.',
        age_tail='Source35+ is open-ended. age_high54 is solely an explicit model alignment proxy; source_age_high is null.',
        unknown_age='Preserved separately, never assigned to an age.',
        statewide='Direct state query, not sum of partially suppressed county cells.',
        latest_available_year=2024,retrieval_date=datetime.now(timezone.utc).date().isoformat())
    (DEST/'fertility_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--start',type=int,default=2006);parser.add_argument('--end',type=int,default=2024);parser.add_argument('--pooled-only',action='store_true');parser.add_argument('--cache-only',action='store_true')
    args=parser.parse_args();build(args.start,args.end,args.pooled_only,args.cache_only)
