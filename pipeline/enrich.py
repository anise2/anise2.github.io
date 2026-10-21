"""Evidence imports: archived disc averages and conservative MangaUpdates matching.

Only metadata is fetched. Original pickle globals are replaced with inert records;
no downloaded Python functions or pandas constructors are executed.
"""
import argparse
import io
import json
import pickle
import re
import time
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

from pipeline.common import (load_snapshot, write_json, original_for, relation_graph,
    series_members, is_tv, parse_date, season_progress)
from pipeline.fetch import session

SALES_REV = '76ec74bcadb9817031c3067dd5cf68b4ba6e2b12'
SALES_RAW_REV = 'b729e3979de09c5e11c6edd1bdcd10216e2fef73'
SALES_REPO = 'https://github.com/zhouandrewc/anime-sales-prediction'
SALES_URL = SALES_REPO + '/blob/' + SALES_RAW_REV + '/data/clean_anime_sales.pickle'
CROSSWALK_URL = 'https://github.com/manami-project/anime-offline-database/releases/download/2026-27/anime-offline-database-minified.json.zst'
ADAPTATION_CACHE_DAYS = 45


def key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if c.isalnum())


class InertRecord:
    def __new__(cls,*args):
        obj = object.__new__(cls)
        obj.args = args
        return obj

    def __setstate__(self,state):
        self.state = state


class MetadataReader(pickle.Unpickler):
    def find_class(self,module,name):
        allowed = {('pandas.core.frame','DataFrame'),('pandas.core.internals.managers','BlockManager'),
            ('pandas.core.indexes.base','_new_Index'),('pandas.core.indexes.base','Index'),
            ('numpy.core.multiarray','_reconstruct'),('numpy','ndarray'),('numpy','dtype'),('builtins','slice')}
        if (module,name) not in allowed:
            raise ValueError(f'Unexpected metadata constructor: {module}.{name}')
        return type(name,(InertRecord,),{})


def cached_bytes(client,url,path):
    path=Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        r=client.get(url,timeout=120);r.raise_for_status();path.write_bytes(r.content)
    return path.read_bytes()


def sales_rows(blob):
    obj=MetadataReader(io.BytesIO(blob)).load()
    state=obj.state['_mgr'].state
    columns=state[0][0].args[1]['data'].state[4]
    array=state[1][0].state
    width,height=array[1]
    required=['title','year','season_qtr','avg_sales','re_rls','total','gross_1st_rls','studio','source']
    if columns not in (required,required+['link']) or width != len(columns):
        raise ValueError('Upstream sales schema changed')
    values=array[4]
    return [{col:values[j*height+i] for j,col in enumerate(columns)} for i in range(height)]


def validate_raw_sales(rows):
    """Reject the upstream later cleaning stage that silently split comma digits.

    These anchors are from the pinned *pre-cleaning* data file, not estimates.
    Never repair the truncated file by multiplying its values by 1000.
    """
    anchors={'Hyouka':'10,046','Shigatsu wa Kimi no Uso':'10,727','Bakemonogatari':'79,201'}
    observed={r['title']:str(r['avg_sales']) for r in rows}
    if any(observed.get(title)!=value for title,value in anchors.items()):
        raise ValueError('Disc source failed raw-number integrity checks; possible comma truncation')


def import_sales(snapshot,client,cache,output):
    import zstandard
    blob=cached_bytes(client,'https://raw.githubusercontent.com/zhouandrewc/anime-sales-prediction/'+SALES_REV+'/data/anime_sales_df_linked.pickle',cache/'sales.pickle')
    raw_blob=cached_bytes(client,'https://raw.githubusercontent.com/zhouandrewc/anime-sales-prediction/'+SALES_RAW_REV+'/data/clean_anime_sales.pickle',cache/('sales-raw-'+SALES_RAW_REV+'.pickle'))
    raw_rows=sales_rows(raw_blob)
    validate_raw_sales(raw_rows)
    raw_lookup={(r['title'],r['year']):r for r in raw_rows}
    # Remove every previous import from this provider before rebuilding validated records.
    for record in output.values():
        if 'anime-sales-prediction' in record.get('disc_evidence',{}).get('disc_url',''):
            record.pop('disc_evidence',None)
    cross=cached_bytes(client,CROSSWALK_URL,cache/'anime-offline-database.json.zst')
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(cross)) as stream:
        cross=json.load(stream)
    aliases={}
    for entry in cross['data']:
        for url in entry['sources']:
            m=re.search(r'myanimelist.net/anime/(\d+)',url)
            if m:
                aliases[int(m[1])]=[entry['title'],*entry.get('synonyms',[])]
    lookup=defaultdict(set)
    for s in snapshot['subjects']:
        if is_tv(s) and parse_date(s['date']):
            for title in [s['name'],s['name_cn']]:
                lookup[(key(title),s['date'][:4])].add(s['id'])
    count=0
    for row in sales_rows(blob):
        raw=raw_lookup.get((row['title'],row['year']))
        if not raw:continue
        m=re.search(r'myanimelist.net/anime/(\d+)',str(row['link']))
        if not m:continue
        ids=set()
        for title in [row['title'],*aliases.get(int(m[1]),[])]:
            ids.update(lookup.get((key(title),str(row['year'])),set()))
        value=str(raw['avg_sales']).replace(',','').strip()
        if len(ids)!=1 or not value.isdigit() or int(value)<=0:continue
        sid=str(next(iter(ids)))
        output.setdefault(sid,{})['disc_evidence']={'disc_average':int(value),'disc_scope':'bd_dvd_volume_average',
            'disc_url':SALES_URL,'disc_title':row['title'],'disc_year':int(row['year']),
            'disc_raw_value':str(raw['avg_sales']), 'disc_raw_revision':SALES_RAW_REV,
            'disc_match':'exact Japanese/Chinese alias + release year; MAL ID crosswalk',
            'observed_at':'2020-10-13','disc_observed_at':'2020-10-13'}
        count+=1
    print(f'Disc metadata: {count} exact title/year matches; BD + DVD volume averages, snapshot 2020-10-13.',flush=True)
    return count


def parse_endpoint(record,original,season_count):
    """An endpoint is not a coverage percentage: chapters may be skipped/reordered."""
    raw=(record.get('anime') or {}).get('end','') or ''
    if not raw or 'N/A' in raw:return {}
    parts=re.split(r'\s*/\s*|\n',raw)
    labelled=[p for p in parts if re.search(r'\bS(?:eason\s*)?\d',p,re.I)]
    if labelled:
        parts=[p for p in labelled if re.search(r'\bS(?:eason\s*)?'+str(season_count)+r'\b',p,re.I)]
        if len(parts)!=1:return {}
    elif season_count != 1 or len(parts)!=1:
        return {}
    end=parts[0]
    # Do not translate alternate endings / ranges into invented precise ratios.
    if re.search(r'alternat|original|diverg|different|\d\s*[-~]\s*\d',end,re.I):return {}
    chapter=re.search(r'\bChap(?:ter)?\.?\s*(\d+)',end,re.I)
    volume=re.search(r'\bVol(?:ume)?\.?\s*(\d+)',end,re.I)
    total_chapters=original.get('chapters')
    total_volumes=original.get('volumes')
    if chapter and total_chapters and int(chapter[1])<=total_chapters:
        return {'adaptation_endpoint':int(chapter[1]),'adaptation_total':total_chapters,'adaptation_unit':'话'}
    if volume and total_volumes and int(volume[1])<=total_volumes:
        return {'adaptation_endpoint':int(volume[1]),'adaptation_total':total_volumes,'adaptation_unit':'卷'}
    return {}


def adaptation_priority(item,cache,today):
    subject,original,_=item
    path=cache/'mangaupdates'/f'{original["id"]}.json'
    cached=load_snapshot(path) if path.exists() else {}
    observed=parse_date(cached.get('observed_at'))
    valid=cached.get('matching_version')==2 and observed is not None
    # Unqueried originals must not starve behind popular but expired entries.
    # Fresh results are still consumed below even after the request budget ends.
    phase=0 if not valid else 2 if (today-observed).days<ADAPTATION_CACHE_DAYS else 1
    oldest=observed.isoformat() if phase==1 else ''
    return phase,oldest,-subject.get('votes',0),original['id']


def import_adaptations(snapshot,client,cache,output,max_lookups):
    subjects,_,_,groups=relation_graph(snapshot)
    grouped=series_members(snapshot,subjects,groups)
    cutoff=parse_date(snapshot['snapshot_at'])
    selected=[]
    for group,members in grouped.items():
        aired=[s for s in members if is_tv(s) and parse_date(s['date']) and '2006-01-01'<=s['date']<=snapshot['snapshot_at']]
        if not aired:continue
        latest=max(aired,key=lambda s:(s['date'],s['id']))
        original=original_for(latest,members)
        if original:selected.append((latest,original,season_progress(members,cutoff)['regular']))
    selected.sort(key=lambda item:adaptation_priority(item,cache,date.today()))
    used,matched,notes=0,0,0
    for s,original,seasons in selected:
        path=cache/'mangaupdates'/f'{original["id"]}.json'
        cached=load_snapshot(path) if path.exists() else {}
        observed=parse_date(cached.get('observed_at'))
        fresh=cached.get('matching_version')==2 and observed is not None and (date.today()-observed).days<ADAPTATION_CACHE_DAYS
        if not fresh and used < max_lookups:
            used+=1
            try:
                r=client.post('https://api.mangaupdates.com/v1/series/search',json={'search':original['name'],'per_page':10},timeout=25)
                r.raise_for_status()
                candidates=[v['record'] for v in r.json().get('results',[])[:5]]
                names={key(original['name']),key(original['name_cn'])}
                matches=[]
                for candidate in candidates:
                    if (original['kind']=='小说') != ('novel' in candidate.get('type','').lower()):continue
                    detail_path=cache/'mangaupdates-details'/f'{candidate["series_id"]}.json'
                    if detail_path.exists():v=load_snapshot(detail_path)
                    else:
                        r=client.get('https://api.mangaupdates.com/v1/series/'+str(candidate['series_id']),timeout=25);r.raise_for_status();v=r.json()
                        write_json(detail_path,v);time.sleep(.15)
                    if names & {key(v['title']),*(key(a['title']) for a in v.get('associated',[]))}:matches.append(v)
                record=matches[0] if len(matches)==1 else None
                if record and 'anime' not in record:
                    r=client.get('https://api.mangaupdates.com/v1/series/'+str(record['series_id']),timeout=25);r.raise_for_status();record=r.json()
                cached={'matching_version':2,'observed_at':date.today().isoformat(),'record':{k:record.get(k) for k in ['series_id','title','url','status','anime']} if record else None}
                write_json(path,cached)
                time.sleep(.25)
            except Exception as error:
                print(f'MangaUpdates lookup skipped #{original["id"]}: {type(error).__name__}',flush=True)
                continue
            if used%20==0:print(f'MangaUpdates requests {used}/{max_lookups}; matched progress notes {notes}.',flush=True)
        elif not fresh:continue
        record=cached['record']
        if not record:continue
        anime=record.get('anime') or {}
        if not anime.get('end') or anime['end'] in ('N/A','None'):continue
        notes+=1
        evidence={'observed_at':cached['observed_at'],'adaptation_observed_at':cached['observed_at'],
            'adaptation_url':record['url'],'adaptation_note':anime,'adaptation_source_status':record.get('status'),
            **parse_endpoint(record,original,seasons)}
        matched+=int('adaptation_endpoint' in evidence)
        output.setdefault(str(s['id']),{})['adaptation_evidence']=evidence
        output[str(s['id'])].pop('story_completion',None)
        final_chapter = (evidence.get('adaptation_unit')=='话' and evidence.get('adaptation_endpoint')==original.get('chapters')
            and bool(original.get('chapters')) and re.search(r'\bComplete\b',record.get('status') or '',re.I)
            and not re.search(r'ongoing|hiatus|diverg|original ending|changed ending',str(record.get('status'))+' '+anime['end'],re.I))
        full_story = re.search(r'fully adapted|adapts the full story|complete adaptation',anime['end'],re.I)
        if original['status']=='completed' and (full_story or final_chapter):
            output[str(s['id'])]['story_completion']={'observed_at':cached['observed_at'],
                'reason':'原作已完结；改编资料明确标注完整故事改编' if full_story else '原作已完结；改编终点到达原作最后一话（规则识别）', 'url':record['url']}
    print(f'Adaptation evidence: {notes} sourced notes, {matched} safely paired endpoints.',flush=True)
    return {'requests':used,'notes':notes,'numeric':matched}


def run(args):
    snap=load_snapshot(args.snapshot);client=session();cache=Path(args.cache)
    existing=load_snapshot(args.output) if Path(args.output).exists() else {}
    output=existing.get('subjects',{})
    sales=import_sales(snap,client,cache,output)
    progress=import_adaptations(snap,client,cache,output,args.max_lookups)
    write_json(args.output,{'observed_at':date.today().isoformat(),'sales_matches':sales,'progress':progress,
        'sources':[SALES_URL,CROSSWALK_URL,'https://api.mangaupdates.com/'],'subjects':output})
    # Persist evidence inside future immutable monthly metadata snapshots too.
    for s in snap['subjects']:
        if 'anime-sales-prediction' in s.get('disc_evidence',{}).get('disc_url',''):
            s.pop('disc_evidence',None)
        s.update(output.get(str(s['id']),{}))
    snap['external_observed_at']=date.today().isoformat()
    write_json(args.snapshot,snap)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',default='.cache/latest.json');p.add_argument('--output',default='data/external/enrichment.json')
    p.add_argument('--cache',default='.cache/research');p.add_argument('--max-lookups',type=int,default=120)
    run(p.parse_args())
