"""Keyed batch transport with durable submission intent and explicit reconciliation."""
import asyncio
import hashlib
import json
import time
import uuid
from .store import encode

class BatchCoordinator:

    def __init__(self, journal, provider, config_version, poll_interval=30, max_requests=100):
        self.journal = journal
        self.db = journal.db
        self.provider = provider
        self.config_version = config_version
        self.poll_interval = poll_interval
        if not 1 <= max_requests <= 100:
            raise ValueError('max_requests must be 1..100')
        self.max_requests = max_requests
        self.waiters = {}

    async def request(self, run_key, role, occurrence, model, request):
        self.journal.assert_no_uncertain_receipts()
        key = hashlib.sha256(encode([self.config_version, run_key, role, occurrence]).encode()).hexdigest()
        digest = hashlib.sha256(encode([model, request]).encode()).hexdigest()
        row = self.db.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
        if row:
            if row['input_hash'] != digest:
                raise ValueError('Request input hash mismatch')
            if row['response']:
                return json.loads(row['response'])
            if row['state'] == 'CANCELLED' and row['job'] is None:
                # Only an explicit same-input caller can revive unpaid work.
                self.db.execute("UPDATE requests SET state='QUEUED',local_cancelled=0,queued=? WHERE key=?", (time.time(),key))
                self.db.commit()
                old_future = self.waiters.pop(key, None)
                if old_future is not None and not old_future.done():
                    old_future.cancel()
            elif row['state'] in ('FAILED', 'CANCELLED'):
                raise RuntimeError(row['error'] or 'Request is ' + row['state'])
            job = self.db.execute('SELECT state FROM jobs WHERE id=?', (row['job'],)).fetchone()
            if job and job['state'] in ('SUBMITTING', 'SUBMIT_UNKNOWN'):
                raise RuntimeError('Submission outcome unknown; attach-job required')
        else:
            payload = dict(request)
            payload['metadata'] = {**payload.get('metadata', {}), 'key': key}
            if len(encode(payload).encode()) + 2 >= 19000000:
                raise ValueError('Request exceeds inline batch size')
            self.db.execute('INSERT INTO requests (key,input_hash,model,payload,state,queued) VALUES (?,?,?,?,?,?)', (key, digest, model, encode(payload), 'QUEUED', time.time()))
            self.db.commit()
        future = self.waiters.get(key)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            self.waiters[key] = future
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            self.db.execute("UPDATE requests SET local_cancelled=1, state=CASE WHEN state='QUEUED' THEN 'CANCELLED' ELSE state END WHERE key=?", (key,))
            self.db.commit()
            raise

    def status(self):
        return {'jobs': [dict(r) for r in self.db.execute('SELECT * FROM jobs')], 'requests': [dict(r) for r in self.db.execute('SELECT * FROM requests')]}

    def attach_job(self, job_id, provider_name):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['state'] not in ('SUBMIT_UNKNOWN', 'SUBMITTING'):
            raise ValueError('Job is not awaiting reconciliation')
        if not provider_name:
            raise ValueError('Provider name required')
        self.db.execute("UPDATE jobs SET name=?,state='ACTIVE' WHERE id=?", (provider_name, job_id))
        self.db.commit()

    def reconcile(self, job_id, provider_name):
        self.attach_job(job_id, provider_name)

    def _fail(self, keys, error):
        for key in keys:
            self.db.execute("UPDATE requests SET state='FAILED',error=? WHERE key=?", (str(error), key))
            f = self.waiters.get(key)
            if f and (not f.done()):
                f.set_exception(error)
        self.db.commit()

    async def _submit(self):
        rows = [
            row
            for row in self.db.execute("SELECT * FROM requests WHERE state='QUEUED' ORDER BY queued")
            if row["key"] in self.waiters and not self.waiters[row["key"]].done()
        ]
        while rows:
            # A provider await can let later callers cancel. Re-read immediately
            # before the next durable intent; no await occurs until after commit.
            rows = [
                current
                for old in rows
                if (current := self.db.execute('SELECT * FROM requests WHERE key=?', (old['key'],)).fetchone())
                and current['state'] == 'QUEUED'
                and not current['local_cancelled']
                and current['key'] in self.waiters
                and not self.waiters[current['key']].done()
            ]
            if not rows:
                break
            model = rows[0]['model']
            batch = []
            size = 0
            for r in rows:
                n = len(r['payload'].encode()) + 2
                if r['model'] == model and len(batch) < self.max_requests and (size + n < 19000000):
                    batch.append(r)
                    size += n
            if not batch:
                self._fail([rows[0]['key']], ValueError('Request exceeds inline batch size'))
                rows = rows[1:]
                continue
            selected = {r['key'] for r in batch}
            rows = [r for r in rows if r['key'] not in selected]
            self.journal.assert_no_uncertain_receipts()
            jid = uuid.uuid4().hex
            self.db.execute('INSERT INTO jobs (id,name,state,payload,created) VALUES (?,?,?,?,?)', (jid, None, 'SUBMITTING', encode(sorted(selected)), time.time()))
            for r in batch:
                self.db.execute("UPDATE requests SET state='SUBMITTED',job=?,submitted=? WHERE key=?", (jid, time.time(), r['key']))
            self.db.commit()
            try:
                out = await self.provider.create(model, [json.loads(r['payload']) for r in batch], jid)
                if not out.get('name'):
                    raise RuntimeError('Provider omitted job name')
            except BaseException as exc:
                self.db.execute("UPDATE jobs SET state='SUBMIT_UNKNOWN',raw_output=? WHERE id=?", (encode({'error_type': type(exc).__name__, 'status_code': safe_status_code(exc)}),jid))
                self.db.commit()
                raise RuntimeError('Submission outcome unknown; attach-job required')
            self.db.execute("UPDATE jobs SET name=?,state='ACTIVE',raw_output=? WHERE id=?", (out['name'], encode(out), jid))
            self.db.commit()

    async def _poll(self):
        for job in list(self.db.execute("SELECT * FROM jobs WHERE state='ACTIVE'")):
            out = await self.provider.get(job['name'])
            state = str(out.get('state', ''))
            self.db.execute('UPDATE jobs SET raw_output=? WHERE id=?', (encode(out), job['id']))
            self.db.commit()
            keys = json.loads(job['payload'])
            if state.endswith(('FAILED', 'CANCELLED', 'EXPIRED')):
                self.db.execute('UPDATE jobs SET state=? WHERE id=?', (state, job['id']))
                self._fail(keys, RuntimeError('Provider job failed: ' + encode(out)))
                continue
            if not state.endswith('SUCCEEDED'):
                continue
            rows = out.get('responses', [])
            mapped = {}
            error = None
            if not isinstance(rows,list):
                rows=[]
                error=RuntimeError('Malformed batch responses')
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('metadata',{}),dict) or (not row.get('error') and not isinstance(row.get('response',row),dict)):
                    error=RuntimeError('Malformed batch response')
                    break
                key = row.get('key') or row.get('metadata', {}).get('key')
                if key not in keys or key in mapped:
                    error = RuntimeError('Unknown or duplicate response key')
                    break
                mapped[key] = row
            if error is None and set(mapped) != set(keys):
                error = RuntimeError('Missing batch response keys')
            if error:
                self.db.execute("UPDATE jobs SET state='INVALID_RESULTS' WHERE id=?", (job['id'],))
                self._fail(keys, error)
                continue
            for key, row in mapped.items():
                if row.get('error'):
                    self._fail([key], RuntimeError(encode(row)))
                    continue
                raw = row.get('response', row)
                normalized = normalize(raw)
                stored = self.db.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
                normalized['response_metadata'].update({'batch_key': key, 'job_name': job['name'], 'queue_latency_seconds': stored['submitted'] - stored['queued'], 'provider_latency_seconds': time.time() - stored['submitted'], 'local_cancelled': bool(stored['local_cancelled']), 'local_delivery_cancelled': bool(stored['local_cancelled'])})
                self.db.execute("UPDATE requests SET state='DONE',response=?,completed=? WHERE key=?", (encode(normalized), time.time(), key))
                f = self.waiters.get(key)
                if f and (not f.done()):
                    f.set_result(normalized)
            self.db.execute('UPDATE jobs SET state=? WHERE id=?', (state, job['id']))
            self.db.commit()

    async def drive(self, tasks):
        try:
            while True:
                await asyncio.sleep(0)
                self.journal.assert_no_uncertain_receipts()
                unknown = self.db.execute("SELECT id FROM jobs WHERE state IN ('SUBMIT_UNKNOWN','SUBMITTING')").fetchone()
                if unknown:
                    raise RuntimeError('Submission outcome unknown; attach-job required')
                await self._submit()
                await self._poll()
                active = self.db.execute("SELECT 1 FROM jobs WHERE state='ACTIVE'").fetchone()
                if all((t.done() for t in tasks)) and (not active):
                    self.journal.assert_no_uncertain_receipts()
                    break
                await asyncio.sleep(self.poll_interval)
            values = await asyncio.gather(*tasks, return_exceptions=True)
            for value in values:
                if isinstance(value, Exception):
                    raise value
            return values
        except BaseException:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

def normalize(raw):
    text = raw.get('text')
    if text is None:
        text = ''.join((p.get('text', '') for c in raw.get('candidates', []) for p in c.get('content', {}).get('parts', []) if not p.get('thought')))
    usage = dict(raw.get('usage_metadata', raw.get('usageMetadata', {})) or {})
    aliases = {
        'promptTokenCount': 'prompt_token_count',
        'candidatesTokenCount': 'candidates_token_count',
        'totalTokenCount': 'total_token_count',
        'thoughtsTokenCount': 'thoughts_token_count',
        'cachedContentTokenCount': 'cached_content_token_count',
        'toolUsePromptTokenCount': 'tool_use_prompt_token_count',
    }
    for alias, canonical in aliases.items():
        if alias in usage:
            usage.setdefault(canonical, usage[alias])
    metadata = {k: v for k, v in raw.items() if k not in ('text', 'usage_metadata', 'usageMetadata')}
    if 'modelVersion' in raw:
        metadata.setdefault('model_version', raw['modelVersion'])
    return {'text': text, 'usage_metadata': usage, 'response_metadata': metadata, 'raw_response': raw}

class GeminiBatchProvider:

    def __init__(self, api_key, client=None):
        if client is None:
            from google import genai
            client = genai.Client(api_key=api_key)
        self.client = client

    async def create(self, model, requests, display_name):
        job = await self.client.aio.batches.create(model=model, src=requests, config={'display_name': display_name})
        return job.model_dump(mode='json', exclude_none=True)

    async def get(self, name):
        job = await self.client.aio.batches.get(name=name)
        out = job.model_dump(mode='json', exclude_none=True)
        dest = out.get('dest', {})
        rows = dest.get('inlined_responses', [])
        if dest.get('file_name'):
            data = await self.client.aio.files.download(file=dest['file_name'])
            rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
        out['responses'] = rows
        return out

    async def close(self):
        await self.client.aio.aclose()


def safe_status_code(error):
    code = getattr(error, 'status_code', None)
    if code is None:
        code = getattr(error, 'code', None)
    return code if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599 else None
