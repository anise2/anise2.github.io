"""Train a censored long-term continuation model and export a static site.

Legacy annual-RF helpers are retained only for regression tests. Production
uses the eventual-sequel mixture model in pipeline.survival.
"""
import argparse
import json
import math
import hashlib
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import sklearn
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import (accuracy_score, average_precision_score, balanced_accuracy_score,
    brier_score_loss, confusion_matrix, precision_score, recall_score, roc_auc_score)

from pipeline.common import (add_year, enrichment_at, feature_label, features, first_season, is_tv,
    load_snapshot, parse_date, read_enrichment, relation_graph, season_progress, sequels_of, series_members, write_json,
    COMMUNITY_FEATURES, original_for, completion_evidence, evidence_for)


def prospective_rows(feature_snapshot, outcome_snapshot, enrichment):
    """Historical features only; outcome snapshot is used solely for labels."""
    cutoff = parse_date(feature_snapshot['snapshot_at'])
    end = add_year(cutoff)
    if parse_date(outcome_snapshot['snapshot_at']) < end:
        return []  # Right censored; not negative examples.
    subjects, successors, predecessors, groups = relation_graph(feature_snapshot)
    grouped = series_members(feature_snapshot, subjects, groups)
    future, future_next, _, _ = relation_graph(outcome_snapshot)
    result = []
    for sid, s in subjects.items():
        aired = parse_date(s['date'])
        if not is_tv(s) or not aired or not (date(2006,1,1) <= aired <= cutoff):
            continue
        if sequels_of(sid, subjects, successors):
            continue  # A known continuation at decision time is not a forecast.
        next_items = sequels_of(sid, future, future_next) if sid in future else []
        target = any(parse_date(t['date']) and cutoff < parse_date(t['date']) <= end for t in next_items)
        result.append({'id': sid, 'group': groups[sid], 'cutoff': cutoff.isoformat(), 'outcome_end': end.isoformat(),
                       'y': int(target), 'features': features(s, cutoff, enrichment, grouped[groups[sid]])})
    return result


def retrospective_rows(snapshot, enrichment):
    """Exploration only: contemporary scores/tags are NOT historical evidence."""
    subjects, successors, predecessors, groups = relation_graph(snapshot)
    grouped = series_members(snapshot, subjects, groups)
    observed = parse_date(snapshot['snapshot_at'])
    rows = []
    for group, members in grouped.items():
        regular = sorted([s for s in members if is_tv(s) and parse_date(s['date'])], key=lambda s:(s['date'],s['id']))
        if not regular or not any(s['date'] >= '2006-01-01' for s in regular):
            continue
        for year in range(max(2006,parse_date(regular[0]['date']).year), observed.year):
            cutoff, end = date(year,12,31), date(year+1,12,31)
            if end > observed:
                continue
            aired = [s for s in regular if parse_date(s['date']) <= cutoff]
            if not aired:
                continue
            s = aired[-1]
            next_items = sequels_of(s['id'], subjects, successors)
            target = any(parse_date(t['date']) and cutoff < parse_date(t['date']) <= end for t in next_items)
            rows.append({'id':s['id'],'group':group,'cutoff':cutoff.isoformat(),'outcome_end':end.isoformat(),
                'y':int(target),'features':features(s,cutoff,enrichment,members)})
    return rows


def choose_split(rows, strict=False):
    dates = sorted({r['cutoff'] for r in rows}, reverse=True)
    for cutoff in dates:
        test = [r for r in rows if r['cutoff'] == cutoff]
        excluded = {r['group'] for r in test}
        train = [r for r in rows if r['outcome_end'] <= cutoff and r['cutoff'] < cutoff and r['group'] not in excluded]
        if len(train) < 15 and len(test) >= 40:
            # Repeated annual risk sets share franchises. Reserve a stable 20% of
            # whole franchises before evaluation; retain the chronological cutoff.
            held = {g for g in excluded if int(hashlib.sha256(str(g).encode()).hexdigest()[:8],16) % 5 == 0}
            test = [r for r in test if r['group'] in held]
            train = [r for r in rows if r['outcome_end'] <= cutoff and r['cutoff'] < cutoff and r['group'] not in held]
        if len(train) >= 15 and len(test) >= (5 if strict else 8) and len({r['y'] for r in train}) == 2:
            return train, test, cutoff
    return [], [], None


def fit(rows, trees=300, include_community=False):
    counts = Counter(k for r in rows for k in r['features'] if k.startswith('tag='))
    keep = {k for k, count in counts.most_common(500) if count >= 3}
    vectorizer = DictVectorizer(sparse=False, dtype=np.float32)
    clean = [{k: v for k, v in r['features'].items() if (not k.startswith('tag=') or k in keep)
              and (include_community or k not in COMMUNITY_FEATURES)} for r in rows]
    X = vectorizer.fit_transform(clean)
    y = np.array([r['y'] for r in rows])
    if len(rows) < 15 or len(set(y)) < 2:
        raise ValueError('At least 15 observations and both label classes are required; refusing invented probabilities.')
    model = RandomForestClassifier(n_estimators=trees, min_samples_leaf=max(2, min(8, len(rows) // 80)),
        max_depth=14, max_features='sqrt', n_jobs=-1, random_state=42)
    model.fit(X, y)
    return model, vectorizer


def explain(model, X):
    """Exact additive path contributions: root probability + changes along tree paths.

    This is not SHAP and not causal. Contributions sum to RF.predict_proba[:, 1].
    Traverses each tree vectorized across all displayed anime for CPU efficiency.
    """
    result = np.zeros_like(X, dtype=np.float64)
    baseline = 0.0
    for estimator in model.estimators_:
        tree = estimator.tree_
        values = tree.value[:, 0, :]
        p = values[:, 1] / values.sum(axis=1)
        baseline += p[0] / len(model.estimators_)
        nodes = np.zeros(len(X), dtype=int)
        active = tree.children_left[nodes] != -1
        while active.any():
            ix = np.flatnonzero(active)
            parents = nodes[ix]
            columns = tree.feature[parents]
            children = np.where(X[ix, columns] <= tree.threshold[parents], tree.children_left[parents], tree.children_right[parents])
            result[ix, columns] += (p[children] - p[parents]) / len(model.estimators_)
            nodes[ix] = children
            active = tree.children_left[nodes] != -1
    return baseline, result


def evaluate(model, vectorizer, train, test, cutoff):
    X = vectorizer.transform([r['features'] for r in test])
    y = np.array([r['y'] for r in test])
    p = model.predict_proba(X)[:, 1]
    pred = p >= .5
    prevalence = sum(r['y'] for r in train) / len(train)
    majority = int(prevalence >= .5)
    metrics = {'accuracy': accuracy_score(y, pred), 'balanced_accuracy': balanced_accuracy_score(y, pred),
        'precision': precision_score(y, pred, zero_division=0), 'recall': recall_score(y, pred, zero_division=0),
        'brier': brier_score_loss(y, p), 'baseline_accuracy': float(np.mean(y == majority)),
        'baseline_brier': brier_score_loss(y, np.full(len(y), prevalence)),
        'roc_auc': roc_auc_score(y, p) if len(set(y)) == 2 else None,
        'pr_auc': average_precision_score(y, p) if y.sum() else None}
    # Held-out permutation scores for top 20 training features, without tuning on the test set.
    names = vectorizer.get_feature_names_out()
    rng = np.random.default_rng(42)
    permutation = []
    reference = -brier_score_loss(y, p)
    for col in np.argsort(model.feature_importances_)[-20:][::-1]:
        scores = []
        for _ in range(3):
            permuted = X.copy()
            permuted[:, col] = rng.permutation(permuted[:, col])
            scores.append(reference + brier_score_loss(y, model.predict_proba(permuted)[:, 1]))
        permutation.append({'key': names[col], 'name': feature_label(names[col]), 'value': float(np.mean(scores)), 'std': float(np.std(scores))})
    bins = []
    for low in np.arange(0, 1, .2):
        mask = (p >= low) & ((p < low + .2) if low < .79 else (p <= 1))
        bins.append({'low': round(float(low), 1), 'high': round(float(low + .2), 1), 'n': int(mask.sum()),
                     'predicted': float(p[mask].mean()) if mask.any() else None,
                     'observed': float(y[mask].mean()) if mask.any() else None})
    return {'cutoff': cutoff, 'outcome_end': max(r['outcome_end'] for r in test), 'train_n': len(train), 'test_n': len(test),
        'positive_n': int(y.sum()), 'threshold': .5, 'metrics': metrics,
        'confusion': confusion_matrix(y, pred, labels=[0, 1]).tolist(), 'calibration': bins,
        'permutation': sorted(permutation, key=lambda r: r['value'], reverse=True),
        'predictions': [{'id': r['id'], 'actual': r['y'], 'probability': round(float(q), 5)} for r, q in zip(test, p)]}


def catalog(snapshot, model, vectorizer, enrichment, limit):
    subjects, successors, predecessors, groups = relation_graph(snapshot)
    cutoff = parse_date(snapshot.get('prediction_at',snapshot['snapshot_at']))
    grouped = series_members(snapshot, subjects, groups)
    eligible = [s for s in subjects.values() if is_tv(s) and parse_date(s['date']) and date(2006,1,1) <= parse_date(s['date']) <= cutoff]
    eligible.sort(key=lambda s: (s['date'], s['id']), reverse=True)
    selected, seen = [], set()
    for s in eligible:
        if groups[s['id']] not in seen:
            selected.append(s)
            seen.add(groups[s['id']])
    total = len(selected)
    selected.sort(key=lambda s: max(t.get('votes', 0) for t in grouped[groups[s['id']]]), reverse=True)
    if limit:
        selected = selected[:limit]
    from pipeline.temporal import Chronology
    history=Chronology(snapshot)
    X = vectorizer.transform([history.features(s['id'], cutoff, enrichment) for s in selected])
    probability = model.predict_proba(X)[:, 1]
    baseline = None
    contributions = model.explanations(X)
    names = vectorizer.get_feature_names_out()
    output = []
    for s, p, contribution in zip(selected, probability, contributions):
        sid = s['id']
        members = sorted(grouped[groups[sid]], key=lambda t: t.get('date') or '9999')
        next_tv = [t for t in sequels_of(sid, subjects, successors) if not parse_date(t['date']) or parse_date(t['date']) > parse_date(s['date'])]
        upcoming = bool(next_tv) or any(parse_date(t.get('date')) and parse_date(t['date']) > cutoff and not t.get('excluded_regular') for t in members)
        completion = completion_evidence(members,cutoff, upcoming)
        status = 'known' if next_tv else 'completed' if completion else 'predict'
        progress = season_progress(members,cutoff)
        regular = sorted([t for t in members if is_tv(t) and parse_date(t.get('date')) and parse_date(t['date']) <= cutoff],key=lambda t:t['date'])
        recent = any(parse_date(t['date']) > cutoff-timedelta(days=365) for t in regular[1:])
        e = evidence_for(s,cutoff,enrichment,members)
        top = sorted(range(len(names)), key=lambda i: abs(contribution[i]), reverse=True)[:6]
        explanation = [{'key': names[i], 'name': feature_label(names[i]), 'contribution': round(float(contribution[i]), 6), 'value': float(X[len(output), i])} for i in top if abs(contribution[i]) > 1e-8]
        public = {k:v for k,v in s.items() if k not in ('originals','disc_evidence','adaptation_evidence','ova_episode_records')}
        output.append({**public, 'group': groups[sid], 'status': status, 'probability': round(float(p), 5) if status == 'predict' else None,
            'baseline': None, 'explanation': explanation if status == 'predict' else [],
            'explanation_method':'single_feature_replacement', 'explanation_other': None,
            'enrichment': e, 'progress':progress, 'original':original_for(s,members), 'completion':completion,
            'recent_sequel':recent, 'has_sequel':progress['regular'] >= 2 or bool(next_tv),
            'series': [{**{k: t.get(k) for k in ('id', 'name_cn', 'name', 'date', 'platform', 'image', 'external_image', 'episodes', 'duration_minutes', 'excluded_kind')}, 'regular':is_tv(t)} for t in members],
            'sequel_ids': [t['id'] for t in next_tv]})
    return output, total


def run(args):
    from pipeline.backtest import load_data, summarize
    from pipeline.survival import season_rows, select_model, fit_cure, run_fold, permutation_audit, CANDIDATES, validation_cutoffs
    from pipeline.temporal import PROTOCOL, POLICY
    snapshot, enrichment = load_data(args.snapshot,args.external,args.enrichment)
    strict = bool(args.history)
    if strict:
        raise ValueError('The eventual-sequel survival model requires release-time historical feature cohorts for strict validation. Current archive reconstruction is exploration only; refusing silent fallback.')
    cutoff = parse_date(snapshot['snapshot_at'])
    production_rows = season_rows(snapshot,cutoff,enrichment)
    config,threshold,selection = select_model(snapshot,cutoff,enrichment,min(args.trees,120))
    print('Selected production model: '+config['name'],flush=True)
    model,vectorizer = fit_cure(production_rows,config,args.trees)
    model.threshold = threshold
    years_path=Path(args.backtest_report)
    annual=load_snapshot(years_path) if years_path.exists() else None
    if annual and (annual.get('target') != 'eventual_next_season' or annual.get('feature_policy')!=PROTOCOL):
        annual=None
    latest=validation_cutoffs(cutoff)[-1]
    if annual and annual['snapshot_at'] == snapshot['snapshot_at'] and annual.get('years') and annual['years'][-1]['cutoff']==latest.isoformat() and len(annual['years'][-1]['selection']['candidates'])==len(CANDIDATES):
        report=annual['years'][-1]
    else:
        observed=parse_date(snapshot['snapshot_at'])
        report=run_fold(snapshot,latest,enrichment,min(args.trees,160))
        report['snapshot_at']=snapshot['snapshot_at']
        if annual:
            annual=dict(annual)
            annual['years']=[r for r in annual['years'] if r['cutoff']!=report['cutoff']]+[report]
            annual['years'].sort(key=lambda r:r['cutoff'])
            annual['summary']=summarize(annual['years'])
            annual['latest_refresh_snapshot_at']=snapshot['snapshot_at']
    if not report.get('permutation'):
        audit_config=next(c for c in CANDIDATES if c['id']==report['selection']['selected'])
        report['permutation']=permutation_audit(snapshot,parse_date(report['cutoff']),enrichment,audit_config,min(args.trees,160))
    rows=production_rows
    items, total = catalog(snapshot, model, vectorizer, enrichment, args.limit)
    names = vectorizer.get_feature_names_out()
    importance = [{'key': k, 'name': feature_label(k), 'value': float(v)} for k, v in zip(names, model.feature_importances_)]
    importance.sort(key=lambda x: x['value'], reverse=True)
    training_coverage={
        'n':len(production_rows),
        'source_known':sum(not r['features'].get('source=未知',0) for r in production_rows),
        'studio_known':sum(not r['features']['studio_missing'] for r in production_rows),
        'source_age_known':sum(not r['features']['source_age_missing'] for r in production_rows),
        'previous_sales_known':sum(any(not r['features'][key+'_missing'] for key in ('log_previous_disc_average','log_previous_recent_disc_average')) for r in production_rows),
        'previous_season_n':sum(r['features']['regular_seasons']>=2 for r in production_rows),
    }
    from pipeline.temporal import SOURCE_TYPES
    source_breakdown=[{'source':source,'n':sum(r['features'].get('source='+source,0) for r in production_rows),
        'importance':next((i['value'] for i in importance if i['key']=='source='+source),0)} for source in SOURCE_TYPES]
    season_bins = []
    for season in range(1, 5):
        cohort = [r for r in production_rows if min(int(r['features']['regular_seasons']), 4) == season]
        positives = sum(r['y'] for r in cohort)
        season_bins.append({'season': season, 'label': str(season) + ('+ 季' if season == 4 else ' 季'), 'n': len(cohort), 'positive_n': positives, 'rate': positives / len(cohort) if cohort else None})
    artifact = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(), 'snapshot_at': snapshot['snapshot_at'],
        'mode': snapshot.get('mode'), 'validation':'temporal_release_events', 'temporal_policy':POLICY,
        'prediction_at':snapshot.get('prediction_at',snapshot['snapshot_at']),
        'source': snapshot['source'], 'source_url': snapshot['source_url'], 'source_sha256': snapshot.get('sha256'),
        'supplemental_report':snapshot.get('supplemental_report'),
        'target': '在截至当前仍未出现下一季的条件下，将来任何时候是否会有下一季 TV / WEB 正篇；不限定未来一年',
        'model': {'name': config['name'], 'kind':config['kind'], 'trees': args.trees if config['kind'] in ('rf','extra') else 100 if config['kind']=='hist' else 0, 'features': len(names), 'tags': sum(k.startswith('tag=') for k in names),
            'training_samples': len(production_rows), 'positive_samples': sum(r['y'] for r in production_rows), 'seed': 42,
            'feature_set':config.get('feature_set','credits'),
            'feature_selection':model.feature_selection,
            'calibrated': False, 'probability_type':'conditional_eventual_probability', 'threshold':threshold, 'training_cutoff':snapshot['snapshot_at'],
            'selection':selection, 'survival':{'distribution':'lognormal','mu':model.mu,'sigma':model.sigma,'em_trace':model.em_trace},
            'importance_description':{'rf':'随机森林分裂重要性，合计 100%','extra':'极端随机树分裂重要性，合计 100%','hist':'梯度提升分裂增益，合计 100%','logistic':'标准化回归系数绝对值，归一为 100%'}[config['kind']],
            'global_importance': importance, 'season_breakdown': season_bins,
            'training_coverage':training_coverage,'source_breakdown':source_breakdown,
            'community_policy':'评分、收藏、题材评价标签、原作卷数与完结状态没有历史快照，排除动态现值。原作类型和制作署名单独作为固定作品属性由当前归档重建。',
            'libraries': {'scikit_learn':sklearn.__version__, 'numpy':np.__version__}},
        'coverage': {'anime_records': len(snapshot['subjects']), 'eligible_series': total, 'displayed': len(items),
            'since_year':2006, 'years':[{'year':y,'n':sum(s.get('date','').startswith(str(y)) for s in snapshot['subjects'])} for y in range(2006,parse_date(snapshot['snapshot_at']).year+1)],
            'records_since_2006':sum('2006-01-01' <= s.get('date','') <= snapshot['snapshot_at'] for s in snapshot['subjects']),
            'original':sum(bool(i.get('original')) for i in items),
            'source_volumes':sum((i.get('original') or {}).get('volumes') is not None for i in items),
            'source_status':sum((i.get('original') or {}).get('status','unknown') != 'unknown' for i in items),
            'bd': sum(i['enrichment'].get('bd_copies') is not None or i['enrichment'].get('disc_average') is not None or bool(i['enrichment'].get('previous_disc')) for i in items),
            'bd_recent':sum(bool(i.get('recent_disc_evidence')) for i in items),
            'mal_matched':sum(bool(i.get('external_anime')) for i in items),
            'adaptation': sum(i['enrichment'].get('adapted_units') is not None or i['enrichment'].get('adaptation_endpoint') is not None for i in items),
            'adaptation_notes':sum(bool(i['enrichment'].get('adaptation_note')) for i in items)},
        'backtest': report, 'annual_backtest':annual, 'items': items}
    write_json(args.output, artifact)
    Path(args.artifacts).mkdir(parents=True, exist_ok=True)
    write_json(Path(args.artifacts) / 'model-card.json', {k: v for k, v in artifact.items() if k != 'items'})
    write_json(Path(args.artifacts) / 'feature-dictionary.json', [{'key': k, 'name': feature_label(k)} for k in names])
    joblib.dump({'model':model,'vectorizer':vectorizer,'snapshot_at':snapshot['snapshot_at']},Path(args.artifacts)/'next-season-model.joblib',compress=3)
    print(f'{len(production_rows)} training samples; {len(items)} series; {len(names)} features. Validation: {artifact["validation"]}', flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--snapshot', default='.cache/latest.json')
    p.add_argument('--history', help='Reserved strict-mode guard; current survival reconstruction refuses this mode until release-time cohorts are available')
    p.add_argument('--enrichment', default='data/enrichment.csv')
    p.add_argument('--output', default='data/catalog.json')
    p.add_argument('--artifacts', default='artifacts')
    p.add_argument('--trees', type=int, default=300)
    p.add_argument('--limit', type=int, default=0, help='0 exports all eligible series since 2006')
    p.add_argument('--external', default='data/external/enrichment.json')
    p.add_argument('--backtest-report', default='data/backtest-years.json', help='Reuse matching local annual report; CI otherwise evaluates only the latest cutoff')
    run(p.parse_args())
