"""Optional timing, without changing clinical decisions or duplicating error events."""
import asyncio
from contextlib import contextmanager, asynccontextmanager
import time
from scripts.healthnews_experiment import _record

@contextmanager
def measured(stage):
    row = _record.get()
    timing = {'stage': stage, 'status': 'started'}
    if row is not None:
        row.setdefault('diagnostic_timings', []).append(timing)
    start = time.perf_counter()
    try:
        yield timing
        timing['status'] = 'completed'
    except BaseException as exc:
        timing.update(status='cancelled' if isinstance(exc, asyncio.CancelledError) else 'error', error_type=type(exc).__name__)
        raise
    finally:
        timing['latency_ms'] = round((time.perf_counter()-start)*1000, 3)

class MeasuredConnection:
    def __init__(self, connection):self.connection=connection
    def __getattr__(self, name):return getattr(self.connection,name)
    async def fetch(self,*args,**kwargs):
        with measured('pg_query'):
            return await self.connection.fetch(*args,**kwargs)
    async def execute(self,*args,**kwargs):
        with measured('pg_session_setting'):
            return await self.connection.execute(*args,**kwargs)

class MeasuredPool:
    def __init__(self,pool):self.pool=pool
    def __getattr__(self,name):return getattr(self.pool,name)
    @asynccontextmanager
    async def acquire(self):
        manager=self.pool.acquire()
        with measured('pg_pool_wait'):
            connection=await manager.__aenter__()
        try:
            yield MeasuredConnection(connection)
        except BaseException as exc:
            if not await manager.__aexit__(type(exc),exc,exc.__traceback__):raise
        else:
            await manager.__aexit__(None,None,None)

class MeasuredEmbeddings:
    def __init__(self,embeddings):self.embeddings=embeddings
    async def aembed_query(self,*args,**kwargs):
        with measured('embedding_rpc'):
            return await self.embeddings.aembed_query(*args,**kwargs)

class MeasuredRetriever:
    def __init__(self,retriever,stage):self.retriever,self.stage=retriever,stage
    async def ainvoke(self,*args,**kwargs):
        with measured(self.stage):
            return await self.retriever.ainvoke(*args,**kwargs)

class LoopProbe:
    def __init__(self,interval=.1):
        self.interval=interval;self.task=None;self.samples=0;self.total=0.;self.maximum=0.;self.over_100ms=0
    async def start(self):
        self.task=asyncio.create_task(self._run())
        await asyncio.sleep(0)
    async def _run(self):
        while True:
            start=time.perf_counter()
            await asyncio.sleep(self.interval)
            lag=max(0.,(time.perf_counter()-start-self.interval)*1000)
            self.samples+=1;self.total+=lag;self.maximum=max(self.maximum,lag);self.over_100ms+=lag>=100
    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task,return_exceptions=True)
        return {'samples':self.samples,'mean_lag_ms':self.total/self.samples if self.samples else 0,
                'max_lag_ms':self.maximum,'over_100ms':self.over_100ms,'interval_seconds':self.interval}
