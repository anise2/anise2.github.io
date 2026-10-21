"""Long-term sequel propensity via a log-normal mixture cure model.

P(eventually continues | no continuation by age t, x)
  = pi(x) S(t) / (1 - pi(x) + pi(x) S(t)).
Non-events are right censored, never hard labels of permanent cancellation.
The eventual probability depends on the parametric tail assumption; it is not
identifiable nonparametrically from a finite archive. Annual outcomes evaluate
retrieval and a separate finite-time event forecast, not eternal negatives.
"""
from datetime import date
import numpy as np
from scipy.optimize import minimize
from scipy.stats import lognorm
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix
from threadpoolctl import threadpool_limits

from pipeline.common import (features, parse_date, relation_graph, series_members, is_tv,
    sequels_of, feature_label)
from pipeline.modeling import bucket, vectorize, choose_threshold, metrics
from pipeline.temporal import Chronology, event_features, PROTOCOL

CANDIDATES=[
    {'id':'rf','name':'随机森林 + 长期续作模型','kind':'rf','leaf':6,'features':.5},
    {'id':'rf_smooth','name':'随机森林 · 叶节点正则','kind':'rf','leaf':15,'features':.5},
    {'id':'extra','name':'极端随机树 + 长期续作模型','kind':'extra','leaf':6,'features':.7},
    {'id':'hist','name':'梯度提升 + 长期续作模型','kind':'hist','leaves':15,'l2':10},
    {'id':'hist_shallow','name':'浅树梯度提升 + 长期续作模型','kind':'hist','leaves':7,'l2':20},
    {'id':'logistic','name':'逻辑回归 + 长期续作模型','kind':'logistic'},
]
# Feature-set selection uses the same past-only inner validation as model choice.
# Sparse studio identities need to earn their place against compact statistics.
CANDIDATES += [{**c,'id':c['id']+'_compact','name':c['name']+' · 精简特征','feature_set':'compact'} for c in CANDIDATES]


def forecast_features(s,cutoff,enrichment,members):
    return event_features(s,cutoff,enrichment,members)


def season_rows(snapshot, cutoff, enrichment):
    history=Chronology(snapshot)
    subjects,successors,groups=history.subjects,history.next,history.groups
    rows=[]
    for s in subjects.values():
        air=parse_date(s.get('date'))
        if not is_tv(s) or not air or not(date(2006,1,1)<=air<=cutoff):continue
        later=sorted([parse_date(t['date']) for t in sequels_of(s['id'],subjects,successors)
            if parse_date(t.get('date')) and parse_date(t['date'])>air])
        event=bool(later and later[0]<=cutoff)
        end=later[0] if event else cutoff
        rows.append({'id':s['id'],'group':groups[s['id']],'cutoff':cutoff.isoformat(),'outcome_end':cutoff.isoformat(),
            'event':event,'y':int(event),'duration':max(1,(end-air).days)/365.25,
            'features':history.features(s['id'],cutoff,enrichment)})
    return rows


def risk_rows(snapshot,cutoff,enrichment):
    """Every series' latest season before cutoff, regardless of release year."""
    history=Chronology(snapshot)
    subjects,successors=history.subjects,history.next
    # Rebuild series groups using only released endpoints. Future sequels must
    # not join two previously independent series into one historical risk row.
    past={i for i,s in subjects.items() if parse_date(s.get('date')) and parse_date(s['date'])<=cutoff}
    clipped={'subjects':[subjects[i] for i in past], 'relations':[r for r in snapshot['relations'] if r['subject_id'] in past and r['related_subject_id'] in past]}
    visible,_,_,groups=relation_graph(clipped)
    grouped=series_members(clipped,visible,groups);rows=[]
    end=date(cutoff.year+1,cutoff.month,cutoff.day)
    for group,members in grouped.items():
        regular=sorted([s for s in members if is_tv(s) and parse_date(s.get('date')) and parse_date(s['date'])<=cutoff],key=lambda s:(s['date'],s['id']))
        if not regular or regular[-1]['date']<'2006-01-01':continue
        s=regular[-1]
        following=sorted([parse_date(t['date']) for t in sequels_of(s['id'],subjects,successors)
            if parse_date(t.get('date')) and parse_date(t['date'])>cutoff])
        rows.append({'id':s['id'],'group':group,'cutoff':cutoff.isoformat(),'outcome_end':end.isoformat(),
            'y':int(bool(following and following[0]<=end)),
            'features':history.features(s['id'],cutoff,enrichment)})
    return rows


def make_estimator(config,trees):
    if config['kind'] in ('rf','extra'):
        cls=RandomForestClassifier if config['kind']=='rf' else ExtraTreesClassifier
        return cls(n_estimators=trees,min_samples_leaf=config['leaf'],max_depth=14,
            max_features=config['features'],n_jobs=6,random_state=42)
    if config['kind']=='hist':
        return HistGradientBoostingClassifier(max_iter=100,learning_rate=.05,max_leaf_nodes=config['leaves'],
            min_samples_leaf=25,l2_regularization=config['l2'],early_stopping=False,random_state=42)
    return make_pipeline(StandardScaler(),LogisticRegression(C=.03,max_iter=700,random_state=42))


def fit_fractional(config,X,w,trees):
    # Replicate weighted targets to fit E[latent eventual-event label]. No
    # oversampling prior shift: total weight of every season stays exactly one.
    x=np.concatenate([X,X]);y=np.r_[np.ones(len(X)),np.zeros(len(X))];weight=np.r_[w,1-w]
    mask=weight>1e-8;m=make_estimator(config,trees)
    with threadpool_limits(limits=4):
        if config['kind']=='logistic':m.fit(x[mask],y[mask],logisticregression__sample_weight=weight[mask])
        else:m.fit(x[mask],y[mask],sample_weight=weight[mask])
    return m


class CureModel:
    def __init__(self,model,config,mu,sigma,reference,age_col,em_trace):
        self.model,self.config,self.mu,self.sigma=model,config,float(mu),float(sigma)
        self.reference,self.age_col,self.em_trace=reference,age_col,em_trace
        self.threshold=.5
        self.feature_importances_=self.importance()
        # Waiting time affects the conditional prediction through the explicit
        # survival formula, not a split, so its split importance is zero.

    def baseline(self,X):
        z=X.copy();z[:,self.age_col]=0
        with threadpool_limits(limits=4):p=self.model.predict_proba(z)[:,1]
        return np.clip(p,1e-5,1-1e-5)

    def survival(self,t):return lognorm.sf(np.maximum(t,1e-8),s=self.sigma,scale=np.exp(self.mu))

    def predict_proba(self,X):
        pi=self.baseline(X);s=self.survival(X[:,self.age_col]/365.25)
        p=pi*s/(1-pi+pi*s)
        return np.column_stack((1-p,p))

    def annual_probability(self,X):
        pi=self.baseline(X);t=X[:,self.age_col]/365.25;s=self.survival(t)
        return pi*(s-self.survival(t+1))/(1-pi+pi*s)

    def nll(self,X,rows):
        pi=self.baseline(X);t=np.array([r['duration'] for r in rows]);event=np.array([r['event'] for r in rows])
        logevent=np.log(pi)+lognorm.logpdf(t,s=self.sigma,scale=np.exp(self.mu))
        logcensor=np.log(np.maximum(1-pi+pi*self.survival(t),1e-12))
        return float(-np.where(event,logevent,logcensor).mean())

    def importance(self):
        if hasattr(self.model,'feature_importances_'):return self.model.feature_importances_
        if hasattr(self.model,'_predictors'):
            v=np.zeros(len(self.reference))
            for stage in self.model._predictors:
                for tree in stage:
                    n=tree.nodes;mask=~n['is_leaf'].astype(bool)
                    for col,gain in zip(n['feature_idx'][mask],n['gain'][mask]):v[col]+=max(0,gain)
            return v/max(v.sum(),1e-12)
        v=np.abs(self.model[-1].coef_[0]);return v/max(v.sum(),1e-12)

    def explanations(self,X,top=20):
        p=self.predict_proba(X)[:,1];result=np.zeros_like(X,dtype=np.float64)
        columns=set(np.argsort(self.feature_importances_)[-top:]);columns.add(self.age_col)
        for col in columns:
            z=X.copy();z[:,col]=self.reference[col]
            result[:,col]=p-self.predict_proba(z)[:,1]
        return result


def _fit_cure_matrix(rows,config,X,age_col,trees,iterations):
    X=X.copy();X[:,age_col]=0
    t=np.array([r['duration'] for r in rows]);event=np.array([r['event'] for r in rows])
    mu,sigma=np.log(2),1.0
    w=np.where(event,1,.35*np.exp(-t/5));trace=[]
    model=None
    for step in range(iterations):
        model=fit_fractional(config,X,w,trees)
        with threadpool_limits(limits=4):pi=np.clip(model.predict_proba(X)[:,1],1e-5,1-1e-5)
        def loss(theta):
            location,ls=theta;scale=np.exp(ls)
            return -np.mean(event*(np.log(pi)+lognorm.logpdf(t,s=scale,scale=np.exp(location)))+
                (~event)*np.log(np.maximum(1-pi+pi*lognorm.sf(t,s=scale,scale=np.exp(location)),1e-12)))
        opt=minimize(loss,[mu,np.log(sigma)],method='L-BFGS-B',bounds=[(-2,3),(-1.5,.8)])
        if not opt.success and not np.isfinite(opt.fun):raise ValueError('Non-finite mixture likelihood')
        mu,sigma=float(opt.x[0]),float(np.exp(opt.x[1]))
        S=lognorm.sf(t,s=sigma,scale=np.exp(mu));next_w=np.where(event,1,pi*S/(1-pi+pi*S))
        delta=float(np.mean(abs(w-next_w)));trace.append({'iteration':step+1,'nll':float(opt.fun),'posterior_change':delta})
        w=next_w
        if step>=2 and delta<.001:break
    return CureModel(model,config,mu,sigma,np.median(X,axis=0),age_col,trace)


def fit_cure(rows,config,trees=160,iterations=6):
    """Fit, remove unused columns, then refit until the retained set is stable.

    Selection uses only these training rows, independently at every origin and
    for every candidate. Neither validation nor future outcomes enter a mask.
    age_days is a structural input to the conditional survival formula, even
    though the incidence estimator must never split on it.
    """
    if config.get('feature_set')=='compact':
        rows=[{**r,'features':{k:v for k,v in r['features'].items() if not k.startswith('studio=')}} for r in rows]
    v,X,_=vectorize(rows)
    names=v.get_feature_names_out()
    keep=(np.ptp(X,axis=0)>0) | (names=='age_days')
    constants=list(names[~keep]);initial_count=len(names)
    v.restrict(keep);X=X[:,keep]
    rounds=[];removed=[]
    while True:
        names=v.get_feature_names_out();age_col=list(names).index('age_days')
        model=_fit_cure_matrix(rows,config,X,age_col,trees,iterations)
        if not np.isfinite(model.feature_importances_).all():
            raise ValueError('Non-finite feature importance')
        keep=(model.feature_importances_>0) | (names=='age_days')
        unused=list(names[~keep])
        rounds.append({'fit':len(rounds)+1,'inputs':len(names),'removed':unused})
        if not unused:break
        removed.extend(unused)
        v.restrict(keep);X=X[:,keep]
    model.feature_selection={
        'method':'training_only_zero_importance_refit_until_stable',
        'initial_inputs':initial_count,'constant_removed':constants,
        'zero_removed':removed,'retained_inputs':len(names),
        'incidence_inputs':len(names)-1,'formula_inputs':['age_days'],
        'rounds':rounds,
    }
    return model,v


def validation_cutoffs(cutoff):
    # A production run in October must not label the unfinished year negative.
    dates=[date(y,12,31) for y in range(cutoff.year-4,cutoff.year)
           if date(y+1,12,31)<=cutoff]
    return dates[-2:]


def screening_threshold(model,v,snapshot,cutoff,enrichment):
    dates=validation_cutoffs(cutoff)
    screening=[r for d in dates for r in risk_rows(snapshot,d,enrichment)]
    X=v.transform([r['features'] for r in screening])
    threshold,selection=choose_threshold(np.array([r['y'] for r in screening]),model.predict_proba(X)[:,1])
    selection['threshold_cutoffs']=[d.isoformat() for d in dates]
    selection['threshold_outcome_end']=max(r['outcome_end'] for r in screening)
    selection['threshold_protocol']='two_mature_prior_years_F2'
    return threshold,selection


def select_model(snapshot,cutoff,enrichment,trees=120):
    # All validation labels/censoring times are known before the outer cutoff.
    valid_cutoff=validation_cutoffs(cutoff)[0]
    train_rows=season_rows(snapshot,valid_cutoff,enrichment)
    inner=train_rows
    # Validate future follow-up of the earlier risk set, never current labels
    # from the outer test year. Conditional likelihood removes pre-origin time.
    valid_ids={x['id'] for x in risk_rows(snapshot,valid_cutoff,enrichment)}
    valid=[r for r in season_rows(snapshot,cutoff,enrichment) if r['id'] in valid_ids]
    screening=risk_rows(snapshot,valid_cutoff,enrichment)
    if len(inner)<50 or sum(r['event'] for r in inner)<5 or not screening or not sum(r['y'] for r in screening):
        raise ValueError('Insufficient historical evidence for nested selection')
    results=[];best=None
    for config in CANDIDATES:
        m,v=fit_cure(inner,config,trees)
        at_origin={r['id']:r for r in train_rows}
        after=m.nll(v.transform([r['features'] for r in valid]),valid)
        before=[at_origin[r['id']] for r in valid]
        nll=after-m.nll(v.transform([r['features'] for r in before]),before)
        p=m.predict_proba(v.transform([r['features'] for r in screening]))[:,1]
        ap=float(average_precision_score([r['y'] for r in screening],p))
        results.append({'id':config['id'],'name':config['name'],'censored_nll':nll,'annual_pr_auc':ap,
            'retained_inputs':m.feature_selection['retained_inputs'],
            'zero_removed':len(m.feature_selection['zero_removed'])})
        if best is None or nll<best[0]:best=(nll,config,m,v)
    _,config,selected_model,selected_vectorizer=best
    threshold,selection=screening_threshold(selected_model,selected_vectorizer,snapshot,cutoff,enrichment)
    selection.update({'selected':config['id'],'name':config['name'],'candidates':sorted(results,key=lambda r:r['censored_nll']),
        'feature_sets':['compact','credits'],'feature_set':config.get('feature_set','credits'),
        'cutoffs':[valid_cutoff.isoformat()],'outcome_end':cutoff.isoformat(),'inner_train_n':len(inner),
        'selection_metric':'post-origin conditional censored negative log likelihood (lower is better)',
        'threshold_purpose':'prior-year F2 retrieval operating point, not annual event probability'})
    return config,threshold,selection


def annual_report(rows,probabilities,annual_p,cutoff,threshold,selection,train_n):
    y=np.array([r['y'] for r in rows]);p=np.array(probabilities);annual_p=np.array(annual_p)
    m=metrics(y,p,threshold,float(y.mean()))
    # Brier for eventual propensity is undefined: non-events are censored.
    m.pop('brier');m.pop('baseline_brier');m.pop('accuracy');m.pop('baseline_accuracy');m.pop('balanced_accuracy')
    m['annual_brier']=float(brier_score_loss(y,annual_p))
    bins=[]
    for low,high in zip([0,.025,.05,.1,.2,.4,.6],[.025,.05,.1,.2,.4,.6,1]):
        mask=(annual_p>=low)&((annual_p<high) if high<1 else (annual_p<=1))
        bins.append({'low':low,'high':high,'n':int(mask.sum()),'predicted':float(annual_p[mask].mean()) if mask.any() else None,'observed':float(y[mask].mean()) if mask.any() else None})
    fixed=metrics(y,p,.5,float(y.mean()))
    n=max(1,int(np.ceil(len(y)*.1)));top=np.argsort(-p)[:n]
    return {'cutoff':cutoff,'outcome_end':rows[0]['outcome_end'],'train_n':train_n,'test_n':len(rows),
        'positive_n':int(y.sum()),'threshold':threshold,'metrics':m,'selection':selection,
        'fixed_50_metrics':{k:fixed[k] for k in ['recall','precision','f2']},
        'confusion':confusion_matrix(y,p>=threshold,labels=[0,1]).tolist(),
        'calibration':bins,'permutation':[],
        'ranking':{'top_fraction':.1,'top_n':n,'hits':int(y[top].sum()),'recall':float(y[top].sum()/max(y.sum(),1)),'precision':float(y[top].mean())},
        'predictions':[{'id':r['id'],'group':r['group'],'cutoff':cutoff,'actual':r['y'],
            'probability':round(float(q),6),'annual_probability':round(float(a),6),'predicted':bool(q>=threshold)} for r,q,a in zip(rows,p,annual_p)]}


def run_fold(snapshot,cutoff,enrichment,trees=160):
    config,threshold,selection=select_model(snapshot,cutoff,enrichment,min(trees,120))
    training=season_rows(snapshot,cutoff,enrichment);test=risk_rows(snapshot,cutoff,enrichment)
    model,v=fit_cure(training,config,trees);X=v.transform([r['features'] for r in test])
    report=annual_report(test,model.predict_proba(X)[:,1],model.annual_probability(X),cutoff.isoformat(),threshold,selection,len(training))
    report['feature_policy']=PROTOCOL
    report['feature_selection']=getattr(model,'feature_selection',{})
    report['protocol']='rolling temporal origin; training and selection outcomes <= cutoff; following year reserved for evaluation'
    return report


def permutation_audit(snapshot,cutoff,enrichment,config,trees=160):
    """Independent franchise fold, evaluated with censoring-aware likelihood."""
    rows=season_rows(snapshot,cutoff,enrichment)
    train=[r for r in rows if bucket(r['group'])!=0];held=[r for r in rows if bucket(r['group'])==0]
    model,v=fit_cure(train,config,trees)
    X=v.transform([r['features'] for r in held]);reference=model.nll(X,held)
    names=v.get_feature_names_out();rng=np.random.default_rng(42);out=[]
    for col in np.argsort(model.feature_importances_)[-20:]:
        values=[]
        for _ in range(3):
            changed=X.copy();changed[:,col]=rng.permutation(changed[:,col])
            values.append(model.nll(changed,held)-reference)
        out.append({'key':names[col],'name':feature_label(names[col]),'value':float(np.mean(values)),'std':float(np.std(values))})
    return sorted(out,key=lambda r:r['value'],reverse=True)
