import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from scripts.healthnews_experiment import _record

async def test_pool_records_wait_and_query_separately_without_changing_results():
    from scripts.healthnews_batch.diagnostics import MeasuredPool
    class Connection:
        async def fetch(self,*args):
            await asyncio.sleep(.002)
            return ['same-result']
    class Pool:
        @asynccontextmanager
        async def acquire(self):
            await asyncio.sleep(.005)
            yield Connection()
    row={};token=_record.set(row)
    try:
        async with MeasuredPool(Pool()).acquire() as conn:
            assert await conn.fetch('SELECT id')==['same-result']
    finally:_record.reset(token)
    assert {x['stage'] for x in row['diagnostic_timings']}=={'pg_pool_wait','pg_query'}
    assert all(x['status']=='completed' and x['latency_ms']>0 for x in row['diagnostic_timings'])

async def test_pool_cancelled_wait_is_recorded_and_not_delivered():
    from scripts.healthnews_batch.diagnostics import MeasuredPool
    entered=asyncio.Event()
    class Pool:
        @asynccontextmanager
        async def acquire(self):
            entered.set();await asyncio.Event().wait();yield None
    row={}
    async def waiter():
        token=_record.set(row)
        try:
            async with MeasuredPool(Pool()).acquire():raise AssertionError('acquired')
        finally:_record.reset(token)
    task=asyncio.create_task(waiter());await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert row['diagnostic_timings'][0]['status']=='cancelled'
    assert 'runtime_errors' not in row

async def test_loop_probe_closes_and_reports_scheduler_lag():
    from scripts.healthnews_batch.diagnostics import LoopProbe
    probe=LoopProbe(.001);await probe.start();await asyncio.sleep(.005);report=await probe.close()
    assert report['samples']>0 and report['max_lag_ms']>=0
    assert probe.task.done()


def test_stability_gate_keeps_kb_empty_but_rejects_technical_errors():
    from scripts.healthnews_batch.sequence import stability
    rows=[{'run_status':'completed','runtime_errors':[],'fail_code':'KB_EMPTY'},
          {'run_status':'completed','runtime_errors':[],'fail_code':None}]
    assert stability(rows,2)['stable'] is True
    rows[0]['runtime_errors']=[{'error_type':'TimeoutError'}]
    assert stability(rows,2)['stable'] is False
    assert stability(rows,3)['stable'] is False


def sequence_fixture(tmp_path):
    import json
    all_cases=[{'id':f'HN{i:03}','question':f'題目{i}？','language':'zh-TW'} for i in range(1,101)]
    configs={}
    for name,concurrency,rounds,cases in [('low',1,1,all_cases[:8]),('higher',4,1,all_cases[:8]),('controlled',1,3,all_cases)]:
        configs[name]=SimpleNamespace(corpus_mode='live_observed',version=name,output=tmp_path/name/'output',cases=cases,
            planned_runs=len(cases)*2*rounds,snapshots={'new':{'same_baseline':True}},raw={'selected_arms':['B','D'],
            'diagnostics':{'enabled':True},'models':{},'settings':{},'code':{},'batch':{'concurrency':concurrency},
            'dataset':{'sha256':'a'*64},'rounds':rounds,'study_role':'controlled_rerun' if name=='controlled' else 'diagnostic'})
    plan={name:name+'/experiment.json' for name in configs};plan['diagnostic_ids']=[c['id'] for c in all_cases[:8]]
    from scripts.healthnews_batch.config import digest
    plan['original_dataset_sha256']='a'*64;plan['original_cases_sha256']=digest(all_cases);plan['config_versions']=['low','higher','controlled']
    path=tmp_path/'sequence.json';path.write_text(json.dumps(plan))
    def loader(p,**kwargs):return configs[p.parent.name]
    def reader(c):
        return [{'case_id':case['id'],'arm':arm,'round':n,'runtime_errors':[],'run_status':'completed','fail_code':'KB_EMPTY'}
            for case in c.cases for arm in ('B','D') for n in range(1,c.raw['rounds']+1)]
    return path,configs,loader,reader


async def test_sequence_gates_full_rerun_without_deleting_timeout_cases(tmp_path):
    from scripts.healthnews_batch.sequence import execute
    path,configs,loader,read=sequence_fixture(tmp_path)
    calls=[]
    async def runner(args):
        calls.append(args.config.parent.name)
        return {'status':'COMPLETED_PENDING_REVIEW','corpus_verification':{'status':'ENDPOINTS_MATCHED'}}
    def reader(c):
        rows=read(c)
        if c.version=='low':rows[0].update(runtime_errors=[{'error_type':'TimeoutError'}],run_status='completed_with_incidents')
        return rows
    result=await execute(path,runner=runner,loader=loader,reader=reader)
    assert calls==['low','higher']
    assert result['status']=='DIAGNOSTIC_UNSTABLE_FULL_RERUN_NOT_STARTED'
    assert result['stages'][0]['completed']==16
    with pytest.raises(FileExistsError):await execute(path,runner=runner,loader=loader,reader=reader)
    assert calls==['low','higher']


async def test_sequence_runs_all_original_cases_after_stable_gate_even_if_kb_empty(tmp_path):
    from scripts.healthnews_batch.sequence import execute
    path,configs,loader,reader=sequence_fixture(tmp_path)
    calls=[]
    async def runner(args):
        calls.append(args.config.parent.name)
        return {'status':'COMPLETED_PENDING_REVIEW','corpus_verification':{'status':'ENDPOINTS_MATCHED'}}
    result=await execute(path,runner=runner,loader=loader,reader=reader)
    assert calls==['low','higher','controlled']
    assert result['stages'][-1]['completed']==600
    assert result['status']=='COMPLETED_PENDING_ANSWER_REVIEW'


async def test_sequence_rejects_missing_pairs_before_full_rerun(tmp_path):
    from scripts.healthnews_batch.sequence import execute
    path,configs,loader,read=sequence_fixture(tmp_path)
    async def runner(args):return {'status':'COMPLETED_PENDING_REVIEW','corpus_verification':{'status':'ENDPOINTS_MATCHED'}}
    with pytest.raises(ValueError,match='paired cohort'):
        await execute(path,runner=runner,loader=loader,reader=lambda c:read(c)[1:])


def test_sequence_rejects_changed_budget():
    from scripts.healthnews_batch.sequence import validate
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
        _,configs,_,_=sequence_fixture(Path(directory))
        for c in configs.values():c.raw['budget']={'context_tokens':100,'candidate_total_per_run':160}
        configs['controlled'].raw['budget']={'context_tokens':101,'candidate_total_per_run':160}
        with pytest.raises(ValueError,match='budget'):
            validate(list(configs.values()),[c['id'] for c in configs['low'].cases])


async def test_sequence_rejects_replacement_of_nondiagnostic_original_case(tmp_path):
    from scripts.healthnews_batch.sequence import execute
    path,configs,loader,reader=sequence_fixture(tmp_path)
    configs['controlled'].cases[-1]={'id':'HN100','question':'被替換','language':'zh-TW'}
    async def forbidden(args):raise AssertionError('must reject before paid stage')
    with pytest.raises(ValueError,match='original cohort identity'):
        await execute(path,runner=forbidden,loader=loader,reader=reader)


async def test_controlled_incidents_are_retained_and_block_effectiveness_claim(tmp_path):
    from scripts.healthnews_batch.sequence import execute
    path,configs,loader,read=sequence_fixture(tmp_path)
    async def runner(args):return {'status':'COMPLETED_PENDING_REVIEW','corpus_verification':{'status':'ENDPOINTS_MATCHED'}}
    def reader(c):
        rows=read(c)
        if c.version=='controlled':rows[-1].update(run_status='completed_with_incidents',runtime_errors=[{'error_type':'TimeoutError'}])
        return rows
    result=await execute(path,runner=runner,loader=loader,reader=reader)
    assert result['stages'][-1]['completed']==600
    assert result['status']=='CONTROLLED_COMPLETED_WITH_INCIDENTS_QUALITY_EVALUATION_BLOCKED'


async def test_sequence_rejects_plan_identity_drift_before_paid_calls(tmp_path):
    import json
    from scripts.healthnews_batch.sequence import execute
    path,configs,loader,reader=sequence_fixture(tmp_path)
    plan=json.loads(path.read_text());plan['config_versions']=['expected-low','higher','controlled'];path.write_text(json.dumps(plan))
    async def forbidden(args):raise AssertionError('identity drift must not submit')
    with pytest.raises(ValueError,match='configuration identity'):
        await execute(path,runner=forbidden,loader=loader,reader=reader)
