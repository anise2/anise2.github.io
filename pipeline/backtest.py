"""Local yearly censored-survival backtest; never invoked in weekly CI."""
import argparse
from datetime import date,datetime,timezone
from pathlib import Path
import numpy as np
from pipeline.common import load_snapshot,read_enrichment,write_json,parse_date
from pipeline.survival import run_fold, validation_cutoffs, season_rows, fit_cure, CANDIDATES, screening_threshold, annual_report
from pipeline.modeling import bucket
from pipeline.temporal import PROTOCOL, POLICY


def load_data(snapshot_path,external_path='data/external/enrichment.json',enrichment_path='data/enrichment.csv'):
    snapshot=load_snapshot(snapshot_path)
    if Path(external_path).exists():
        extra=load_snapshot(external_path)
        snapshot['prediction_at']=max(snapshot['snapshot_at'],extra.get('observed_at',snapshot['snapshot_at']))
        for s in snapshot['subjects']:s.update(extra.get('subjects',{}).get(str(s['id']),{}))
    return snapshot,read_enrichment(enrichment_path)


def summarize(reports):
    confusion=np.sum([r['confusion'] for r in reports],axis=0);tn,fp,fn,tp=confusion.ravel()
    return {'years':len(reports),'test_n':int(confusion.sum()),'positive_n':int(tp+fn),'confusion':confusion.tolist(),
        'precision':float(tp/max(tp+fp,1)),'recall':float(tp/max(tp+fn,1)),
        'f2':float(5*tp/max(5*tp+4*fn+fp,1)),
        'macro_pr_auc':float(np.mean([r['metrics']['pr_auc'] for r in reports if r['metrics']['pr_auc'] is not None])),
        'annual_brier':float(np.average([r['metrics']['annual_brier'] for r in reports],weights=[r['test_n'] for r in reports]))}


def run(args):
    snapshot,enrichment=load_data(args.snapshot);reports=[]
    observed=parse_date(snapshot['snapshot_at'])
    for year in range(args.start_year,min(args.end_year,validation_cutoffs(observed)[-1].year)+1):
        print(f'cutoff {year}-12-31: select {len(CANDIDATES)} model / feature combinations on past-only validation; predict every prior series',flush=True)
        r=run_fold(snapshot,date(year,12,31),enrichment,args.trees);reports.append(r)
        output={'schema_version':2,'target':'eventual_next_season','generated_at':datetime.now(timezone.utc).isoformat(),
            'snapshot_at':snapshot['snapshot_at'],'validation':'temporal_release_events','feature_policy':PROTOCOL,'temporal_policy':POLICY,
            'protocol':'rolling origin; release events plus reconstructed fixed attributes; all dynamic evidence and selection outcomes <= cutoff; next year untouched',
            'trees':args.trees,'summary':summarize(reports),'years':reports}
        write_json(args.output,output)
        print(f"  {r['selection']['name']}: threshold {r['threshold']:.3f}; yearly yield {r['metrics']['precision']:.3f}; recall {r['metrics']['recall']:.3f}; AP {r['metrics']['pr_auc']:.3f}; top10 recall {r['ranking']['recall']:.3f}",flush=True)
    if not reports:raise ValueError('No eligible folds')
    print(output['summary'],flush=True)


def retune(args):
    snapshot,enrichment=load_data(args.snapshot)
    artifact=load_snapshot(args.output)
    if artifact.get('feature_policy')!=PROTOCOL:raise ValueError('Cached predictions use an obsolete feature policy; retrain all years')
    if artifact['snapshot_at']!=snapshot['snapshot_at'] or artifact['target']!='eventual_next_season':
        raise ValueError('Cached annual predictions must match target and snapshot')
    updated=[]
    for r in artifact['years']:
        cutoff=parse_date(r['cutoff']);start=validation_cutoffs(cutoff)[0]
        train=[row for row in season_rows(snapshot,start,enrichment) if bucket(row['group'])>=2]
        config=next(c for c in CANDIDATES if c['id']==r['selection']['selected'])
        model,v=fit_cure(train,config,min(artifact['trees'],120))
        threshold,details=screening_threshold(model,v,snapshot,cutoff,enrichment)
        selection={**r['selection'],**details}
        predictions=r['predictions']
        rows=[{'id':p['id'],'group':p['group'],'cutoff':r['cutoff'],'outcome_end':r['outcome_end'],'y':p['actual']} for p in predictions]
        revised=annual_report(rows,[p['probability'] for p in predictions],[p['annual_probability'] for p in predictions],r['cutoff'],threshold,selection,r['train_n'])
        revised['permutation']=r.get('permutation',[])
        updated.append(revised)
        print(f"{r['cutoff']} pooled historical threshold {threshold:.3f}, recall {revised['metrics']['recall']:.3f}, annual yield {revised['metrics']['precision']:.3f}",flush=True)
    artifact['years']=updated;artifact['summary']=summarize(updated)
    artifact['threshold_protocol']='two_mature_prior_years_F2'
    artifact['generated_at']=datetime.now(timezone.utc).isoformat()
    write_json(args.output,artifact)
    print(artifact['summary'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',default='.cache/latest.json')
    p.add_argument('--start-year',type=int,default=2010);p.add_argument('--end-year',type=int,default=9999)
    p.add_argument('--trees',type=int,default=160);p.add_argument('--output',default='data/backtest-years.json')
    p.add_argument('--retune-thresholds',action='store_true',help='Reuse matching cached probabilities; fit only earlier validation models to update retrieval thresholds')
    args=p.parse_args()
    retune(args) if args.retune_thresholds else run(args)
