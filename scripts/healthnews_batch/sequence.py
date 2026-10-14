"""Run predeclared low-concurrency diagnostics, gate a separate complete rerun."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from .config import load_config, digest, _relative
from scripts.healthnews_snapshot import save


def stability(rows, expected):
    errors=sum(len(r.get('runtime_errors', [])) for r in rows)
    incidents=sum(r.get('run_status') != 'completed' for r in rows)
    return {'stable':len(rows)==expected and not errors and not incidents,
            'completed':len(rows),'expected':expected,'runtime_error_events':errors,
            'incident_runs':incidents,'kept_all_fail_codes':True}


def read_rows(config):
    with sqlite3.connect('file:'+str(config.output/'journal.sqlite')+'?mode=ro',uri=True) as db:
        identity=db.execute('SELECT version FROM config').fetchone()
        if not identity or identity[0] != config.version:raise ValueError('sequence journal identity mismatch')
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM results')]


def validate(configs, ids):
    low, higher, controlled=configs
    if len(ids)!=8 or len(set(ids))!=8:raise ValueError('fixed eight diagnostic IDs required')
    for c in configs:
        if c.corpus_mode!='live_observed' or c.raw['selected_arms']!=['B','D']:
            raise ValueError('sequence requires live paired B/D')
        if not c.raw.get('diagnostics',{}).get('enabled'):raise ValueError('timing must be enabled')
        if c.raw.get('budget',{})!=low.raw.get('budget',{}) or {k:v for k,v in c.raw['batch'].items() if k!='concurrency'}!={k:v for k,v in low.raw['batch'].items() if k!='concurrency'}:
            raise ValueError('budget or non-concurrency batch settings differ')
        if c.raw['models']!=low.raw['models'] or c.raw['settings']!=low.raw['settings'] or c.raw['code']!=low.raw['code'] or c.snapshots!=low.snapshots:
            raise ValueError('models/settings/code/baseline differ across study stages')
    if low.cases!=higher.cases or [c['id'] for c in low.cases]!=ids:
        raise ValueError('diagnostic cohort changed')
    if [c.raw['batch']['concurrency'] for c in configs]!=[1,4,1] or [c.raw['rounds'] for c in configs]!=[1,1,3]:
        raise ValueError('predeclared concurrency/rounds changed')
    if len(controlled.cases)!=100 or any(c not in controlled.cases for c in low.cases):
        raise ValueError('complete original cohort required for controlled rerun')
    if len({str(c.output) for c in configs})!=3 or len({c.version for c in configs})!=3:
        raise ValueError('study stages must use separate identities and journals')
    if [c.raw.get('study_role') for c in configs]!=['diagnostic','diagnostic','controlled_rerun']:
        raise ValueError('study roles must be explicit')


async def execute(plan_path, *, runner=None, loader=load_config, reader=read_rows):
    if runner is None:
        from scripts.healthnews_batch_experiment import main
        runner=main
    plan_path=Path(plan_path).resolve();plan=json.loads(plan_path.read_text())
    paths=[_relative(plan_path.parent,plan[k]) for k in ('low','higher','controlled')]
    configs=[loader(p,require_snapshots=True) for p in paths]
    validate(configs,plan['diagnostic_ids'])
    if [c.version for c in configs]!=plan.get('config_versions'):
        raise ValueError('sequence configuration identity drifted')
    if configs[2].raw['dataset']['sha256']!=plan.get('original_dataset_sha256') or digest(configs[2].cases)!=plan.get('original_cases_sha256'):
        raise ValueError('original cohort identity changed')
    status_path=plan_path.parent/'sequence-output/sequence-status.json'
    status_path.parent.mkdir(parents=True,exist_ok=True)
    status={'status':'STARTING','plan_sha256':digest(plan),'config_versions':[c.version for c in configs],
            'stages':[],'original_results_modified':False,'no_failed_case_filtering':True}
    with status_path.open('x') as f:json.dump(status,f)
    async def stage(index):
        config=configs[index]
        status.update(status='RUNNING',active_stage=plan[('low','higher','controlled')[index]])
        save(status_path,status)
        report=await runner(SimpleNamespace(command='run',submit=True,config=paths[index]))
        if report.get('status')!='COMPLETED_PENDING_REVIEW' or report.get('corpus_verification',{}).get('status')!='ENDPOINTS_MATCHED':
            raise ValueError('stage lacks successful complete physical verification')
        rows=reader(config)
        expected={(c['id'],a,n) for c in config.cases for a in ('B','D') for n in range(1,config.raw['rounds']+1)}
        actual={(r.get('case_id'),r.get('arm'),r.get('round')) for r in rows}
        if actual!=expected or len(rows)!=len(expected):raise ValueError('incomplete or duplicated paired cohort')
        metrics=stability(rows,config.planned_runs)
        status['stages'].append({'config_version':config.version,'study_role':config.raw['study_role'],**metrics})
        save(status_path,status)
        return metrics
    try:
        low=await stage(0)
        await stage(1)
        status['low_concurrency_gate']=low
        if not low['stable']:
            status['status']='DIAGNOSTIC_UNSTABLE_FULL_RERUN_NOT_STARTED'
            save(status_path,status)
            return status
        status['controlled_authorized_by_gate']=True
        controlled=await stage(2)
        status.update(status='COMPLETED_PENDING_ANSWER_REVIEW' if controlled['stable'] else 'CONTROLLED_COMPLETED_WITH_INCIDENTS_QUALITY_EVALUATION_BLOCKED',answer_quality_review='pending')
        save(status_path,status)
        return status
    except BaseException as exc:
        status.update(status='FAILED_REQUIRES_INVESTIGATION',error_type=type(exc).__name__)
        save(status_path,status)
        raise


def cli():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True,type=Path)
    args=p.parse_args()
    try:
        result=asyncio.run(execute(args.plan));print(json.dumps(result,ensure_ascii=False))
        return 0 if result['status'] in ('COMPLETED_PENDING_ANSWER_REVIEW','CONTROLLED_COMPLETED_WITH_INCIDENTS_QUALITY_EVALUATION_BLOCKED') else 2
    except Exception as exc:
        print(json.dumps({'status':'FAILED_REQUIRES_INVESTIGATION','error_type':type(exc).__name__}))
        return 2

if __name__=='__main__':raise SystemExit(cli())
