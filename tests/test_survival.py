import unittest
from unittest.mock import patch
from datetime import date
import numpy as np
from pipeline.common import features
from pipeline.modeling import bucket, choose_threshold
from pipeline.survival import CureModel, season_rows, risk_rows, forecast_features, fit_cure, run_fold, CANDIDATES, validation_cutoffs
from test_pipeline import subject, snapshot


class ConstantModel:
    feature_importances_=np.array([0.,1.])
    def predict_proba(self,X):return np.tile([.4,.6],(len(X),1))


class SurvivalTests(unittest.TestCase):
    def test_validation_never_uses_unfinished_calendar_year(self):
        for cutoff in [date(2026,10,21),date(2024,12,31)]:
            dates=validation_cutoffs(cutoff)
            self.assertEqual(len(dates),2)
            self.assertTrue(all(date(d.year+1,12,31)<=cutoff for d in dates))
        self.assertEqual(validation_cutoffs(date(2026,10,21)),[date(2023,12,31),date(2024,12,31)])

    def test_eventual_probability_and_waiting_time_formula(self):
        m=CureModel(ConstantModel(),{'kind':'rf'},np.log(2),1,np.array([0.,0.]),0,[])
        X=np.array([[0.,1.],[365.,1.],[3650.,1.]])
        p=m.predict_proba(X)[:,1]
        self.assertAlmostEqual(p[0],.6,places=6)
        self.assertTrue(np.all(np.diff(p)<0))
        self.assertTrue(np.all(m.annual_probability(X)<=p))
        self.assertGreater(p[-1],0) # A long wait is not an eternal negative.

    def test_future_sequel_is_censored_until_it_occurs(self):
        one=subject(1,'2006-01-01');two=subject(2,'2017-04-01')
        snap=snapshot([one,two],[(1,2,3)],at='2020-12-31')
        before=season_rows(snap,date(2010,12,31),{})
        self.assertEqual(len(before),1);self.assertFalse(before[0]['event'])
        after=season_rows(snap,date(2018,12,31),{})
        self.assertTrue(next(r for r in after if r['id']==1)['event'])
        self.assertGreater(next(r for r in after if r['id']==1)['duration'],11)

    def test_every_old_series_is_in_yearly_risk_set(self):
        snap=snapshot([subject(1,'2006-01-01'),subject(2,'2015-05-01'),subject(3,'2016-07-01')],[(1,3,3)],at='2018-12-31')
        rows=risk_rows(snap,date(2015,12,31),{})
        self.assertEqual({r['id'] for r in rows},{1,2})
        self.assertEqual(next(r for r in rows if r['id']==1)['y'],1)
        self.assertEqual(next(r for r in rows if r['id']==2)['y'],0)
        self.assertEqual(next(r for r in rows if r['id']==1)['features']['regular_seasons'],1)

    def test_unobserved_future_graph_does_not_change_training_features(self):
        one=subject(1,'2006-01-01');two=subject(2,'2017-01-01')
        before=season_rows(snapshot([one],at='2010-12-31'),date(2010,12,31),{})
        after=season_rows(snapshot([one,two],[(1,2,3)],at='2020-12-31'),date(2010,12,31),{})
        self.assertEqual(before,after)

    def test_cadence_only_uses_already_aired_seasons(self):
        a,b,c=subject(1,'2006-01-01'),subject(2,'2008-01-01'),subject(3,'2015-01-01')
        f=features(b,date(2009,12,31),{},[a,b,c])
        self.assertEqual(f['regular_seasons'],2)
        self.assertEqual(f['previous_gap_days'],730)
        self.assertEqual(f['past_series_episodes'],24)

    def test_threshold_can_retrieve_positives_below_half(self):
        y=np.array([0,0,0,1,1]);p=np.array([.01,.02,.03,.2,.3])
        threshold,report=choose_threshold(y,p)
        self.assertEqual(threshold,.2)
        self.assertEqual(report['validation_recall'],1)
        self.assertTrue(report['floor_satisfied'])

    def test_fractional_training_retains_censoring_and_serializes(self):
        import pickle
        rng=np.random.default_rng(4);rows=[]
        for i in range(150):
            duration=float(rng.uniform(.1,10));event=i%3==0
            rows.append({'id':i,'group':i,'duration':duration,'event':event,'y':int(event),
                'features':{'age_days':duration*365.25,'signal':float(i%3==0),'regular_seasons':1}})
        m,v=fit_cure(rows,CANDIDATES[0],trees=12,iterations=4)
        X=v.transform([r['features'] for r in rows])
        p=m.predict_proba(X)[:,1]
        self.assertTrue(np.isfinite(p).all());self.assertTrue(((p>0)&(p<1)).all())
        self.assertGreater(p[::3].mean(),p[1::3].mean())
        self.assertTrue(all(np.isfinite(t['nll']) for t in m.em_trace))
        np.testing.assert_allclose(pickle.loads(pickle.dumps(m)).predict_proba(X)[:,1],p)

    def test_compact_model_keeps_source_and_drops_studio_identities(self):
        rows=[{'id':i,'group':i,'duration':1+i%5,'event':i%3==0,'y':int(i%3==0),
               'features':{'age_days':365*(1+i%5),'studio=A':int(i%2==0),'studio=B':int(i%2!=0),
                           'source=漫画改':int(i%2==0),'source=原创':int(i%2!=0),'studio_prior_releases':i%5}}
              for i in range(60)]
        config=next(c for c in CANDIDATES if c['id']=='logistic_compact')
        model,v=fit_cure(rows,config,trees=8,iterations=3)
        names=list(v.get_feature_names_out())
        self.assertFalse(any(k.startswith('studio=') for k in names))
        self.assertIn('source=漫画改',names);self.assertIn('source=原创',names)
        self.assertIn('studio_prior_releases',names)
        self.assertTrue(np.isfinite(model.predict_proba(v.transform([r['features'] for r in rows]))).all())

    def test_zero_gain_is_removed_from_vectorizer_and_refitted(self):
        rows=[{'id':i,'group':i,'duration':1+i%5,'event':i%2==0,'y':int(i%2==0),
               'features':{'age_days':365*(1+i%5),'signal':int(i%2==0),
                           'constant':1,'rare_unused':int(i==0)}} for i in range(120)]
        config=next(c for c in CANDIDATES if c['id']=='hist_shallow')
        model,v=fit_cure(rows,config,trees=8,iterations=3)
        names=list(v.get_feature_names_out())
        self.assertEqual(names,['age_days','signal'])
        self.assertEqual(model.model.n_features_in_,len(names))
        self.assertEqual(model.feature_selection['constant_removed'],['constant'])
        self.assertIn('rare_unused',model.feature_selection['zero_removed'])
        self.assertGreaterEqual(len(model.feature_selection['rounds']),2)
        self.assertEqual(model.feature_selection['rounds'][-1]['removed'],[])
        self.assertTrue(all(w>0 for k,w in zip(names,model.feature_importances_) if k!='age_days'))
        original=v.transform([r['features'] for r in rows])
        changed=v.transform([{**r['features'],'rare_unused':1e6,'future_only':1e6} for r in rows])
        np.testing.assert_array_equal(model.predict_proba(original),model.predict_proba(changed))

    def test_all_zero_gain_retains_waiting_formula_and_finite_predictions(self):
        rows=[{'id':i,'group':i,'duration':1+i%5,'event':i%3==0,'y':int(i%3==0),
               'features':{'age_days':365*(1+i%5),'rare_unused':int(i==0)}} for i in range(60)]
        config=next(c for c in CANDIDATES if c['id']=='hist_shallow')
        model,v=fit_cure(rows,config,trees=8,iterations=3)
        self.assertEqual(list(v.get_feature_names_out()),['age_days'])
        X=v.transform([{'age_days':0},{'age_days':3650}])
        p=model.predict_proba(X)[:,1]
        self.assertTrue(np.isfinite(p).all())
        self.assertGreater(p[0],p[1])

    def test_annual_report_trains_only_before_cutoff_and_predicts_all_prior_series(self):
        snap=snapshot([subject(i,'2006-01-01') for i in range(1,51)],at='2018-12-31')
        fitted=[]
        class V:
            def transform(self,features):return np.zeros((len(features),1))
        class M:
            def predict_proba(self,X):return np.tile([.8,.2],(len(X),1))
            def annual_probability(self,X):return np.full(len(X),.1)
        def fit(rows,*args):fitted.append(rows);return M(),V()
        with patch('pipeline.survival.select_model',return_value=(CANDIDATES[0],.2,{})),patch('pipeline.survival.fit_cure',side_effect=fit):
            r=run_fold(snap,date(2015,12,31),{},12)
        self.assertEqual(r['test_n'],50)
        self.assertEqual(len({p['id'] for p in r['predictions']}),50)
        self.assertEqual(len(fitted),1)
        self.assertTrue(all(r['cutoff']=='2015-12-31' for r in fitted[0]))
        self.assertTrue(all(not r['event'] for r in fitted[0]))
        self.assertEqual(r['feature_policy'],'release_static_pruned_v3')
        self.assertNotIn('accuracy',r['metrics']) # Censored cases are not eternal negatives.
        self.assertNotIn('brier',r['metrics'])


if __name__=='__main__':unittest.main()
