import tempfile
import math
import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path

import numpy as np
from pipeline.common import (duration_minutes, enrichment_at, features, is_tv, normalize_subject, read_enrichment,
    relation_graph, season_progress, sequels_of, series_members, normalize_book, original_for, completion_evidence, infobox_value)
from pipeline.train import choose_split, explain, fit, prospective_rows, retrospective_rows
from pipeline.enrich import parse_endpoint, MetadataReader, validate_raw_sales


def subject(sid, aired='2023-06-01', platform='TV', **kwargs):
    return {'id':sid,'name':f'テスト{sid}','name_cn':f'测试{sid}','date':aired,'platform':platform,
            'japanese':True,'nsfw':False,'tags':['日本','漫画改','青春'],'source':'漫画改',
            'score':7.5,'votes':100,'collection':{},'episodes':12,'duration_minutes':None, **kwargs}


def snapshot(items, relations=(), at='2023-12-31'):
    return {'subjects':items,'relations':[{'subject_id':a,'related_subject_id':b,'relation_type':r} for a,b,r in relations], 'snapshot_at':at,'mode':'archive'}


class LabelTests(unittest.TestCase):
    def test_ova_fraction_is_not_a_new_season_label(self):
        first = subject(1)
        ova = subject(2,'2024-02-01','OVA',episodes=2,duration_minutes=21)
        before = snapshot([first])
        after = snapshot([first,ova],[(1,2,3)],at='2024-12-31')
        self.assertEqual(prospective_rows(before,after,{})[0]['y'],0)
        progress = season_progress([first,ova],date(2024,12,31))
        self.assertEqual(progress['equivalent'],1.2)
        self.assertEqual(progress['regular'],1)

    def test_summary_and_side_story_are_not_regular_seasons(self):
        snap = snapshot([subject(1),subject(2),subject(3)],[(1,2,4),(1,3,6)])
        subjects, _, _, groups = relation_graph(snap)
        self.assertTrue(is_tv(subjects[1]))
        self.assertFalse(is_tv(subjects[2]))
        self.assertFalse(is_tv(subjects[3]))
        self.assertEqual(season_progress(list(subjects.values()),date(2024,1,1))['regular'],1)

    def test_next_season_not_only_second_season(self):
        one,two,three = subject(1,'2021-01-01'),subject(2,'2023-06-01'),subject(3,'2024-05-01')
        before = snapshot([one,two],[(1,2,3)])
        after = snapshot([one,two,three],[(1,2,3),(2,3,3)],at='2024-12-31')
        rows = prospective_rows(before,after,{})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],2)
        self.assertEqual(rows[0]['y'],1)
        self.assertEqual(rows[0]['features']['regular_seasons'],2)

    def test_known_planned_sequel_is_not_a_prediction(self):
        first,next_one = subject(1),subject(2,'2024-06-01')
        before = snapshot([first,next_one],[(1,2,3)])
        after = snapshot([first,next_one],[(1,2,3)],at='2024-12-31')
        self.assertEqual(prospective_rows(before,after,{}),[])

    def test_censored_outcome_is_not_negative(self):
        before = snapshot([subject(1)])
        self.assertEqual(prospective_rows(before,snapshot([subject(1)],at='2024-10-01'),{}),[])

    def test_reverse_relation_and_ova_chain(self):
        snap = snapshot([subject(1),subject(2,'2024-01-01','OVA'),subject(3,'2025-01-01')],[(2,1,2),(2,3,3)])
        subjects, successor, _, groups = relation_graph(snap)
        self.assertEqual([s['id'] for s in sequels_of(1,subjects,successor)],[3])
        self.assertEqual(groups[1],groups[3])

    def test_unknown_ova_duration_never_fabricates_progress(self):
        progress = season_progress([subject(1),subject(2,platform='OVA',episodes=2)],date(2025,1,1))
        self.assertEqual(progress['equivalent'],1.0)
        self.assertEqual(progress['ova_unverified'],1)

    def test_short_ova_and_future_seasons_excluded_from_progress(self):
        progress = season_progress([subject(1),subject(2,platform='OVA',duration_minutes=20),subject(3,'2026-01-01')],date(2025,1,1))
        self.assertEqual(progress['equivalent'],1.0)

    def test_ova_per_episode_airdate_and_runtime(self):
        ova=subject(2,platform='OVA',ova_episode_records=[{'date':'2023-06-01','minutes':21},{'date':'2024-06-01','minutes':22},{'date':'2023-06-02','minutes':5}])
        self.assertEqual(season_progress([subject(1),ova],date(2023,12,31))['equivalent'],1.1)

    def test_summary_title_and_tag_excluded_without_relation(self):
        self.assertFalse(is_tv(subject(1,name_cn='特别编辑版')))
        self.assertFalse(is_tv(subject(1,tags=['总集篇'])))

    def test_extra_works_are_grouped_but_not_counted_as_seasons(self):
        snap=snapshot([subject(1),subject(2,platform='OVA',duration_minutes=21,episodes=2),subject(3)],[(1,2,4),(1,3,6)])
        subjects,_,_,groups=relation_graph(snap)
        members=series_members(snap,subjects,groups)[groups[1]]
        self.assertEqual(len(members),3)
        self.assertEqual(season_progress(members,date(2024,1,1))['equivalent'],1.0)


class LeakageTests(unittest.TestCase):
    def test_historical_scores_not_replaced_by_future(self):
        before = snapshot([subject(1,score=6)])
        after = snapshot([subject(1,score=9),subject(2,'2024-10-01')],[(1,2,3)],at='2024-12-31')
        row = prospective_rows(before,after,{})[0]
        self.assertEqual(row['features']['score'],6)
        self.assertEqual(row['y'],1)

    def test_future_enrichment_excluded_and_zero_sales_preserved(self):
        rows = {1:[{'observed_at':'2025-01-01','bd_copies':999}]}
        f = features(subject(1),date(2024,1,1),rows)
        self.assertTrue(f['bd_missing'])
        rows[1].append({'observed_at':'2023-12-01','bd_copies':0})
        f = features(subject(1),date(2024,1,1),rows)
        self.assertFalse(f['bd_missing'])
        self.assertEqual(f['log_bd_copies'],0)

    def test_label_revealing_tags_excluded(self):
        f = features(subject(1,tags=['青春','第二季','续作','2023年10月','制作决定']),date(2023,12,31),{})
        self.assertIn('tag=青春',f)
        for k in ['tag=第二季','tag=续作','tag=2023年10月','tag=制作决定']:
            self.assertNotIn(k,f)

    def test_group_and_time_separation(self):
        rows = [{'id':i,'group':i,'cutoff':'2021-12-31','outcome_end':'2022-12-31','y':i%2} for i in range(30)]
        rows += [{'id':100+i,'group':i,'cutoff':'2023-12-31','outcome_end':'2024-12-31','y':i%2} for i in range(8)]
        rows += [{'id':500,'group':500,'cutoff':'2023-01-01','outcome_end':'2024-01-01','y':1}]
        train,test,cutoff = choose_split(rows)
        self.assertTrue(train and test)
        self.assertFalse({r['group'] for r in train} & {r['group'] for r in test})
        self.assertTrue(all(r['outcome_end'] <= cutoff for r in train))
        self.assertNotIn(500,[r['id'] for r in train])

    def test_enrichment_requires_provenance_and_consistent_unit(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'enrichment.csv'
            f.write_text('subject_id,observed_at,source_url,bd_copies,bd_scope,source_total_units,adapted_units,unit\n1,2023-01-01,https://example.com,12,total,10,12,卷\n')
            with self.assertRaises(ValueError): read_enrichment(f)

    def test_tree_path_contributions_reconcile_to_probability(self):
        rows = [{'features':{'score':i%10,'season_equivalent':i%3+1,'tag=青春':i%2,'tag=rare':i==0},'y':int(i%5 < 2)} for i in range(50)]
        model,vectorizer = fit(rows,trees=12)
        X = vectorizer.transform([r['features'] for r in rows])
        baseline,contributions = explain(model,X)
        np.testing.assert_allclose(baseline+contributions.sum(axis=1),model.predict_proba(X)[:,1],atol=1e-8)
        self.assertTrue((model.predict_proba(X)[:,1] >= 0).all())


class NormalizeTests(unittest.TestCase):
    def test_blank_wiki_fields_do_not_consume_the_next_line(self):
        self.assertEqual(infobox_value('|册数=\n|页数=123','册数'),'')

    def test_year_tags_are_numeric_and_dates_win(self):
        s=subject(1,'2015-04-03',tags=['2015年','2015年4月','2015年四月番','2015-04','2010s','2015','青春'])
        f=features(s,date(2015,12,31),{})
        self.assertEqual(f['release_year'],2015)
        self.assertEqual(f['release_month'],4)
        self.assertEqual(f['release_decade'],2010)
        self.assertNotIn('tag=2010s',f)
        self.assertFalse(any(k.startswith('tag=2015') for k in f))
        s['date']='';s['tags']=['2015年']
        self.assertEqual(features(s,date(2016,1,1),{})['release_year'],2015)
        s['tags']=['2015年','2016年']
        self.assertTrue(features(s,date(2016,1,1),{})['release_year_missing'])

    def test_original_book_status_and_volume_count(self):
        book=normalize_book({'id':1,'name':'原作','platform':1001,'series':True,
            'infobox':'|册数=全27卷\n|话数=108\n|连载结束=2010年6月12日'})
        self.assertEqual((book['volumes'],book['chapters'],book['status']),(27,108,'completed'))
        anime=subject(2,originals=[book])
        f=features(anime,date(2009,12,31),{})
        self.assertFalse(f['source_completed'])
        self.assertTrue(f['source_ongoing'])
        self.assertIsNone(original_for(subject(3,source='原创',originals=[book])))

    def test_end_issue_is_completion_evidence_without_inventing_a_day(self):
        book=normalize_book({'id':3,'platform':1001,'infobox':'|连载结束=2015年3月号\n|册数=11'})
        self.assertEqual(book['status'],'completed')
        self.assertIsNone(book['ended_at'])
        self.assertEqual(book['ended_year'],2015)
        self.assertEqual(book['evidence']['ended'],'2015年3月号')

    def test_previous_disc_sales_remain_distinct_from_current_season(self):
        previous=subject(1,'2018-01-01',disc_evidence={'observed_at':'2020-10-13','disc_average':5000})
        current=subject(2,'2023-01-01')
        f=features(current,date(2023,12,31),{},[previous,current])
        self.assertTrue(f['disc_average_missing'])
        self.assertFalse(f['previous_disc_missing'])
        self.assertEqual(f['log_previous_disc_average'],math.log1p(5000))

    def test_single_season_and_source_completion_are_not_franchise_completion(self):
        book={'id':3,'kind':'漫画','status':'completed'}
        s=subject(1,tags=['已完结','完结'],originals=[book])
        self.assertIsNone(completion_evidence([s],date(2025,1,1)))
        s['tags']=['系列完结']
        self.assertIsNotNone(completion_evidence([s],date(2025,1,1)))
        self.assertIsNone(completion_evidence([s],date(2025,1,1),upcoming=True))

    def test_endpoint_is_matched_to_season_and_consistent_units(self):
        r={'anime':{'end':'Vol 7, Chap 60 (S1) / Vol 9, Chap 80 (S2)'}}
        self.assertEqual(parse_endpoint(r,{'volumes':15},2)['adaptation_endpoint'],9)
        self.assertEqual(parse_endpoint(r,{'chapters':150},1)['adaptation_endpoint'],60)
        self.assertEqual(parse_endpoint(r,{'volumes':15},3),{})
        self.assertEqual(parse_endpoint({'anime':{'end':'Vol 8, Chap 38 (Original Ending)'}},{'volumes':15},1),{})

    def test_observation_years_start_in_2006_and_retain_long_gaps(self):
        snap=snapshot([subject(1,'2006-04-01'),subject(2,'2010-04-01')],[(1,2,3)],at='2012-12-31')
        rows=retrospective_rows(snap,{})
        self.assertEqual(min(r['cutoff'] for r in rows),'2006-12-31')
        self.assertEqual(next(r for r in rows if r['cutoff']=='2009-12-31')['y'],1)
        self.assertEqual(next(r for r in rows if r['cutoff']=='2010-12-31')['features']['regular_seasons'],2)

    def test_community_features_only_enter_auxiliary_model(self):
        rows=[{'features':{'score':i%10,'log_collect':i,'regular_seasons':i%3+1},'y':i%2} for i in range(40)]
        _,main=fit(rows,trees=8)
        _,auxiliary=fit(rows,trees=8,include_community=True)
        self.assertNotIn('score',main.get_feature_names_out())
        self.assertNotIn('log_collect',main.get_feature_names_out())
        self.assertIn('score',auxiliary.get_feature_names_out())

    def test_disc_and_adaptation_evidence_are_time_gated_separately(self):
        s=subject(1,disc_evidence={'observed_at':'2020-10-13','disc_average':1000},
            adaptation_evidence={'observed_at':'2026-10-21','adaptation_endpoint':9,'adaptation_total':15})
        f=features(s,date(2023,12,31),{})
        self.assertEqual(f['log_disc_average'],math.log1p(1000))
        self.assertTrue(f['adaptation_endpoint_missing'])
        self.assertTrue(f['bd_missing'])

    def test_archive_done_collection_and_duration_formats(self):
        s=subject(1,collection={'done':100})
        self.assertEqual(features(s,date(2023,12,31),{})['log_collect'],math.log1p(100))
        self.assertEqual(duration_minutes('00:21:00'),21)
        self.assertEqual(duration_minutes('21:30'),21.5)
        self.assertIsNone(duration_minutes('unknown'))

    def test_archive_and_api_formats(self):
        s = normalize_subject({'id':1,'type':2,'name':'テスト','platform':1,'tags':[{'name':'日本'},{'name':'漫画改'}],
            'favorite':{'wish':100},'score':7.2,'score_details':{'7':3},'infobox':'{{Infobox animanga/TVAnime\n|话数=2\n|单集片长=21分钟\n}}'})
        self.assertEqual(s['duration_minutes'],21)
        self.assertEqual(s['episodes'],2)
        self.assertEqual(s['source'],'漫画改')
        self.assertEqual(s['votes'],3)


class CheckpointTests(unittest.TestCase):
    def test_truncated_upstream_sales_cannot_enter_the_model(self):
        raw=[{'title':'Hyouka','avg_sales':'10,046'},{'title':'Shigatsu wa Kimi no Uso','avg_sales':'10,727'},
             {'title':'Bakemonogatari','avg_sales':'79,201'}]
        validate_raw_sales(raw)
        damaged=[{**r,'avg_sales':r['avg_sales'].split(',')[0]} for r in raw]
        with self.assertRaises(ValueError):validate_raw_sales(damaged)

    def test_lookup_checkpoint_survives_local_cache_loss(self):
        from pipeline.history import evidence
        from pipeline.common import write_json,load_snapshot
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory)/'cache';lookup=cache/'mangaupdates'/'123.json';output=Path(directory)/'evidence.json'
            write_json(lookup,{'observed_at':'2026-10-21','matching_version':2,'record':None})
            write_json(output,{'observed_at':'2026-10-21','subjects':{'1':{'disc_evidence':{'disc_average':10}}}})
            with patch('pipeline.history.metadata',return_value={'assets':[{'name':'evidence-checkpoint.json.gz'}]}), patch('pipeline.history.gh'), patch.dict('os.environ',{'GITHUB_REPOSITORY':'example/test'}):
                evidence('evidence-store',cache,output)
                lookup.unlink();output.unlink()
                evidence('evidence-restore',cache,output)
                self.assertIsNone(load_snapshot(lookup)['record'])
                self.assertEqual(load_snapshot(output)['subjects']['1']['disc_evidence']['disc_average'],10)


if __name__ == '__main__':
    unittest.main()
