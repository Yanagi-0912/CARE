"""只讀觀察現有資料庫；不建立副本、索引，也不宣稱不可變。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from scripts.healthnews_snapshot import DatabaseAdapter, scan, fingerprint
import json
import os
import tempfile


def publish_baseline(path, baseline):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        name = temporary.name
        try:
            json.dump(baseline, temporary, ensure_ascii=False, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
            os.link(name, path)  # atomic publication, fails if a baseline already exists
        finally:
            os.unlink(name)


async def observe(adapter, target):
    actual = await scan(adapter, target['mongo_collection'], target['pg_table'])
    index = await adapter.mongo_index(target['mongo_collection'], target['mongo_search_index'])
    if not index.get('queryable') or not index.get('latestDefinition'):
        raise ValueError('live search index is not queryable or definition missing')
    pg_indexes = await adapter.pg_indexes(target['pg_table'])
    return {**target, **actual, 'mongo_search_definition_sha256': fingerprint(index['latestDefinition']),
            'pg_indexes_sha256': fingerprint(pg_indexes), 'status': 'live_observed',
            'immutable': False, 'observed_at': datetime.now(timezone.utc).isoformat()}


async def capture(config, settings):
    import asyncpg
    from motor.motor_asyncio import AsyncIOMotorClient
    if config.corpus_mode != 'live_observed':
        raise ValueError('observe-live requires explicit live_observed mode')
    path = config.path.parent / config.raw['snapshots']['new']
    if path.exists() or (config.output / 'journal.sqlite').exists():
        raise ValueError('live baseline exists or journal started; use a new experiment directory')
    conn = None
    mongo = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=10000)
    try:
        conn = await asyncpg.connect(settings.PGVECTOR_DSN, timeout=10, command_timeout=60,
                                     server_settings={'default_transaction_read_only': 'on'})
        options = SimpleNamespace(**config.raw['settings'])
        adapter = DatabaseAdapter(mongo, conn, options)
        first = await observe(adapter, config.raw['live_corpus'])
        second = await observe(adapter, config.raw['live_corpus'])
        for key in first:
            if key != 'observed_at' and first[key] != second[key]:
                raise ValueError('live corpus changed while recording baseline')
        baseline = {**second, 'snapshot_id': 'live-' + config.raw['experiment_id'],
                    'baseline_endpoint_checks_equal': True,
                    'writes_paused_by_this_command': False,
                    'continuous_immutability_proven': False}
        path.parent.mkdir(parents=True, exist_ok=True)
        if (config.output / "journal.sqlite").exists():
            raise ValueError("journal started while observing; baseline cannot be replaced")
        publish_baseline(path, baseline)
        return {'status': 'LIVE_BASELINE_RECORDED', 'manifest': str(path),
                'model_requests_submitted': 0, 'batch_jobs_submitted': 0,
                'immutable': False, 'mongo_count': baseline['mongo_count'], 'pg_count': baseline['pg_count']}
    finally:
        if conn is not None:
            await conn.close()
        mongo.close()
