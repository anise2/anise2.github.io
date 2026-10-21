import copy
import unittest
from datetime import date
from pipeline.temporal import Chronology
from pipeline.survival import season_rows, risk_rows
from pipeline.supplement import parse_sales, sales_key
from pipeline.common import season_progress
from test_pipeline import subject,snapshot

class TimeControlsTests(unittest.TestCase):
    def test_previous_sales_unknown_differs_from_a_genuine_zero(self):
        from unittest.mock import patch
        from pipeline.temporal import event_features
        a=subject(1,'2010-01-01')
        with patch('pipeline.temporal.evidence_for',return_value={}):
            missing=event_features(a,date(2015,1,1),{},[a])
        with patch('pipeline.temporal.evidence_for',return_value={'previous_disc':{'disc_average':0}}):
            zero=event_features(a,date(2015,1,1),{},[a])
        self.assertEqual(missing['log_previous_disc_average'],zero['log_previous_disc_average'])
        self.assertTrue(missing['log_previous_disc_average_missing'])
        self.assertFalse(zero['log_previous_disc_average_missing'])

    def test_current_sales_and_unavailable_capacity_are_not_features(self):
        from unittest.mock import patch
        from pipeline.temporal import event_features
        a=subject(1,'2010-01-01')
        with patch('pipeline.temporal.evidence_for',return_value={}):
            baseline=event_features(a,date(2015,1,1),{},[a])
        for scope in ['bd_dvd_known_volume_average','bd_dvd_volume_average']:
            with patch('pipeline.temporal.evidence_for',return_value={'disc_average':50000,'disc_scope':scope,'bd_copies':10000,'source_total_units':10,'adapted_units':5}):
                changed=event_features(a,date(2015,1,1),{},[a])
            self.assertEqual(changed,baseline)
        self.assertFalse(set(baseline)&{'log_disc_average','log_recent_disc_average','log_bd_copies','source_remaining_ratio','remaining_missing'})

    def test_current_mutable_metadata_cannot_change_past_features(self):
        a=subject(1,'2010-01-01',duration_minutes=24)
        old=snapshot([a],at='2026-10-20')
        changed=copy.deepcopy(old);s=changed['subjects'][0]
        s.update(score=9.9,votes=999999,collection={'collect':1000000},tags=['日本','战斗','青春'],
                 episodes=100,duration_minutes=99,
                 originals=[{'volumes':100,'status':'completed'}],mal_evidence={'score':10},external_anime={'tags':['hit']})
        self.assertEqual(season_rows(old,date(2015,12,31),{}),season_rows(changed,date(2015,12,31),{}))

    def test_source_categories_have_an_explicit_unknown_without_dynamic_tags(self):
        for source in ['漫画改','小说改','原创','游戏改','未知','unsupported']:
            s=subject(1,'2010-01-01',source=source)
            f=Chronology(snapshot([s])).features(1,date(2015,1,1),{})
            expected=source if source!='unsupported' else '未知'
            self.assertEqual({k:v for k,v in f.items() if k.startswith('source=')},{'source='+expected:1})
            self.assertFalse(any(k.startswith('tag=') for k in f))

    def test_original_age_ignores_future_publications_and_dynamic_book_state(self):
        s=subject(1,'2010-01-01',originals=[{'kind':'漫画','date':'2008-01-01','volumes':10,'status':'ongoing'}])
        f=Chronology(snapshot([s])).features(1,date(2015,1,1),{})
        self.assertEqual(f['source_age_days'],731)
        s['originals'][0].update(volumes=100,status='completed')
        s['originals'].append({'kind':'漫画','date':'2020-01-01'})
        self.assertEqual(f,Chronology(snapshot([s])).features(1,date(2015,1,1),{}))
        s['originals']=[{'kind':'漫画','date':'2020-01-01'}]
        self.assertTrue(Chronology(snapshot([s])).features(1,date(2015,1,1),{})['source_age_missing'])

    def test_studio_activity_excludes_current_same_day_and_future_releases(self):
        items=[subject(1,'2008-01-01',studio='A'),subject(2,'2009-10-01',studio='A'),
               subject(3,'2010-01-01',studio='A、B'),subject(4,'2010-01-01',studio='A'),subject(5,'2020-01-01',studio='A')]
        f=Chronology(snapshot(items)).features(3,date(2015,1,1),{})
        self.assertEqual(f['studio_prior_releases'],2)
        self.assertEqual(f['studio_prior_year_releases'],1)
        self.assertEqual(f['studio=A'],1);self.assertEqual(f['studio=B'],1)

    def test_future_bridge_cannot_merge_historical_candidates(self):
        a,b,f=subject(1,'2006-01-01'),subject(2,'2008-01-01'),subject(3,'2020-01-01')
        plain=snapshot([a,b]);future=snapshot([a,b,f],[(1,3,3),(2,3,3)],at='2026-10-20')
        self.assertEqual({r['id'] for r in risk_rows(future,date(2015,12,31),{})},{1,2})
        x=Chronology(plain).features(2,date(2015,12,31),{})
        y=Chronology(future).features(2,date(2015,12,31),{})
        self.assertEqual(x,y);self.assertEqual(y['regular_seasons'],1)

    def test_new_sales_of_an_old_anime_are_not_backdated(self):
        a=subject(1,'2010-01-01');b=subject(2,'2015-01-01')
        snap=snapshot([a,b],[(1,2,3)],at='2026-10-20')
        before=Chronology(snap).features(2,date(2020,1,1),{})
        a['recent_disc_evidence']={'disc_average':5000,'observed_at':'2026-10-21'}
        self.assertEqual(before,Chronology(snap).features(2,date(2020,1,1),{}))
        a['recent_disc_evidence']['observed_at']='2014-01-01'
        self.assertGreater(Chronology(snap).features(2,date(2020,1,1),{})['log_previous_disc_average'],0)

    def test_later_sales_revision_preserves_earlier_observation(self):
        a=subject(1,'2010-01-01');b=subject(2,'2015-01-01')
        a['recent_disc_evidence']={'disc_average':9000,'observed_at':'2026-10-21','disc_scope':'bd_dvd_known_volume_average'}
        a['disc_observations']=[{'disc_average':1200,'observed_at':'2014-01-01','disc_scope':'bd_dvd_known_volume_average'}]
        f=Chronology(snapshot([a,b],[(1,2,3)])).features(2,date(2020,1,1),{})
        import math
        self.assertAlmostEqual(f['log_previous_recent_disc_average'],math.log1p(1200))
        self.assertEqual(f['log_previous_disc_average'],0)
        self.assertTrue(f['log_previous_disc_average_missing'])
        self.assertFalse(f['log_previous_recent_disc_average_missing'])

    def test_ova_aggregate_does_not_claim_future_episodes_already_aired(self):
        a=subject(1,'2010-01-01');o=subject(2,'2011-01-01','OVA',episodes=20,duration_minutes=25)
        b=subject(3,'2012-01-01')
        snap=snapshot([a,o,b],[(1,2,3),(2,3,3)],at='2026-10-20')
        self.assertEqual(Chronology(snap).features(3,date(2015,1,1),{})['season_equivalent'],2)
        o['ova_episode_records']=[{'date':'2011-01-01','minutes':25},{'date':'2013-01-01','minutes':25}]
        self.assertEqual(Chronology(snap).features(3,date(2015,1,1),{})['season_equivalent'],2.1)

    def test_sales_unknown_is_not_zero_and_cinema_table_is_not_disc_data(self):
        html='<table><tr><th>累平</th><th>季</th><th>タイトル</th></tr><tr><td>1,234</td><td>春</td><td>A</td></tr><tr><td>不明</td><td>夏</td><td>B</td></tr></table><table><tr><td>100億</td><td>Film</td></tr></table>'
        self.assertEqual(parse_sales(html),[{'value':1234,'season':'春','title':'A'}])
        self.assertEqual(sales_key('タイトル（2期）'),sales_key('タイトル 第2期'))
        with self.assertRaises(ValueError):parse_sales('<html>Blocked</html>')

    def test_detail_counts_list_extras_without_changing_regular_seasons(self):
        items=[subject(1),subject(2,platform='剧场版'),subject(3,excluded_kind='summary',excluded_regular=True),subject(4,excluded_kind='side_story',excluded_regular=True)]
        p=season_progress(items,date(2024,1,1))
        self.assertEqual((p['regular'],p['movies'],p['summaries'],p['side_stories']),(1,1,1,1))

if __name__=='__main__':unittest.main()
