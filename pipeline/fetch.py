"""Download official archives in CI; a deliberately small API sample for local QA."""
import argparse
import hashlib
import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pipeline.common import duration_minutes, normalize_subject, normalize_book, write_json

UA = 'AnimeSeason2Predictor/0.1 (open-source research; https://github.com/bangumi/Archive)'


def session():
    s = requests.Session()
    s.headers['User-Agent'] = UA
    s.mount('https://', HTTPAdapter(max_retries=Retry(total=4, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
    return s


def get_json(s, url, **kwargs):
    r = s.get(url, timeout=60, **kwargs)
    r.raise_for_status()
    return r.json()


def api_sample(output, cache, count=120):
    s = session()
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)

    def cached(path, params=None):
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', path + json.dumps(params or {}, sort_keys=True))
        file = cache / (name + '.json')
        if file.exists():
            return json.loads(file.read_text())
        result = get_json(s, 'https://api.bgm.tv' + path, params=params)
        write_json(file, result)
        time.sleep(.15)
        return result

    subjects = {}
    for offset in range(0, count, 50):
        batch = cached('/v0/subjects', {'type': 2, 'sort': 'rank', 'limit': min(50, count - offset), 'offset': offset})
        for raw in batch['data']:
            if not raw.get('nsfw'):
                subjects[raw['id']] = normalize_subject(raw)
    relations, needed = [], set()
    original = list(subjects)
    for pos, sid in enumerate(original):
        for rel in cached(f'/v0/subjects/{sid}/subjects'):
            code = {'续集': 3, '前传': 2, '番外篇': 6, '主线故事': 12, '总集篇': 4}.get(rel.get('relation'))
            if code and rel.get('type') == 2:
                relations.append({'subject_id': sid, 'related_subject_id': rel['id'], 'relation_type': code})
                needed.add(rel['id'])
        if pos % 20 == 0:
            print(f'API relation sample {pos + 1}/{len(original)}', flush=True)
    for sid in sorted(needed - subjects.keys()):
        try:
            raw = cached(f'/v0/subjects/{sid}')
            if not raw.get('nsfw'):
                subjects[sid] = normalize_subject(raw)
        except requests.HTTPError as e:
            if e.response.status_code not in (404, 403):
                raise
    write_json(output, {'snapshot_at': datetime.now(timezone.utc).date().isoformat(), 'source': 'Bangumi API local debug sample',
        'mode': 'api_sample', 'source_url': 'https://bangumi.github.io/api/',
        'subjects': list(subjects.values()), 'relations': relations, 'sample_limit': count})
    print(f'Saved {len(subjects)} real anime records; covers remain remote URLs.', flush=True)


def asset_metadata(s, before=None):
    if not before:
        return get_json(s, 'https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json')
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN']} if os.getenv('GH_TOKEN') else {}
    assets, page = [], 1
    release = get_json(s, 'https://api.github.com/repos/bangumi/Archive/releases/tags/archive', headers=headers)
    while True:
        batch = get_json(s, release['assets_url'], params={'per_page': 100, 'page': page}, headers=headers)
        assets.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    candidates = [a for a in assets if re.match(r'dump-\d{4}-\d{2}-\d{2}.*\.zip$', a['name']) and a['name'][5:15] <= before]
    if not candidates:
        raise ValueError(f'No retained Archive snapshot on/before {before}. Supply your own historical normalized snapshots; never substitute latest data.')
    return max(candidates, key=lambda a: a['name'])


def archive(output, cache, before=None):
    s = session()
    meta = asset_metadata(s, before)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    file = cache / meta['name']
    if not file.exists():
        temporary = file.with_suffix('.partial')
        with s.get(meta['browser_download_url'], stream=True, timeout=(30, 180)) as r:
            r.raise_for_status()
            with temporary.open('wb') as f:
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)
        temporary.replace(file)
    digest = hashlib.sha256()
    with file.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    if (meta.get('digest') or '').startswith('sha256:') and digest.hexdigest() != meta['digest'][7:]:
        file.unlink()
        raise ValueError('Archive SHA-256 mismatch; removed corrupt cache')
    subjects, relations, books, adaptations, book_parents = [], [], {}, [], {}
    with zipfile.ZipFile(file) as z:
        names = z.namelist()

        def member(stem):
            matches = [n for n in names if Path(n).name.split('.')[0].replace('_', '-').rstrip('s') == stem.rstrip('s')]
            if len(matches) != 1:
                raise ValueError(f'Expected one {stem} file, found {matches}. Archive members: {names}')
            return matches[0]

        with z.open(member('subject')) as f:
            for line in io.TextIOWrapper(f, encoding='utf-8'):
                raw = json.loads(line)
                if raw.get('type') == 2 and not raw.get('nsfw'):
                    subjects.append(normalize_subject(raw))
                elif raw.get('type') == 1 and not raw.get('nsfw'):
                    books[int(raw['id'])] = normalize_book(raw)
        print(f'Read {len(subjects)} anime and {len(books)} book records; connecting original works.', flush=True)
        ids = {s['id'] for s in subjects}
        with z.open(member('subject-relations')) as f:
            for line in io.TextIOWrapper(f, encoding='utf-8'):
                r = json.loads(line)
                if r['subject_id'] in ids and r['related_subject_id'] in ids and r['relation_type'] in (2, 3, 4, 6, 12):
                    relations.append(r)
                a, b = r['subject_id'], r['related_subject_id']
                if r['relation_type'] == 1:
                    if a in ids and b in books:
                        adaptations.append((a,b))
                    elif b in ids and a in books:
                        adaptations.append((b,a))
                elif r['relation_type'] == 1002 and a in books and b in books:
                    book_parents[a] = b
                elif r['relation_type'] == 1003 and a in books and b in books:
                    book_parents[b] = a
        by_id = {s['id']:s for s in subjects}
        for anime, book in sorted(set(adaptations)):
            book = book_parents.get(book, book)
            target = by_id[anime].setdefault('originals', [])
            if not any(b['id'] == book for b in target):
                target.append(books[book])
        ovas = {s['id']: s for s in subjects if s['platform'] == 'OVA'}
        # Episode-level running times, when supplied, allow exact 0.1-season accounting.
        with z.open(member('episode')) as f:
            for line in io.TextIOWrapper(f, encoding='utf-8'):
                ep = json.loads(line)
                if ep.get('subject_id') in by_id and ep.get('type') == 0:
                    anime = by_id[ep['subject_id']]
                    airdate = str(ep.get('airdate') or '')
                    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',airdate) and airdate <= meta['name'][5:15]:
                        anime['last_episode_date'] = max(anime.get('last_episode_date',''),airdate)
                        anime['dated_episodes'] = anime.get('dated_episodes',0) + 1
                if ep.get('subject_id') in ovas and ep.get('type') == 0:
                    ovas[ep['subject_id']].setdefault('ova_episode_records', []).append({'date':ep.get('airdate'), 'minutes':duration_minutes(ep.get('duration'))})
    write_json(output, {'snapshot_at': meta['name'][5:15], 'source': 'bangumi/Archive', 'mode': 'archive',
        'source_url': meta['browser_download_url'], 'sha256': digest.hexdigest(),
        'subjects': subjects, 'relations': relations,
        'book_records_scanned':len(books), 'adaptation_links':len(set(adaptations))})
    print(f'Normalized {len(subjects)} anime; {len(relations)} relevant relationships.', flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', default='.cache/latest.json')
    p.add_argument('--cache', default='.cache/downloads')
    p.add_argument('--before', help='Choose an actual available archive on/before YYYY-MM-DD')
    p.add_argument('--sample', type=int, help='Small API sample for debugging only')
    a = p.parse_args()
    if a.sample:
        api_sample(a.output, a.cache, a.sample)
    else:
        archive(a.output, a.cache, a.before)
