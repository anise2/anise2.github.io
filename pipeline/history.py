"""Persist compressed monthly metadata snapshots as GitHub Release assets (no images).

    GH_TOKEN and GITHUB_REPOSITORY are provided only by Actions. Snapshots must outlive
    ordinary CI artifacts for a multi-year, genuinely prospective evaluation.
"""
import argparse
import gzip
import json
import os
import subprocess
from datetime import date
from pathlib import Path
from pipeline.common import add_year, load_snapshot, parse_date, write_json
import re

TAG = 'training-snapshots'


def gh(*args, check=True):
    result = subprocess.run(['gh', *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result


def metadata():
    repo = os.environ['GITHUB_REPOSITORY']
    result = gh('api', f'repos/{repo}/releases/tags/{TAG}', check=False)
    if result.returncode:
        if '404' in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def store(snapshot_path, directory):
    snapshot = load_snapshot(snapshot_path)
    if snapshot.get('mode') != 'archive':
        raise ValueError('Only official Archive snapshots may enter the historical store')
    meta = metadata()
    if meta is None:
        gh('release', 'create', TAG, '--repo', os.environ['GITHUB_REPOSITORY'], '--title', 'Historical Bangumi training snapshots', '--notes', 'Monthly immutable normalized metadata snapshots. No images. Used for time-controlled evaluation.', '--prerelease')
        meta = metadata()
    name = f"snapshot-{snapshot['snapshot_at'][:7]}.json.gz"
    if any(a['name'] == name for a in meta['assets']):
        print('Monthly snapshot already exists; keeping its original observation date.')
        return
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(',', ':'))
    gh('release', 'upload', TAG, str(path), '--repo', os.environ['GITHUB_REPOSITORY'])
    print(f'Preserved {name}')


def restore(directory):
    meta = metadata()
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    if meta is None:
        print('No historical snapshot release yet.')
        return
    assets = sorted([a for a in meta['assets'] if a['name'].startswith('snapshot-') and a['name'].endswith('.json.gz')], key=lambda a:a['name'], reverse=True)
    # At most four annual cohorts bound RAM use on ordinary GitHub CPU runners.
    # Keep all monthly assets remotely; select yearly-spaced candidates to restore.
    selected, before = [], date.today()
    for a in assets:
        month = parse_date(a['name'][9:16] + '-01')
        if month and month <= before:
            selected.append(a)
            before = date(month.year - 1, month.month, 1)
            if len(selected) == 4:
                break
    for asset in selected:
        gh('release', 'download', TAG, '--repo', os.environ['GITHUB_REPOSITORY'], '--pattern', asset['name'], '--dir', str(out), '--clobber')
        path = out / asset['name']
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            snapshot = json.load(f)
        (out / f"{snapshot['snapshot_at']}.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
    print(f'Restored {len(selected)} real historical snapshots')


def evidence(action, cache, output):
    """A replaceable lookup checkpoint, separate from immutable monthly snapshots.

    GitHub cache eviction must not restart the external lookup queue each week.
    Only reduced text/numeric metadata is saved; no images or crosswalk binaries.
    """
    name='evidence-checkpoint.json.gz'
    meta=metadata()
    if meta is None:
        if action=='evidence-restore':
            print('No evidence checkpoint yet.');return
        raise ValueError('Create the monthly snapshot release before checkpointing evidence')
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    path=cache/name
    if action=='evidence-store':
        payload={'evidence':load_snapshot(output),'lookups':{p.name:load_snapshot(p) for p in (cache/'mangaupdates').glob('*.json')}}
        with gzip.open(path,'wt',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False,separators=(',',':'))
        gh('release','upload',TAG,str(path),'--repo',os.environ['GITHUB_REPOSITORY'],'--clobber')
        print(f'Checkpointed {len(payload["lookups"])} metadata lookups.')
    elif any(a['name']==name for a in meta['assets']):
        gh('release','download',TAG,'--repo',os.environ['GITHUB_REPOSITORY'],'--pattern',name,'--dir',str(cache),'--clobber')
        payload=load_snapshot(path)
        for name,value in payload.get('lookups',{}).items():
            if not re.fullmatch(r'\d+\.json',name):raise ValueError('Unexpected checkpoint lookup filename')
            dest=cache/'mangaupdates'/name
            if not dest.exists() or load_snapshot(dest).get('observed_at','') < value.get('observed_at',''):
                write_json(dest,value)
        if not Path(output).exists() or load_snapshot(output).get('observed_at','') <= payload['evidence'].get('observed_at',''):
            write_json(output,payload['evidence'])
        print(f'Restored {len(payload.get("lookups",{}))} lookup records.')


def catalog(action, directory, output):
    """Share the latest trained results with releases that only change the UI."""
    name='site-catalog.json.gz'
    meta=metadata()
    available=meta and any(a['name']==name for a in meta['assets'])
    if action=='catalog-restore' and not available:
        print('No trained catalog release yet; using the repository catalog.')
        return
    if meta is None:
        raise ValueError('Create the snapshot release before storing the trained catalog')
    path=Path(directory)/name
    path.parent.mkdir(parents=True,exist_ok=True)
    if action=='catalog-restore':
        gh('release','download',TAG,'--repo',os.environ['GITHUB_REPOSITORY'],
           '--pattern',name,'--dir',str(path.parent),'--clobber')
    payload=load_snapshot(output if action=='catalog-store' else path)
    if payload.get('schema_version')!=1 or not payload.get('items') or not payload.get('model'):
        raise ValueError('Invalid trained catalog; refusing to replace website data')
    if action=='catalog-store':
        with gzip.open(path,'wt',encoding='utf-8') as f:
            json.dump(payload,f,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        gh('release','upload',TAG,str(path),'--repo',os.environ['GITHUB_REPOSITORY'],'--clobber')
    else:
        write_json(output,payload)
    print(f'{action}: {len(payload["items"])} series, generated {payload.get("generated_at")}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['store', 'restore', 'evidence-store', 'evidence-restore', 'catalog-store', 'catalog-restore'])
    p.add_argument('--snapshot', default='.cache/latest.json')
    p.add_argument('--directory', default='.cache/history')
    p.add_argument('--cache', default='.cache/research')
    p.add_argument('--evidence', default='data/external/enrichment.json')
    p.add_argument('--catalog', default='data/catalog.json')
    a = p.parse_args()
    if a.action.startswith('catalog-'):catalog(a.action,a.directory,a.catalog)
    elif a.action.startswith('evidence-'):evidence(a.action,a.cache,a.evidence)
    else:store(a.snapshot, a.directory) if a.action == 'store' else restore(a.directory)
