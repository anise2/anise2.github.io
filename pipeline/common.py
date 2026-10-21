import csv
import gzip
import html
import json
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SEQUEL, PREQUEL = 3, 2  # bangumi/common subject_relations.yml, anime
TV_PLATFORMS = {'TV', 'WEB'}
BLOCK_TAG = re.compile(r'续[作集篇]|第[二三四五六七八九十\d]+季|[2-9]nd|[2-9]rd|[2-9]th|sequel|season\s*[2-9]|已定档|制作决定', re.I)
DATE_TAG = re.compile(r'^(?:19|20)\d{2}(?:年|年?(?:\d{1,2}|[一二三四五六七八九十]+)月(?:新?番)?|[./-]\d{1,2}|年?[春夏秋冬](?:季)?(?:新?番)?)?$')
DECADE_TAG = re.compile(r'^(?:19|20)\d0(?:s|年代)$',re.I)
COMMUNITY_FEATURES = {'score', 'score_missing', 'log_votes', 'log_wish', 'log_collect', 'log_doing', 'log_on_hold', 'log_dropped'}


def parse_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def add_year(day):
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def tags_of(raw):
    if isinstance(raw, dict):
        return list(raw)
    return [str(t.get('name', '')) if isinstance(t, dict) else str(t) for t in (raw or [])]


def infobox_value(info, key):
    if isinstance(info, list):
        value = next((x.get('value') for x in info if x.get('key') == key), '')
        return str(value) if not isinstance(value, list) else ' / '.join(str(x.get('v', '')) for x in value)
    match = re.search(r'\|[ \t]*' + re.escape(key) + r'[ \t]*=[ \t]*([^\n}]*)', str(info or ''))
    return match[1].strip() if match else ''


def normalize_subject(raw):
    rating = raw.get('rating') or {}
    tags = list(dict.fromkeys(tags_of(raw.get('tags')) + tags_of(raw.get('meta_tags'))))
    platform = {1: 'TV', 2: 'OVA', 3: '剧场版', 5: 'WEB'}.get(raw.get('platform'), str(raw.get('platform', '')))
    if platform.lower() == 'web':
        platform = 'WEB'
    if platform.lower() == 'movie':
        platform = '剧场版'
    info = raw.get('infobox', '')
    origin = '未知'
    for name, aliases in [('漫画改', ('漫画改', '漫改')), ('小说改', ('小说改', '轻小说改', '轻改')), ('原创', ('原创', '原創')), ('游戏改', ('游戏改', '游戏改编'))]:
        if any(t in tags for t in aliases):
            origin = name
            break
    country = infobox_value(info, '制片国家/地区') or infobox_value(info, '国家/地区')
    jp = '日本' in tags or '日本' in country
    inferred = False
    if not jp and not any(t in tags for t in ['中国', '美国', '国产', '欧美', '韩国']):
        jp = bool(re.search(r'[ぁ-ゖァ-ヺ]', raw.get('name', '')))
        inferred = jp
    episodes = raw.get('total_episodes') or raw.get('eps') or infobox_value(info, '话数')
    try:
        episodes = int(episodes)
    except (ValueError, TypeError):
        episodes = None
    duration_text = infobox_value(info, '单集片长') or infobox_value(info, '片长') or infobox_value(info, '播放时长')
    duration_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:分钟|分|min)', duration_text, re.I)
    collection = raw.get('collection') or raw.get('favorite') or {}
    if isinstance(collection, list):
        collection = {str(x.get('type')): x.get('count', 0) for x in collection if isinstance(x, dict)}
    score_details = raw.get('score_details') or rating.get('count') or {}
    votes = rating.get('total') or sum(float(v or 0) for v in score_details.values())
    return {
        'id': int(raw['id']), 'name': html.unescape(raw.get('name', '')),
        'name_cn': html.unescape(raw.get('name_cn', '') or raw.get('name', '')),
        'date': str(raw.get('date') or ''), 'platform': platform, 'tags': tags,
        'meta_tags': tags_of(raw.get('meta_tags')), 'score': raw.get('score', rating.get('score')),
        'rank': raw.get('rank', rating.get('rank')), 'votes': int(votes),
        'collection': collection, 'episodes': episodes, 'source': origin,
        'studio': infobox_value(info, '动画制作'), 'japanese': jp, 'japanese_inferred': inferred,
        'original_credit': infobox_value(info, '原作'),
        'duration_minutes': float(duration_match[1]) if duration_match else None,
        'nsfw': bool(raw.get('nsfw')), 'image': (raw.get('images') or {}).get('common', ''),
    }


def numeric_count(text):
    """Accept a single count, never a date, range or unrelated number."""
    text = str(text or '').strip().translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    match = re.match(r'^(?:全|既刊|已出版|共)?\s*(\d+)\s*(?:[卷巻册冊话話回章]|$)', text)
    return int(match[1]) if match else None


def wiki_date(text):
    match = re.search(r'(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})', str(text or ''))
    return parse_date(f'{int(match[1]):04}-{int(match[2]):02}-{int(match[3]):02}') if match else parse_date(text)


def normalize_book(raw):
    info = raw.get('infobox', '')
    volumes = infobox_value(info, '册数') or infobox_value(info, '卷数')
    chapters = infobox_value(info, '话数')
    ended = infobox_value(info, '连载结束') or infobox_value(info, '结束')
    status_text = ' '.join([volumes, chapters, ended, *tags_of(raw.get('meta_tags')), *tags_of(raw.get('tags'))])
    ended_at = wiki_date(ended)
    ended_year = re.search(r'((?:19|20)\d{2})',ended)
    # A magazine issue/year can prove the serialization ended, without inventing
    # an exact calendar date. Keep that original text for display.
    completed = bool(ended_at or ended_year or re.search(r'已完结|已完結|全\s*\d+\s*[卷巻册冊]|\bComplete(?:d)?\b', status_text, re.I))
    ongoing = bool(re.search(r'连载中|連載中|未完结|未完結|既刊|ongoing', status_text, re.I))
    return {'id':int(raw['id']), 'name':raw.get('name',''), 'name_cn':raw.get('name_cn') or raw.get('name',''),
        'kind':{1001:'漫画',1002:'小说'}.get(raw.get('platform'),'书籍'), 'series':bool(raw.get('series')),
        'date':str(raw.get('date') or ''), 'volumes':numeric_count(volumes), 'chapters':numeric_count(chapters),
        'status':'completed' if completed else 'ongoing' if ongoing else 'unknown',
        'ended_at':ended_at.isoformat() if ended_at else None,
        'ended_year':int(ended_year[1]) if ended_year else None,
        'evidence':{'volumes':volumes,'chapters':chapters,'ended':ended},
        'url':f'https://bgm.tv/subject/{raw["id"]}'}


def original_for(s, members=None):
    candidates = s.get('originals', []) or [b for m in (members or []) for b in m.get('originals', [])]
    expected = {'漫画改':'漫画', '小说改':'小说'}.get(s.get('source'))
    candidates = [b for b in candidates if not expected or b['kind'] == expected]
    if s.get('source') not in ('漫画改','小说改'):
        return None  # A cross-media relation alone does not tell adaptation direction.
    return max(candidates, key=lambda b:(b.get('series',False), b.get('volumes') is not None, -(b['id'])), default=None)


def completion_evidence(members, cutoff, upcoming=False):
    if upcoming:
        return None
    # Generic 完结 / 放送结束 describes a broadcast run, not a finished franchise.
    explicit = {'系列完结','系列完結','系列已完结','全系列完结','故事完结','故事完結','原作完全改编','完全改编','完整改编'}
    for s in sorted(members, key=lambda t:t.get('date') or '', reverse=True):
        if not parse_date(s.get('date')) or parse_date(s['date']) > cutoff:
            continue
        proof=s.get('story_completion') or {}
        if parse_date(proof.get('observed_at')) and parse_date(proof['observed_at']) <= cutoff:
            return proof
        matches = explicit.intersection(s.get('tags', []))
        if matches:
            return {'reason':'明确系列标签：' + '、'.join(sorted(matches)), 'url':f'https://bgm.tv/subject/{s["id"]}'}
    regular = sorted([s for s in members if is_tv(s) and parse_date(s.get('date')) and parse_date(s['date']) <= cutoff],key=lambda s:s['date'])
    if regular:
        latest = regular[-1]
        title = latest.get('name','') + ' ' + latest.get('name_cn','')
        original = original_for(latest,members) or {}
        last = parse_date(latest.get('last_episode_date'))
        ended = bool(last and last <= cutoff and latest.get('dated_episodes',0) >= (latest.get('episodes') or 1))
        final_title = re.search(r'完结篇|完結編|最終章|最终章|\bTHE FINAL\b|\bFinal Stage\b|最終季|最终季',title,re.I)
        partial = re.search(r'前篇|前編|第一|Part\s*1\b|最終章\s*第\d',title,re.I)
        if final_title and not partial and ended and original.get('status') == 'completed':
            return {'reason':'终章标题 + 本季分集已播完 + 原作完结（规则识别）', 'url':f'https://bgm.tv/subject/{latest["id"]}'}
    return None


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')


def load_snapshot(path):
    if str(path).endswith('.gz'):
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    return json.loads(Path(path).read_text(encoding='utf-8'))


def relation_graph(snapshot):
    subjects = {s['id']: dict(s) for s in snapshot['subjects']}
    for row in snapshot['relations']:
        rel = int(row['relation_type'])
        excluded = row['related_subject_id'] if rel in (4, 6) else row['subject_id'] if rel == 12 else None
        if excluded in subjects:
            subjects[excluded]['excluded_regular'] = True
            subjects[excluded]['excluded_kind'] = 'summary' if rel == 4 else 'side_story'
    edges = set()
    for row in snapshot['relations']:
        a, b, rel = int(row['subject_id']), int(row['related_subject_id']), int(row['relation_type'])
        if a not in subjects or b not in subjects:
            continue
        if rel == SEQUEL:
            edges.add((a, b))
        elif rel == PREQUEL:
            edges.add((b, a))
    successors, predecessors = defaultdict(set), defaultdict(set)
    parent = {i: i for i in subjects}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in sorted(edges):
        successors[a].add(b)
        predecessors[b].add(a)
        parent[root(b)] = root(a)
    groups = defaultdict(list)
    for i in subjects:
        groups[root(i)].append(i)
    group_ids = {i: min(ids) for ids in groups.values() for i in ids}
    return subjects, successors, predecessors, group_ids


def is_tv(s):
    summary = any(t in s.get('tags', []) for t in ('总集篇', '总集编', '総集編')) or re.search(r'总集[篇编]|総集編|特别编辑版|TV剪辑版', s.get('name_cn', '') + s.get('name', ''))
    return bool(s['platform'] in TV_PLATFORMS and s.get('japanese') and not s.get('nsfw') and not s.get('excluded_regular') and not summary)


def first_season(s, subjects, predecessors):
    # Traverse intervening movies/OVAs, not just the immediate predecessor.
    seen, todo = set(), list(predecessors[s['id']])
    while todo:
        i = todo.pop()
        if i in seen:
            continue
        seen.add(i)
        other = subjects[i]
        if is_tv(other) and (other.get('date') or '9999') < (s.get('date') or '9999'):
            return False
        todo.extend(predecessors[i])
    return True


def sequels_of(sid, subjects, successors):
    result, seen, todo = [], {sid}, list(successors[sid])
    while todo:
        i = todo.pop()
        if i in seen:
            continue
        seen.add(i)
        if is_tv(subjects[i]):
            result.append(subjects[i])
        else:
            todo.extend(successors[i])
    return result


def read_enrichment(path):
    rows = defaultdict(list)
    if not Path(path).exists():
        return rows
    with open(path, encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            if not row['subject_id']:
                continue
            if not parse_date(row['observed_at']) or not re.match(r'^https?://', row['source_url']):
                raise ValueError('Enrichment needs observed_at and an http(s) source_url')
            for k in ('bd_copies', 'source_total_units', 'adapted_units'):
                row[k] = float(row[k]) if row.get(k) else None
                if row[k] is not None and (not math.isfinite(row[k]) or row[k] < 0):
                    raise ValueError(f'Invalid enrichment {k}')
            if row['bd_copies'] is not None and row.get('bd_scope') != 'volume1_first_week':
                raise ValueError('bd_copies must use volume1_first_week for comparable observations')
            if row['adapted_units'] is not None and row['source_total_units'] is not None:
                if row['source_total_units'] <= 0 or row['adapted_units'] > row['source_total_units'] or not row['unit']:
                    raise ValueError('Adaptation units must use the same named unit, with adapted <= total')
            rows[int(row['subject_id'])].append(row)
    return rows


def enrichment_at(rows, sid, cutoff):
    eligible = [r for r in rows.get(sid, []) if parse_date(r['observed_at']) <= cutoff]
    return max(eligible, key=lambda x: x['observed_at']) if eligible else {}


def evidence_for(s, cutoff, rows, members=None):
    e = dict(enrichment_at(rows,s['id'],cutoff))
    records=[s.get(field) or {} for field in ('disc_evidence','recent_disc_evidence','adaptation_evidence')]+s.get('disc_observations',[])
    for record in sorted(records,key=lambda r:r.get('observed_at','')):
        observed = parse_date(record.get('observed_at'))
        if observed and observed <= cutoff:
            e.update(record)
    if members:
        previous=sorted([m for m in members if is_tv(m) and m['id'] != s['id'] and m.get('date') and m['date'] < s.get('date','')],key=lambda m:m['date'],reverse=True)
        for m in previous:
            prior=evidence_for(m,cutoff,rows)
            if prior.get('disc_average') is not None:
                e['previous_disc']={k:v for k,v in prior.items() if k.startswith('disc_') or k=='observed_at'}
                e['previous_disc']['subject_id']=m['id']
                break
    return e


def features(s, cutoff, enrichment, members=None):
    airdate = parse_date(s['date'])
    c = s.get('collection', {})
    f = {'score': float(s.get('score') or 0), 'score_missing': not bool(s.get('score')),
         'log_votes': math.log1p(s.get('votes', 0)), 'episodes': s.get('episodes') or 0,
         'episodes_missing': s.get('episodes') is None,
         'age_days': max(0, (cutoff - airdate).days) if airdate else 0,
         'source=' + s['source']: 1, 'platform=' + s['platform']: 1}
    year_tags = [int(t[:4]) for t in s['tags'] if DATE_TAG.fullmatch(t)]
    year = airdate.year if airdate else (year_tags[0] if len(set(year_tags)) == 1 else 0)
    decades = {int(t[:4]) for t in s['tags'] if DECADE_TAG.fullmatch(t)}
    decade=year//10*10 if year else next(iter(decades)) if len(decades)==1 else 0
    f.update({'release_year':year, 'release_decade':decade, 'release_month':airdate.month if airdate else 0, 'release_year_missing':not bool(year)})
    for key, aliases in {'wish': ('wish', '1'), 'collect': ('collect', 'done', '2'), 'doing': ('doing', '3'), 'on_hold': ('on_hold', '4'), 'dropped': ('dropped', '5')}.items():
        f['log_' + key] = math.log1p(max(0, float(next((c[a] for a in aliases if a in c), 0))))
    for t in s['tags']:
        if not BLOCK_TAG.search(t) and not DATE_TAG.fullmatch(t) and not DECADE_TAG.fullmatch(t) and not re.search(r'完结|完結|完全改编|完整改编', t):
            f['tag=' + t] = 1
    e = evidence_for(s, cutoff, enrichment, members)
    bd = e.get('bd_copies')
    total, adapted = e.get('source_total_units'), e.get('adapted_units')
    f.update({'bd_missing': bd is None, 'log_bd_copies': math.log1p(bd) if bd is not None else 0,
              'remaining_missing': total is None or adapted is None,
              'source_remaining_ratio': (total - adapted) / total if total and adapted is not None else 0})
    average = e.get('disc_average')
    endpoint, published = e.get('adaptation_endpoint'), e.get('adaptation_total')
    f.update({'disc_average_missing':average is None,'log_disc_average':math.log1p(average) if average is not None else 0,
        'adaptation_endpoint_missing':endpoint is None or not published,
        'adaptation_endpoint_ratio':endpoint / published if published and endpoint is not None else 0})
    previous_average=e.get('previous_disc',{}).get('disc_average')
    f.update({'previous_disc_missing':previous_average is None,
        'log_previous_disc_average':math.log1p(previous_average) if previous_average is not None else 0})
    progress = season_progress(members or [s], cutoff)
    f['regular_seasons'] = progress['regular']
    f['season_equivalent'] = progress['equivalent']
    f['ova_duration_missing'] = progress['ova_unverified'] > 0
    original = original_for(s, members) or {}
    ended = parse_date(original.get('ended_at'))
    status = original.get('status', 'unknown')
    if ended and ended > cutoff:
        status = 'ongoing'
    elif not ended and original.get('ended_year') and original['ended_year'] >= cutoff.year:
        status = 'unknown'  # An issue/year alone cannot locate the historical day.
    f.update({'source_volumes':original.get('volumes') or 0, 'source_volumes_missing':original.get('volumes') is None,
        'source_chapters':original.get('chapters') or 0, 'source_chapters_missing':original.get('chapters') is None,
        'source_completed':status == 'completed', 'source_ongoing':status == 'ongoing', 'source_status_missing':status == 'unknown'})
    # Only already-aired members enter cadence features. Never count future
    # relations, future releases, or the eventual size of a franchise.
    aired = sorted([m for m in (members or [s]) if parse_date(m.get('date')) and parse_date(m['date']) <= cutoff], key=lambda m:m['date'])
    regular = [m for m in aired if is_tv(m)]
    dates = [parse_date(m['date']) for m in regular]
    gaps = [(b-a).days for a,b in zip(dates,dates[1:])]
    mean_gap = sum(gaps)/len(gaps) if gaps else 0
    age = f['age_days']
    f.update({'log_age_days':math.log1p(age), 'series_age_days':(cutoff-dates[0]).days if dates else age,
        'previous_gap_days':gaps[-1] if gaps else 0, 'mean_gap_days':mean_gap,
        'cadence_missing':not bool(gaps), 'elapsed_gap_ratio':min(age/mean_gap,20) if mean_gap else 0,
        'seasons_last_3y':sum((cutoff-d).days <= 1095 for d in dates),
        'past_series_episodes':sum(m.get('episodes') or 0 for m in regular),
        'recent_extra_count':sum(not is_tv(m) and (cutoff-parse_date(m['date'])).days <= 730 for m in aired),
        'last_activity_days':(cutoff-parse_date(aired[-1]['date'])).days if aired else age,
        'runtime_minutes':s.get('duration_minutes') or 0,
        'volumes_per_season':(original.get('volumes') or 0)/max(progress['regular'],1)})
    if s.get('studio'):
        f['studio='+s['studio']] = 1
    # Ratios are auxiliary signals too; the model may include the entire group.
    counts = {k: math.expm1(f['log_'+k]) for k in ('wish','collect','doing','on_hold','dropped')}
    audience = sum(counts.values())
    for k in ('wish','collect','on_hold','dropped'):
        f['audience_ratio_'+k] = counts[k]/max(audience,1)
    return f


FEATURE_LABELS = {'score': 'Bangumi 评分', 'score_missing': '评分缺失', 'log_votes': '评分人数',
    'regular_seasons': '当前正式季数', 'season_equivalent': '季度进度（含 OVA）', 'ova_duration_missing': 'OVA 时长待核实',
    'episodes': '集数', 'episodes_missing': '集数缺失', 'age_days': '首播距截点天数',
    'log_wish': '想看人数', 'log_collect': '看过人数', 'log_doing': '在看人数',
    'log_on_hold': '搁置人数', 'log_dropped': '抛弃人数', 'log_bd_copies': 'BD 首卷首周销量',
    'bd_missing': 'BD 销量缺失', 'source_remaining_ratio': '原作剩余比例', 'remaining_missing': '改编进度缺失'}
FEATURE_LABELS.update({'release_year':'首播年份（数值）','release_decade':'首播年代（数值）','release_month':'首播月份（数值）','release_year_missing':'首播年份缺失',
    'source_volumes':'原作已出版卷数','source_volumes_missing':'原作卷数缺失','source_chapters':'原作话数',
    'source_chapters_missing':'原作话数缺失','source_completed':'原作已完结','source_ongoing':'原作连载中','source_status_missing':'原作状态未知'})
FEATURE_LABELS.update({'disc_average_missing':'碟片卷均销量缺失','log_disc_average':'碟片卷均销量（BD + DVD）',
    'adaptation_endpoint_missing':'改编终点比例缺失','adaptation_endpoint_ratio':'改编终点 / 原作出版量',
    'previous_disc_missing':'前作碟片卷均缺失','log_previous_disc_average':'前作碟片卷均销量'})
FEATURE_LABELS.update({'log_age_days':'等待时间（对数）','series_age_days':'系列已持续天数',
    'previous_gap_days':'上次续季间隔','mean_gap_days':'历史平均续季间隔','cadence_missing':'续季间隔未知',
    'elapsed_gap_ratio':'等待时间 / 历史续季间隔','seasons_last_3y':'近三年已播季数',
    'past_series_episodes':'历季集数总量','recent_extra_count':'近两年已播衍生作品数',
    'last_activity_days':'距最近作品首播天数','runtime_minutes':'单集时长',
    'volumes_per_season':'出版卷数 / 已播季数（非改编率）',
    'audience_ratio_wish':'想看人数占比','audience_ratio_collect':'看过人数占比',
    'audience_ratio_on_hold':'搁置人数占比','audience_ratio_dropped':'抛弃人数占比'})
FEATURE_LABELS.update({'release_quarter':'首播季度','month_sin':'首播月份周期（sin）','month_cos':'首播月份周期（cos）',
    'gap_variation_days':'历史续季间隔极差','prior_year_releases':'首播前一年正篇发行量',
    'log_disc_average_missing':'当时碟片卷均缺失','log_previous_disc_average_missing':'当时前作销量缺失','log_bd_copies_missing':'当时首卷首周销量缺失'})
FEATURE_LABELS.update({'source_age_days':'首播时原作已发行天数','source_age_missing':'原作发行日期未知或不适用',
    'studio_missing':'制作公司署名未知','studio_prior_releases':'制作公司此前正篇发行量',
    'studio_prior_year_releases':'制作公司此前一年发行量'})
COMMUNITY_FEATURES.update(k for k in FEATURE_LABELS if k.startswith('audience_ratio_'))


def feature_label(key):
    for prefix, label in [('tag=', '标签：'), ('source=', '原作：'), ('platform=', '形式：'), ('studio=', '制作公司：')]:
        if key.startswith(prefix):
            return label + key[len(prefix):]
    return FEATURE_LABELS.get(key, key)


def season_progress(members, cutoff):
    aired = [s for s in members if parse_date(s.get('date')) and parse_date(s['date']) <= cutoff]
    regular = sum(is_tv(s) for s in aired)
    ova_eps, pending = 0, 0
    for s in aired:
        if s['platform'] != 'OVA':
            continue
        if s.get('excluded_kind') == 'summary' or any(t in s.get('tags', []) for t in ('总集篇','総集編')):
            continue
        if s.get('ova_episode_records'):
            ova_eps += sum(bool(parse_date(ep.get('date')) and parse_date(ep['date']) <= cutoff and (ep.get('minutes') or 0) >= 21) for ep in s['ova_episode_records'])
            pending += int(any(ep.get('minutes') is None or not parse_date(ep.get('date')) for ep in s['ova_episode_records']))
        elif s.get('duration_minutes') is not None and s.get('episodes') is not None:
            ova_eps += s['episodes'] if s['duration_minutes'] >= 21 else 0
        else:
            pending += 1
    return {'regular': regular, 'ova_episodes': ova_eps, 'equivalent': round(regular + ova_eps / 10, 1), 'ova_unverified': pending,
        'movies':sum(s['platform']=='剧场版' for s in aired),
        'summaries':sum(s.get('excluded_kind')=='summary' or any(t in s.get('tags',[]) for t in ('总集篇','总集编','総集編')) for s in aired),
        'side_stories':sum(s.get('excluded_kind')=='side_story' and s['platform'] not in ('OVA','剧场版') for s in aired)}


def duration_minutes(value):
    if not value:
        return None
    value = str(value).strip()
    parts = value.split(':')
    if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
        parts = [int(p) for p in parts]
        return parts[0] * 60 + parts[1] + parts[2] / 60 if len(parts) == 3 else parts[0] + parts[1] / 60
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:分钟|分|minutes?|mins?)', value, re.I)
    return float(match[1]) if match else None


def series_members(snapshot, subjects, groups):
    grouped = defaultdict(list)
    for sid, s in subjects.items():
        grouped[groups[sid]].append(s)
    for r in snapshot['relations']:
        a, b = r['subject_id'], r['related_subject_id']
        if a not in subjects or b not in subjects or r['relation_type'] not in (4, 6, 12):
            continue
        if r['relation_type'] in (4, 6) and subjects[b] not in grouped[groups[a]]:
            grouped[groups[a]].append(subjects[b])
        if r['relation_type'] == 12 and subjects[a] not in grouped[groups[b]]:
            grouped[groups[b]].append(subjects[a])
    return grouped
