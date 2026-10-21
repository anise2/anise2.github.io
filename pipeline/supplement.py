"""Incremental current metadata and disc sales. Never download image bytes.

Every scrape is timestamped at acquisition, not backdated to anime release.
MAL's official API is used when MAL_CLIENT_ID exists; otherwise Jikan is optional.
"""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
import io
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
import requests
from pipeline.common import load_snapshot, write_json, is_tv, parse_date
from pipeline.enrich import key
from pipeline.fetch import session

AOD_URL='https://github.com/manami-project/anime-offline-database/releases/latest/download/anime-offline-database-minified.json.zst'

class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=[];self.table=None;self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.table=[]
        elif tag=='tr' and self.table is not None:self.row=[]
        elif tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(''.join(self.cell).strip());self.cell=None
        elif tag=='tr' and self.row is not None:
            self.table.append(self.row);self.row=None
        elif tag=='table' and self.table is not None:
            self.tables.append(self.table);self.table=None


def sales_key(title):
    # Normalize only unambiguous season notation. Do not fuzzy-match subtitles,
    # multiple cours, remakes, films or a shortened franchise name.
    title=re.sub(r'[（(]第?(\d+)期[)）]',r' 第\1期',title)
    title=re.sub(r'第?\s*(\d+)\s*期',r' season\1 ',title)
    title=re.sub(r'(\d+)(?:nd|rd|th)\s*season',r'season\1',title,flags=re.I)
    return key(title)


def parse_sales(html):
    parser=Tables();parser.feed(html)
    for table in parser.tables:
        if table and len(table[0])>=3 and '累平' in table[0][0] and 'タイトル' in table[0][2]:
            return [{'value':int(row[0].replace(',','')),'season':row[1],'title':row[2]}
                for row in table[1:] if len(row)>=3 and re.fullmatch(r'[\d,]+',row[0]) and int(row[0].replace(',',''))>0]
    raise ValueError('No verified cumulative BD/DVD average table')


def cached_text(client,url,path,ttl=7):
    if path.exists() and time.time()-path.stat().st_mtime<ttl*86400:return path.read_text()
    r=client.get(url,timeout=45);r.raise_for_status();path.parent.mkdir(parents=True,exist_ok=True);path.write_text(r.text);return r.text


def import_crosswalk(snap,client,cache,records):
    path=cache/'aod-current.json'
    if not path.exists() or time.time()-path.stat().st_mtime>7*86400:
        import zstandard
        response=client.get(AOD_URL,timeout=90);response.raise_for_status()
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(response.content)) as stream:current=json.load(stream)
        path.write_text(json.dumps(current,ensure_ascii=False))
    raw=path.read_text()
    cross=json.loads(raw);lookup=defaultdict(set);entries={}
    for e in cross['data']:
        mids={int(m[1]) for u in e.get('sources',[]) if (m:=re.search(r'myanimelist.net/anime/(\d+)',u))}
        if len(mids)!=1:continue
        mid=mids.pop();entries[mid]=e
        year=e.get('animeSeason',{}).get('year')
        for title in [e['title'],*e.get('synonyms',[])]:lookup[(key(title),str(year),e['type'])].add(mid)
    count=0
    for s in snap['subjects']:
        if not is_tv(s) or not parse_date(s['date']) or s['date']>date.today().isoformat():continue
        candidates=set()
        for title in [s['name'],s['name_cn']]:
            candidates|=lookup.get((key(title),s['date'][:4],{'TV':'TV','WEB':'ONA'}[s['platform']]),set())
        if len(candidates)!=1:continue
        mid=candidates.pop();e=entries[mid];record=records.setdefault(str(s['id']),{})
        record['external_anime']={'mal_id':mid,'url':f'https://myanimelist.net/anime/{mid}','provider':'anime-offline-database',
            'source_url':AOD_URL,'observed_at':date.today().isoformat(),'upstream_updated_at':cross.get('lastUpdate'),
            'match':'exact title alias + year + format','tags':e.get('tags',[]),'studios':e.get('studios',[]),'producers':e.get('producers',[]),
            'episodes':e.get('episodes'),'duration':e.get('duration'),'status':e.get('status')}
        # Image addresses only; no image fetch/cache/proxy in the pipeline.
        if e.get('picture','').startswith('https://cdn.myanimelist.net/'):
            record['external_image']=e['picture']
        count+=1
    return {'matched':count,'source_url':AOD_URL,'sha256':hashlib.sha256(raw.encode()).hexdigest(),'updated_at':cross.get('lastUpdate')}


def import_recent_sales(snap,client,cache,records,years):
    lookup=defaultdict(set)
    for s in snap['subjects']:
        if not is_tv(s) or not parse_date(s['date']):continue
        for title in [s['name'],s['name_cn']]:lookup[(sales_key(title),int(s['date'][:4]))].add(s['id'])
    reports=[]
    for year in years:
        url=f'https://www.lanovelibrary.com/{year}-hegemony-anime-ranking/'
        try:
            raw=cached_text(client,url,cache/f'sales-{year}.html')
            rows=parse_sales(raw);matches=defaultdict(list)
            for row in rows:
                ids=lookup.get((sales_key(row['title']),year),set())
                if len(ids)==1:matches[next(iter(ids))].append(row)
            matched=0
            for sid,values in matches.items():
                if len(values)!=1:continue
                row=values[0]
                record=records.setdefault(str(sid),{})
                evidence={'disc_average':row['value'],'disc_scope':'bd_dvd_known_volume_average',
                    'disc_provider':'ラノベライブラリ','disc_url':url,'disc_title':row['title'],'disc_year':year,
                    'observed_at':date.today().isoformat(),'disc_observed_at':date.today().isoformat(),
                    'disc_match':'exact normalized title + release year; ambiguous rows excluded',
                    'disc_note':'非官方汇总；BD + DVD 已知卷累平，缺少销量的卷不进入分母，排除再版；与旧来源口径分别保留。'}
                previous=record.get('recent_disc_evidence')
                if previous and previous.get('disc_average')==evidence['disc_average'] and previous.get('disc_scope')==evidence['disc_scope']:
                    evidence['observed_at']=previous['observed_at'];evidence['disc_observed_at']=previous['disc_observed_at']
                elif previous:
                    history=record.setdefault('disc_observations',[])
                    if previous not in history:history.append(previous)
                record['recent_disc_evidence']=evidence
                matched+=1
            reports.append({'year':year,'rows':len(rows),'matched':matched,'source_url':url,'sha256':hashlib.sha256(raw.encode()).hexdigest()})
            print(f'Recent sales {year}: {matched}/{len(rows)} exact matches',flush=True)
        except (requests.RequestException,ValueError) as e:
            reports.append({'year':year,'source_url':url,'error':type(e).__name__});print(f'Sales {year}: unavailable ({type(e).__name__})',flush=True)
        time.sleep(.6)
    return reports


def collect_mal(snap,client,cache,records,limit):
    client_id=os.environ.get('MAL_CLIENT_ID');provider='MyAnimeList API v2' if client_id else 'Jikan v4 (unofficial MAL API)'
    attempted=success=failed=0
    for s in sorted(snap['subjects'],key=lambda s:(s.get('date',''),s.get('votes',0)),reverse=True):
        record=records.get(str(s['id']),{});cross=record.get('external_anime')
        if not cross or not is_tv(s) or s.get('date','9999')>date.today().isoformat():continue
        prior=record.get('mal_evidence',{})
        if prior and (date.today()-date.fromisoformat(prior['observed_at'])).days<30:continue
        if attempted>=limit or failed>=3:break
        mid=cross['mal_id'];attempted+=1
        try:
            if client_id:
                r=client.get(f'https://api.myanimelist.net/v2/anime/{mid}',headers={'X-MAL-CLIENT-ID':client_id},
                    params={'fields':'mean,rank,popularity,num_list_users,num_scoring_users,num_episodes,average_episode_duration,source,genres,studios,statistics'},timeout=20)
            else:r=client.get(f'https://api.jikan.moe/v4/anime/{mid}',timeout=20)
            r.raise_for_status();raw=r.json();raw=raw if client_id else raw['data']
            record['mal_evidence']={'observed_at':date.today().isoformat(),'provider':provider,'url':cross['url'],
                'score':raw.get('mean') if client_id else raw.get('score'),'votes':raw.get('num_scoring_users') if client_id else raw.get('scored_by'),
                'members':raw.get('num_list_users') if client_id else raw.get('members'),'rank':raw.get('rank'),'popularity':raw.get('popularity'),
                'genres':[g['name'] for g in raw.get('genres',[])],'source':raw.get('source'),'studios':[g['name'] for g in raw.get('studios',[])],
                'producers':[g['name'] for g in raw.get('producers',[])],'episodes':raw.get('num_episodes') if client_id else raw.get('episodes')}
            success+=1
        except (requests.RequestException,ValueError,KeyError):failed+=1
        time.sleep(1.1)
    return {'provider':provider,'attempted':attempted,'collected':success,'failed':failed,'official_client_configured':bool(client_id)}


def run(args):
    snap=load_snapshot(args.snapshot);out=load_snapshot(args.output) if Path(args.output).exists() else {'subjects':{}}
    records=out['subjects'];cache=Path(args.cache);cache.mkdir(parents=True,exist_ok=True);client=session()
    # Keep this collector bounded: retries cannot turn 3 failures into dozens of calls.
    client.mount('https://',requests.adapters.HTTPAdapter(max_retries=1))
    report={'observed_at':date.today().isoformat()}
    try:report['crosswalk']=import_crosswalk(snap,client,cache,records)
    except (requests.RequestException,ValueError) as e:report['crosswalk']={'error':type(e).__name__}
    report['sales']=import_recent_sales(snap,client,cache,records,range(2021,date.today().year+1))
    report['mal']=collect_mal(snap,client,cache,records,args.max_lookups)
    out.update({'observed_at':date.today().isoformat(),'supplemental_report':report})
    write_json(args.output,out)
    for s in snap['subjects']:s.update(records.get(str(s['id']),{}))
    snap['supplemental_report']=report;write_json(args.snapshot,snap)
    write_json('artifacts/supplemental-report.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',default='.cache/latest.json');p.add_argument('--output',default='data/external/enrichment.json')
    p.add_argument('--cache',default='.cache/research');p.add_argument('--max-lookups',type=int,default=30)
    run(p.parse_args())
