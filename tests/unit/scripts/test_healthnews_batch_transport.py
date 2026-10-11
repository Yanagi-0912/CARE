import asyncio
import pytest
from scripts.healthnews_batch.store import Journal
from scripts.healthnews_batch.transport import BatchCoordinator

class Provider:

    def __init__(self, mode='reverse'):
        self.jobs = {}
        self.mode = mode
        self.creates = 0

    async def create(self, model, requests, display_name):
        self.creates += 1
        if self.mode == 'ambiguous':
            raise ConnectionError('lost reply')
        self.jobs['job'] = requests
        return {'name': 'job', 'state': 'JOB_STATE_PENDING'}

    async def get(self, name):
        rows = [{'key': r['metadata']['key'], 'response': {'text': r['contents'], 'usage_metadata': {'total_token_count': 3}}} for r in self.jobs[name]]
        if self.mode == 'missing':
            rows = rows[:-1]
        if self.mode == 'duplicate':
            rows.append(rows[0])
        return {'state': 'JOB_STATE_SUCCEEDED', 'responses': list(reversed(rows))}

@pytest.mark.asyncio
async def test_round_identity_restart_and_reversed_results(tmp_path):
    j = Journal(tmp_path / 'j.db')
    j.bind_config('v', {'a': 1})
    p = Provider()
    c = BatchCoordinator(j, p, 'v', poll_interval=0)
    tasks = [asyncio.create_task(c.request(f'round{i}', 'generation', 0, 'model', {'contents': str(i)})) for i in range(2)]
    rows = await c.drive(tasks)
    assert [r['text'] for r in rows] == ['0', '1']
    c2 = BatchCoordinator(j, p, 'v', poll_interval=0)
    assert (await c2.request('round0', 'generation', 0, 'model', {'contents': '0'}))['text'] == '0'
    assert p.creates == 1
    with pytest.raises(ValueError):
        await c2.request('round0', 'generation', 0, 'model', {'contents': 'changed'})
    with pytest.raises(ValueError):
        j.bind_config('v', {'a': 2})
    j.save_receipt('r', 'local', 'hash', 'done', {'x': 1})
    with pytest.raises(ValueError):
        j.get_receipt('r', 'local', 'other')

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'ambiguous'])
async def test_fail_closed(tmp_path, mode):
    j = Journal(tmp_path / 'j.db')
    p = Provider(mode)
    c = BatchCoordinator(j, p, 'v', poll_interval=0)
    tasks = [asyncio.create_task(c.request('r', 'g', 0, 'm', {'contents': 'x'}))]
    with pytest.raises(RuntimeError):
        await c.drive(tasks)
    if mode == 'ambiguous':
        assert c.status()['jobs'][0]['state'] == 'SUBMIT_UNKNOWN'
        c2 = BatchCoordinator(j, p, 'v', poll_interval=0)
        with pytest.raises(RuntimeError):
            await c2.request('r', 'g', 0, 'm', {'contents': 'x'})
        assert p.creates == 1

@pytest.mark.asyncio
async def test_cancelled_before_submission(tmp_path):
    j = Journal(tmp_path / 'j.db')
    p = Provider()
    c = BatchCoordinator(j, p, 'v', poll_interval=0)
    t = asyncio.create_task(c.request('r', 'g', 0, 'm', {'contents': 'x'}))
    await asyncio.sleep(0)
    t.cancel()
    await asyncio.sleep(0)
    await c.drive([t])
    assert p.creates == 0

@pytest.mark.asyncio
async def test_cancelled_after_submit_preserves_usage(tmp_path):

    class CancellingProvider(Provider):

        async def create(self, model, requests, display_name):
            reply = await super().create(model, requests, display_name)
            task.cancel()
            await asyncio.sleep(0)
            return reply
    j = Journal(tmp_path / 'j.db')
    p = CancellingProvider()
    c = BatchCoordinator(j, p, 'v', poll_interval=0)
    task = asyncio.create_task(c.request('r', 'g', 0, 'm', {'contents': 'x'}))
    await c.drive([task])
    row = c.status()['requests'][0]
    import json
    response = json.loads(row['response'])
    assert response['usage_metadata']['total_token_count'] == 3
    assert response['response_metadata']['local_cancelled'] is True
    assert response['response_metadata']['local_delivery_cancelled'] is True

@pytest.mark.asyncio
async def test_terminal_failure_and_raw_output_preserved(tmp_path):

    class FailedProvider(Provider):

        async def get(self, name):
            return {'state': 'JOB_STATE_FAILED', 'error': {'message': 'quota'}}
    j = Journal(tmp_path / 'j.db')
    c = BatchCoordinator(j, FailedProvider(), 'v', poll_interval=0)
    t = asyncio.create_task(c.request('r', 'g', 0, 'm', {'contents': 'x'}))
    with pytest.raises(RuntimeError, match='quota'):
        await c.drive([t])
    assert 'quota' in c.status()['jobs'][0]['raw_output']

@pytest.mark.asyncio
async def test_unknown_attach_resumes_without_create(tmp_path):
    j = Journal(tmp_path / 'j.db')
    p = Provider('ambiguous')
    c = BatchCoordinator(j, p, 'v', poll_interval=0)
    t = asyncio.create_task(c.request('r', 'g', 0, 'm', {'contents': 'x'}))
    with pytest.raises(RuntimeError):
        await c.drive([t])
    job = c.status()['jobs'][0]
    row = c.status()['requests'][0]
    import json
    p.mode = 'reverse'
    p.jobs['recovered'] = [json.loads(row['payload'])]
    c.attach_job(job['id'], 'recovered')
    c2 = BatchCoordinator(j, p, 'v', poll_interval=0)
    t = asyncio.create_task(c2.request('r', 'g', 0, 'm', {'contents': 'x'}))
    assert (await c2.drive([t]))[0]['text'] == 'x'
    assert p.creates == 1

@pytest.mark.asyncio
async def test_provider_serializes_sdk_inline_requests_without_network():
    from google.genai import types
    from scripts.healthnews_batch.transport import GeminiBatchProvider
    class Batches:
        async def create(self, model, src, config):
            self.rows=[types.InlinedRequest.model_validate(r).model_dump(mode='json',exclude_none=True) for r in src]
            return types.BatchJob(name='jobs/offline',state='JOB_STATE_PENDING')
    class Aio:
        def __init__(self): self.batches=Batches()
    class Client:
        def __init__(self): self.aio=Aio()
    client=Client(); p=GeminiBatchProvider(None,client=client)
    out=await p.create('model',[{'contents':[{'role':'user','parts':[{'text':'hello'}]}],'config':{'response_mime_type':'application/json','response_schema':{'type':'OBJECT','properties':{'answer':{'type':'STRING'}}},'thinking_config':{'thinking_level':'LOW'}},'metadata':{'key':'identity'}}],'display')
    assert out['name']=='jobs/offline'
    assert client.aio.batches.rows[0]['metadata']['key']=='identity'
    assert client.aio.batches.rows[0]['config']['thinking_config']['thinking_level']=='LOW'

@pytest.mark.asyncio
async def test_malformed_result_fails_waiter_and_persists_failure(tmp_path):
    class MalformedProvider(Provider):
        async def get(self,name): return {'state':'JOB_STATE_SUCCEEDED','responses':[None]}
    j=Journal(tmp_path/'j.db'); c=BatchCoordinator(j,MalformedProvider(),'v',poll_interval=0)
    t=asyncio.create_task(c.request('r','g',0,'m',{'contents':'x'}))
    with pytest.raises(RuntimeError,match='Malformed'): await c.drive([t])
    assert c.status()['requests'][0]['state']=='FAILED'

@pytest.mark.asyncio
async def test_submission_error_does_not_persist_credentials(tmp_path):
    class SecretProvider(Provider):
        async def create(self,model,requests,display_name):
            raise ConnectionError('https://api.example/path?key=secret')
    j=Journal(tmp_path/'j.db'); c=BatchCoordinator(j,SecretProvider(),'v',poll_interval=0)
    t=asyncio.create_task(c.request('r','g',0,'m',{'contents':'x'}))
    with pytest.raises(RuntimeError): await c.drive([t])
    assert 'secret' not in c.status()['jobs'][0]['raw_output']

@pytest.mark.asyncio
async def test_payload_near_inline_limit_rejected_before_submit(tmp_path):
    from scripts.healthnews_batch.store import encode
    j=Journal(tmp_path/'j.db'); c=BatchCoordinator(j,Provider(),'v',poll_interval=0)
    # Delimiters and request key metadata must count toward the total limit.
    overhead=len(encode({'contents':'','metadata':{'key':'x'*64}}).encode())
    request={'contents':'x'*(19_000_000-overhead-1)}
    with pytest.raises(ValueError): await asyncio.wait_for(c.request('r','g',0,'m',request),timeout=1)

@pytest.mark.asyncio
async def test_orphaned_queued_requests_require_explicit_readoption(tmp_path):
    import json
    path=tmp_path/'j.db'; original=Journal(path)
    c1=BatchCoordinator(original,Provider(),'v',poll_interval=0)
    stale=asyncio.create_task(c1.request('neverused','g',0,'m',{'contents':'orphan'}))
    resumed=asyncio.create_task(c1.request('resume','g',0,'m',{'contents':'resumed'}))
    await asyncio.sleep(0)
    # A reopened coordinator has no live caller for either persisted request.
    new=Journal(path); p=Provider(); c2=BatchCoordinator(new,p,'v',poll_interval=0)
    async def delayed_resume():
        await asyncio.sleep(0.01)
        assert p.creates==0
        return await c2.request('resume','g',0,'m',{'contents':'resumed'})
    task=asyncio.create_task(delayed_resume())
    assert (await c2.drive([task]))[0]['text']=='resumed'
    assert len(p.jobs['job'])==1
    orphan=[r for r in c2.status()['requests'] if json.loads(r['payload'])['contents']=='orphan'][0]
    assert orphan['state']=='QUEUED' and orphan['job'] is None
    stale.cancel(); resumed.cancel()
    await asyncio.gather(stale,resumed,return_exceptions=True)

@pytest.mark.asyncio
async def test_cancellation_during_other_model_submission_does_not_submit(tmp_path):
    class CancellingFirst(Provider):
        async def create(self, model, requests, display_name):
            self.models=getattr(self,'models',[])+[model]
            if model=='model0':
                second.cancel()
                await asyncio.sleep(0)
            self.jobs[model]=requests
            return {'name':model,'state':'JOB_STATE_PENDING'}
    j=Journal(tmp_path/'j.db'); p=CancellingFirst(); c=BatchCoordinator(j,p,'v',poll_interval=0)
    first=asyncio.create_task(c.request('r','g',0,'model0',{'contents':'first'}))
    second=asyncio.create_task(c.request('r','g',1,'model1',{'contents':'second'}))
    await c.drive([first,second])
    assert p.models==['model0']

@pytest.mark.asyncio
async def test_downloaded_rest_jsonl_normalizes_usage_and_preserves_raw():
    import json
    from google.genai import types
    from scripts.healthnews_batch.transport import GeminiBatchProvider,normalize
    raw={'candidates':[{'content':{'parts':[{'text':'answer'}]}}],'usageMetadata':{'promptTokenCount':11,'candidatesTokenCount':7,'totalTokenCount':18,'thoughtsTokenCount':2},'modelVersion':'version'}
    class Batches:
        async def get(self,name):
            return types.BatchJob(name=name,state='JOB_STATE_SUCCEEDED',dest=types.BatchJobDestination(file_name='files/results'))
    class Files:
        async def download(self,file):
            assert file=='files/results'
            return json.dumps({'key':'identity','response':raw}).encode()
    class Aio:
        batches=Batches(); files=Files()
    class Client:
        aio=Aio()
    p=GeminiBatchProvider(None,client=Client()); out=await p.get('jobs/test')
    result=normalize(out['responses'][0]['response'])
    assert result['usage_metadata']['prompt_token_count']==11
    assert result['usage_metadata']['candidates_token_count']==7
    assert result['usage_metadata']['total_token_count']==18
    assert result['usage_metadata']['thoughts_token_count']==2
    assert result['response_metadata']['model_version']=='version'
    assert result['raw_response']==raw

def test_started_receipt_becomes_uncertain_on_reopen(tmp_path):
    path=tmp_path/'j.db'; j=Journal(path)
    j.save_receipt('r','rerank','hash','started',{})
    j.close(); reopened=Journal(path)
    assert reopened.get_receipt('r','rerank','hash')['status']=='unknown'
    with pytest.raises(ValueError,match='manual.*reconcile'):
        reopened.assert_no_uncertain_receipts()
    reopened.save_receipt('r','rerank','hash','done',{'result':'attached'})
    reopened.assert_no_uncertain_receipts()

@pytest.mark.asyncio
async def test_uncertain_receipt_blocks_request_and_drive(tmp_path):
    j=Journal(tmp_path/'j.db'); p=Provider(); c=BatchCoordinator(j,p,'v',poll_interval=0)
    task=asyncio.create_task(c.request('r','generation',0,'m',{'contents':'queued'}))
    await asyncio.sleep(0)
    j.save_receipt('r','rerank','hash','unknown',{})
    with pytest.raises(ValueError,match='manual.*reconcile'):
        await asyncio.wait_for(c.request('r','fallback',0,'m',{'contents':'other'}),timeout=0.05)
    with pytest.raises(ValueError,match='manual.*reconcile'):
        await c.drive([task])
    assert p.creates==0

@pytest.mark.asyncio
async def test_explicit_readoption_of_cancelled_unsubmitted_request(tmp_path):
    j=Journal(tmp_path/'j.db'); p=Provider(); old=BatchCoordinator(j,p,'v',poll_interval=0)
    task=asyncio.create_task(old.request('r','g',0,'m',{'contents':'retry'}))
    await asyncio.sleep(0); task.cancel()
    await asyncio.gather(task,return_exceptions=True)
    assert old.status()['requests'][0]['state']=='CANCELLED'
    new=BatchCoordinator(j,p,'v',poll_interval=0)
    with pytest.raises(ValueError): await new.request('r','g',0,'m',{'contents':'different'})
    retry=asyncio.create_task(new.request('r','g',0,'m',{'contents':'retry'}))
    assert (await new.drive([retry]))[0]['text']=='retry'
    assert p.creates==1
