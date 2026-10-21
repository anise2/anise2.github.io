"""Release events and reconstructed fixed attributes; dynamic values stay dated.

This is a strict event-time reconstruction, not a historical database snapshot.
Dates, formats, source types, credits and relation identities are reconstructed from today's archive;
announcement dates and old metadata revisions cannot be certified by it.
"""
import bisect
import math
import re
from collections import defaultdict
from pipeline.common import parse_date, is_tv, relation_graph, evidence_for

PROTOCOL = 'release_static_pruned_v3'
POLICY = {
    'id': PROTOCOL,
    'validation': 'temporal_release_events',
    'feature_selection': '每个训练截点内剔除常量和零贡献字段，再完整重训至稳定；等待时长通过生存公式保留。模型及特征组合只依据截点前验证选定。',
    'included': ['截点前已首播的正篇与有逐集日期的 OVA', '首播年月、季度与历史续季间隔', '固定原作类型：漫画、小说、原创、游戏、未知', '制作公司署名及该公司在本季首播前的发行量', '本季首播前已发行的原作年龄（不含卷数与完结状态）', '有观察日期且早于该季首播的前作销量'],
    'excluded': ['当前季销量（全部口径及缺失标记）', '当前 Bangumi / MAL 评分与收藏人数', '题材、评价、续作等当前用户标签（仅固定原作类型单独编码）', '无历史快照的原作卷数、完结状态与改编进度', '无逐集播出日期的 OVA 集数、当前季最终集数', '截点后发行作品、未来关系连接与未来销量'],
    'limitations': '严格截断发行事件和有日期的动态证据；发行日期、形式、关系、原作类型与制作署名由当前归档重建，假定这些固定作品属性在首播时已确定。无法认证当年数据库是否已收录或分类是否一致；因此不是完整历史快照回测，也不能验证未官宣续作预测。',
}

SOURCE_TYPES = ('漫画改','小说改','原创','游戏改','未知')


def studios_of(s):
    # Only fixed animation credits, never community company tags or popularity.
    credit=re.sub(r'\[[^\]]*\]|[（(][^）)]*[）)]','',s.get('studio') or '')
    return sorted({name.strip() for name in re.split(r'、|\s*/\s*|\s*[;；]\s*',credit) if name.strip()})


def fixed_features(s, air):
    source=s.get('source') if s.get('source') in SOURCE_TYPES else '未知'
    f={'source='+source:1}
    studios=studios_of(s)
    f.update({'studio='+name:1 for name in studios or ['未知']})
    f['studio_missing']=not bool(studios)
    # Do not borrow an original linked only to a later season. A cross-media
    # relation needs the matching source kind AND an earlier publication date.
    kind={'漫画改':'漫画','小说改':'小说'}.get(source)
    dates=[parse_date(b.get('date')) for b in s.get('originals',[]) if kind and b.get('kind')==kind]
    dates=[d for d in dates if d and d<air]
    f['source_age_days']=(air-min(dates)).days if dates else 0
    f['source_age_missing']=not bool(dates)
    return f


class Chronology:
    def __init__(self, snapshot):
        self.subjects, self.next, _, self.groups = relation_graph(snapshot)
        self.links = defaultdict(set)
        self.extras = defaultdict(set)
        for r in snapshot['relations']:
            a,b,kind=r['subject_id'],r['related_subject_id'],r['relation_type']
            if a not in self.subjects or b not in self.subjects: continue
            if kind in (2,3):
                self.links[a].add(b); self.links[b].add(a)
            elif kind in (4,6): self.extras[a].add(b)
            elif kind==12: self.extras[b].add(a)
        self.releases=sorted(parse_date(s['date']) for s in self.subjects.values() if is_tv(s) and parse_date(s.get('date')))
        self.studio_releases=defaultdict(list)
        for s in self.subjects.values():
            day=parse_date(s.get('date'))
            if is_tv(s) and day:
                for studio in studios_of(s):self.studio_releases[studio].append(day)
        for days in self.studio_releases.values():days.sort()

    def members(self, sid, cutoff):
        seen=set(); todo=[sid]
        while todo:
            i=todo.pop()
            day=parse_date(self.subjects[i].get('date'))
            if i in seen or not day or day>cutoff: continue
            seen.add(i);todo.extend(self.links[i]-seen)
        extra={i for s in seen for i in self.extras[s] if parse_date(self.subjects[i].get('date')) and parse_date(self.subjects[i]['date'])<=cutoff}
        return [self.subjects[i] for i in sorted(seen|extra)]

    def features(self, sid, cutoff, enrichment):
        s=self.subjects[sid]; air=parse_date(s['date'])
        f=event_features(s, cutoff, enrichment, self.members(sid,air))
        from datetime import timedelta
        # Left bound excludes future and same-day entries, which may have unknown order.
        f['prior_year_releases']=bisect.bisect_left(self.releases,air)-bisect.bisect_left(self.releases,air-timedelta(days=365))
        counts=[(bisect.bisect_left(self.studio_releases[name],air),
                 bisect.bisect_left(self.studio_releases[name],air)-bisect.bisect_left(self.studio_releases[name],air-timedelta(days=365)))
                for name in studios_of(s)]
        # Max avoids counting a co-produced work twice; same-day releases are excluded.
        f['studio_prior_releases']=max((c[0] for c in counts),default=0)
        f['studio_prior_year_releases']=max((c[1] for c in counts),default=0)
        return f


def event_features(s, cutoff, enrichment, members):
    air=parse_date(s['date'])
    aired=[m for m in members if parse_date(m.get('date')) and parse_date(m['date'])<=air]
    regular=sorted([m for m in aired if is_tv(m)],key=lambda m:(m['date'],m['id']))
    dates=[parse_date(m['date']) for m in regular]
    gaps=[(b-a).days for a,b in zip(dates,dates[1:])]
    ova=sum(1 for m in aired if m['platform']=='OVA' and m.get('excluded_kind')!='summary'
        for ep in m.get('ova_episode_records',[]) if parse_date(ep.get('date')) and parse_date(ep['date'])<=air and (ep.get('minutes') or 0)>=21)
    f={'age_days':max(0,(cutoff-air).days),'release_year':air.year,'release_month':air.month,
       'release_quarter':(air.month-1)//3+1,'month_sin':math.sin(2*math.pi*(air.month-1)/12),'month_cos':math.cos(2*math.pi*(air.month-1)/12),
       'platform='+s['platform']:1,'regular_seasons':len(regular),'season_equivalent':len(regular)+ova/10,
       'series_age_days':(air-dates[0]).days if dates else 0,'previous_gap_days':gaps[-1] if gaps else 0,
       'mean_gap_days':sum(gaps)/len(gaps) if gaps else 0,'cadence_missing':not bool(gaps),
       'gap_variation_days':float(max(gaps)-min(gaps)) if gaps else 0,
       'seasons_last_3y':sum((air-d).days<=1095 for d in dates),
       'recent_extra_count':sum(not is_tv(m) and (air-parse_date(m['date'])).days<=730 for m in aired)}
    f.update(fixed_features(s,air))
    # Evidence keeps its original observation date; newly scraped numbers never
    # get an old date simply because they describe an old anime.
    e=evidence_for(s,air,enrichment,aired)
    previous=e.get('previous_disc',{})
    recent_previous=previous.get('disc_scope')=='bd_dvd_known_volume_average'
    # Keep each sales convention explicit. DictVectorizer fills absent keys
    # with 0, which would otherwise turn an absent *_missing flag into "known".
    for name in ('log_previous_disc_average','log_previous_recent_disc_average'):
        f[name]=0
        f[name+'_missing']=True
    name='log_previous_recent_disc_average' if recent_previous else 'log_previous_disc_average'
    value=previous.get('disc_average')
    f[name]=math.log1p(value) if value is not None else 0
    f[name+'_missing']=value is None
    # Current-season sales are outside the feature policy. Unavailable source
    # capacity fields are removed rather than generated as all-unknown columns.
    return f
