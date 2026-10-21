"""Shared vocabulary, franchise partitioning and retrieval metrics."""
import hashlib
from collections import Counter

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import (average_precision_score, precision_recall_curve,
    accuracy_score, balanced_accuracy_score, brier_score_loss, confusion_matrix,
    precision_score, recall_score, roc_auc_score, fbeta_score)

from pipeline.common import COMMUNITY_FEATURES, feature_label

def bucket(group):
    return int(hashlib.sha256(str(group).encode()).hexdigest()[:8],16) % 5


def vectorize(rows, max_tags=500, community=True):
    # Count unique subjects, not repeated annual negative windows.
    subjects = {}
    for row in rows:
        subjects.setdefault(row['id'],row['features'])
    counts = Counter(k for f in subjects.values() for k in f if k.startswith(('tag=','studio=')))
    keep = {k for k,n in counts.most_common(max_tags) if n >= 3}
    clean = [{k:v for k,v in r['features'].items() if (not k.startswith(('tag=','studio=')) or k in keep)
              and (community or k not in COMMUNITY_FEATURES)} for r in rows]
    v = DictVectorizer(sparse=False,dtype=np.float32)
    X = v.fit_transform(clean)
    return v, X, np.array([r['y'] for r in rows])


def choose_threshold(y,p):
    precision, recall, thresholds = precision_recall_curve(y,p)
    # F2 values recall twice as much as precision, with a precision floor to
    # prevent the trivial all-positive classifier from appearing successful.
    floor = max(.10,2*float(np.mean(y)))
    score = 5*precision[:-1]*recall[:-1]/np.maximum(4*precision[:-1]+recall[:-1],1e-12)
    eligible = precision[:-1] >= floor
    constrained = bool(eligible.any())
    if constrained:
        score = np.where(eligible,score,-1)
    index = int(np.flatnonzero(score == score.max())[-1])
    return float(thresholds[index]), {'objective':'F2', 'precision_floor':floor,'floor_satisfied':constrained,
        'validation_f2':float(score[index]), 'validation_precision':float(precision[index]),
        'validation_recall':float(recall[index]),'n':len(y),'positive_n':int(np.sum(y))}


def metrics(y,p,threshold,prevalence):
    y=np.array(y);p=np.array(p);pred=p>=threshold
    return {'accuracy':float(accuracy_score(y,pred)), 'balanced_accuracy':float(balanced_accuracy_score(y,pred)),
        'precision':float(precision_score(y,pred,zero_division=0)), 'recall':float(recall_score(y,pred,zero_division=0)),
        'f2':float(fbeta_score(y,pred,beta=2,zero_division=0)), 'brier':float(brier_score_loss(y,p)),
        'baseline_accuracy':float(np.mean(y == int(prevalence >= .5))),
        'baseline_brier':float(brier_score_loss(y,np.full(len(y),prevalence))),
        'roc_auc':float(roc_auc_score(y,p)) if len(set(y))==2 else None,
        'pr_auc':float(average_precision_score(y,p)) if y.sum() else None}
