import pytest
from scripts.healthnews_snapshot import validate_targets, fingerprint


def test_rejects_live_and_sql_identifiers():
    for mongo, pg in [('live', 'exp.copy'), ('copy', 'public.live'), ('copy', 'exp.copy;drop')]:
        with pytest.raises(ValueError):
            validate_targets(mongo, pg, 'live', 'live')


def test_canonical_hash_preserves_document_serialization():
    assert fingerprint({'b': 2, 'a': '健康'}) == fingerprint({'a': '健康', 'b': 2})

from scripts.healthnews_snapshot import operate


class Fake:
    def __init__(self, exists=False, mismatch=False, changed=False, ready=True, bad_definition=False):
        self.present, self.mismatch, self.changed = exists, mismatch, changed
        self.ready, self.bad_definition, self.copied = ready, bad_definition, False
    async def exists(self, mongo, pg): return self.present
    async def rows(self, kind, coordinate):
        target = coordinate.startswith('healthnews_exp_')
        if kind == 'mongo':
            yield {'_id': 'a', 'text': 'changed' if self.changed and self.copied and not target else '健康'}
        else: yield {'id': 'b' if self.mismatch else 'a', 'embedding': '[1,2]'}
    async def mongo_index(self, collection, name):
        target = collection.startswith('healthnews_exp_')
        return {'queryable': self.ready if target else True, 'latestDefinition': {'mappings': {'dynamic': not (target and self.bad_definition)}}}
    async def pg_indexes(self, table): return [{'primary_key': True}, {'method': 'hnsw'}]
    async def copy_pg(self, source, target): self.copied = True
    async def copy_mongo(self, *args): pass
    async def create_mongo_index(self, *args): pass


async def run(fake, tmp_path, command='create'):
    return await operate(fake, command=command, snapshot_id='fixed', mongo_target='healthnews_exp_test',
                         pg_target='healthnews_exp_test.chunks', out=tmp_path, source_mongo='live',
                         source_pg='public.live', search_index='text', vector_column='embedding_half', dimension=2, polls=1)


@pytest.mark.asyncio
async def test_success_and_check(tmp_path):
    manifest = await run(Fake(), tmp_path)
    assert manifest['status'] == 'verified_immutable'
    assert manifest['mongo_count'] == manifest['pg_count'] == 1
    assert len(manifest['pg_sha256']) == 64
    assert (tmp_path / 'pg_snapshot_vectors.jsonl.gz').exists()
    assert (await run(Fake(), tmp_path, 'check'))['status'] == 'verified_immutable'


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [{'exists': True}, {'mismatch': True}, {'changed': True}, {'bad_definition': True}])
async def test_refuses_invalid_physical_state(tmp_path, options):
    with pytest.raises(ValueError): await run(Fake(**options), tmp_path)


@pytest.mark.asyncio
async def test_pending_then_check(tmp_path):
    assert (await run(Fake(ready=False), tmp_path))['status'] == 'pending_search_index_readiness'
    assert (await run(Fake(), tmp_path, 'check'))['status'] == 'verified_immutable'


@pytest.mark.asyncio
async def test_refuses_overwrite_manifest(tmp_path):
    await run(Fake(), tmp_path)
    with pytest.raises(ValueError): await run(Fake(), tmp_path)


@pytest.mark.asyncio
async def test_check_refuses_missing_provenance(tmp_path):
    import json
    await run(Fake(), tmp_path)
    path = tmp_path / 'snapshot_manifest.json'
    manifest = json.loads(path.read_text())
    manifest.pop('source_unchanged_during_copy')
    path.write_text(json.dumps(manifest))
    recovered = await run(Fake(), tmp_path, 'check')
    assert recovered['source_unchanged_at_recovery'] is True
    assert 'source_unchanged_during_copy' not in recovered


@pytest.mark.asyncio
async def test_absent_building_definition_stays_pending(tmp_path):
    class Building(Fake):
        async def mongo_index(self, collection, name):
            if collection.startswith('healthnews_exp_'): return {}
            return await super().mongo_index(collection, name)
    manifest = await run(Building(), tmp_path)
    assert manifest['status'] == 'pending_search_index_readiness'


@pytest.mark.asyncio
async def test_plan_normalizes_source_and_reports_dimension(tmp_path):
    manifest = await operate(None, command='plan', snapshot_id='fixed', mongo_target='healthnews_exp_test',
                             pg_target='healthnews_exp_test.chunks', out=tmp_path, source_mongo='live',
                             source_pg='health_articles_chunks', search_index='text',
                             vector_column='embedding_half3072', dimension=3072)
    assert manifest['source_pg_table'] == 'public.health_articles_chunks'
    assert manifest['dimension'] == 3072


@pytest.mark.asyncio
@pytest.mark.parametrize('schema_exists', [True, False])
async def test_copy_pg_skips_existing_schema_without_database_create(schema_exists):
    from scripts.healthnews_snapshot import DatabaseAdapter
    class Transaction:
        async def __aenter__(self): pass
        async def __aexit__(self, *args): pass
    class Connection:
        def __init__(self): self.commands = []
        def transaction(self, **kwargs): return Transaction()
        async def fetchval(self, query, value):
            assert 'pg_namespace' in query
            assert value == 'healthnews_exp_test'
            return schema_exists
        async def execute(self, command): self.commands.append(command)
    class Settings: MONGODB_DB = 'db'
    connection = Connection()
    adapter = DatabaseAdapter({'db': object()}, connection, Settings())
    await adapter.copy_pg('health_articles_chunks', 'healthnews_exp_test.chunks')
    schema_commands = [command for command in connection.commands if command.startswith('CREATE SCHEMA')]
    assert schema_commands == ([] if schema_exists else ['CREATE SCHEMA healthnews_exp_test'])
    assert 'LIKE public.health_articles_chunks INCLUDING ALL' in connection.commands[-2]
    assert connection.commands[-1] == 'INSERT INTO healthnews_exp_test.chunks SELECT * FROM public.health_articles_chunks'


class Quota(Fake):
    def __init__(self, quota=True):
        super().__init__()
        self.quota, self.index_created, self.index_targets = quota, False, []
    async def mongo_index(self, collection, name):
        if collection.startswith('healthnews_exp_') and not self.index_created: return {}
        return await super().mongo_index(collection, name)
    async def create_mongo_index(self, target, name, definition):
        self.index_targets.append(target)
        if self.quota:
            class CapacityError(Exception): code = 20
            raise CapacityError('sensitive provider text must not be persisted')
        self.index_created = True


@pytest.mark.asyncio
async def test_quota_preserves_data_and_exports(tmp_path):
    import json
    manifest = await run(Quota(), tmp_path)
    assert manifest['status'] == 'pending_search_index_capacity'
    assert manifest['data_ready'] and manifest['source_unchanged_during_copy']
    assert manifest['failure_code'] == 20
    assert manifest['mongo_count'] == manifest['pg_count'] == 1
    assert (tmp_path / 'mongo_snapshot.jsonl.gz').exists()
    assert 'sensitive provider' not in (tmp_path / 'snapshot_manifest.json').read_text()


@pytest.mark.asyncio
async def test_create_index_only_designated_target(tmp_path):
    await run(Quota(), tmp_path)
    adapter = Quota(quota=False)
    manifest = await run(adapter, tmp_path, 'create-index')
    assert manifest['status'] == 'verified_immutable'
    assert adapter.index_targets == ['healthnews_exp_test']
    assert not adapter.copied


@pytest.mark.asyncio
async def test_recovery_refuses_source_drift(tmp_path):
    import json
    await run(Quota(), tmp_path)
    path = tmp_path / 'snapshot_manifest.json'
    manifest = json.loads(path.read_text())
    manifest.pop('source_unchanged_during_copy')
    path.write_text(json.dumps(manifest))
    drift = Fake(changed=True)
    drift.copied = True
    with pytest.raises(ValueError, match='recovery'):
        await run(drift, tmp_path, 'check')
    assert 'source_unchanged_at_recovery' not in json.loads(path.read_text())
