"""Fixed KB copies. CLI defaults to plan; create never overwrites artifacts.

The verified_immutable label means a fixed, read-only experiment target with no
known writers, not database permission enforcement. This tool never changes grants.
"""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
import re


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def qualified(table):
    return table if '.' in table else 'public.' + table


def validate_targets(mongo, pg, live_mongo, live_pg):
    if not re.fullmatch(r'healthnews_exp_[A-Za-z0-9_]+', mongo):
        raise ValueError('Mongo target must be an experiment collection identifier')
    if not re.fullmatch(r'healthnews_exp_[A-Za-z0-9_]+\.[A-Za-z_][A-Za-z0-9_]*', pg):
        raise ValueError('PG target must be a qualified experiment table identifier')
    if mongo == live_mongo or qualified(pg) == qualified(live_pg):
        raise ValueError('snapshot cannot target live data')


async def scan(adapter, mongo, pg, out=None):
    result = {}
    all_ids = []
    for kind, coordinate in [('mongo', mongo), ('pg', pg)]:
        h, ids = hashlib.sha256(), []
        f = gzip.open(out / ('mongo_snapshot.jsonl.gz' if kind == 'mongo' else 'pg_snapshot_vectors.jsonl.gz'), 'wt', encoding='utf-8') if out else None
        try:
            async for item in adapter.rows(kind, coordinate):
                value = item if kind == 'mongo' else [str(item['id']), item['embedding']]
                line = json.dumps(value, ensure_ascii=False, **({'sort_keys': True, 'default': str} if kind == 'mongo' else {})) + '\n'
                h.update(line.encode())
                ids.append(str(item['_id' if kind == 'mongo' else 'id']))
                if f: f.write(line)
        finally:
            if f: f.close()
        result[kind + '_sha256'] = h.hexdigest()
        result[kind + '_count'] = len(ids)
        result[kind + '_ids_sha256'] = fingerprint(ids)
        all_ids.append(ids)
    if not all_ids[0] or all_ids[0] != all_ids[1]:
        raise ValueError('Mongo and PG IDs differ or source is empty')
    return result


def save(path, manifest):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


async def operate(adapter, *, command, snapshot_id, mongo_target, pg_target, out, source_mongo, source_pg, search_index, vector_column, dimension, polls=30, interval=2, sleep=asyncio.sleep):
    if command not in {'plan', 'create', 'check', 'create-index'}:
        raise ValueError('unknown snapshot command')
    validate_targets(mongo_target, pg_target, source_mongo, source_pg)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?', source_pg) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', vector_column):
        raise ValueError('invalid configured PG source identifier')
    coordinates = dict(snapshot_id=snapshot_id, mongo_collection=mongo_target, pg_table=pg_target,
                       mongo_search_index=search_index, vector_column=vector_column, dimension=dimension,
                       source_mongo_collection=source_mongo, source_pg_table=qualified(source_pg))
    if command == 'plan': return dict(coordinates, status='plan', production_overwrite=False)
    out = Path(out)
    path = out / 'snapshot_manifest.json'
    if command == 'create':
        if path.exists() or await adapter.exists(mongo_target, pg_target):
            raise ValueError('target or manifest exists; refusing overwrite')
        out.mkdir(parents=True, exist_ok=True)
        manifest = dict(coordinates, status='pending', owned_artifacts=[], experiment_read_only=True,
                        known_writers='snapshot preparation only; experiment runner read-only', permission_enforcement=False)
        save(path, manifest)
    else:
        manifest = json.loads(path.read_text())
        if any(manifest.get(k) != v for k, v in coordinates.items()): raise ValueError('manifest coordinates differ')
        if not manifest.get('experiment_read_only') or not manifest.get('known_writers'):
            raise ValueError('check requires recorded read-only experiment access scope')
        if not manifest.get('source_hashes'):
            raise ValueError('check requires recorded source provenance')
        if not manifest.get('source_unchanged_during_copy'):
            # Recovery proves the source and physical copy still match the original
            # recorded export; this is distinct from attesting the earlier copy window.
            live = await scan(adapter, source_mongo, source_pg)
            copied = await scan(adapter, mongo_target, pg_target)
            if live != manifest['source_hashes'] or copied != manifest['source_hashes']:
                raise ValueError('recovery source or copy differs from original provenance')
            manifest['source_unchanged_at_recovery'] = True
    try:
        if command == 'create':
            before = await scan(adapter, source_mongo, source_pg)
            definition = await adapter.mongo_index(source_mongo, search_index)
            if not definition.get('queryable'): raise ValueError('source search index not queryable')
            manifest.update(source_hashes=before, mongo_search_definition=definition['latestDefinition'])
            manifest['mongo_search_definition_sha256'] = fingerprint(definition['latestDefinition'])
            manifest['pg_source_indexes'] = await adapter.pg_indexes(source_pg)
            save(path, manifest)
            manifest['owned_artifacts'].append(pg_target); save(path, manifest)
            await adapter.copy_pg(source_pg, pg_target)
            manifest['owned_artifacts'].append(mongo_target); save(path, manifest)
            await adapter.copy_mongo(source_mongo, mongo_target, search_index, definition['latestDefinition'])
            after = await scan(adapter, source_mongo, source_pg)
            if before != after: raise ValueError('live source changed during copy')
            manifest['source_unchanged_during_copy'] = True
            save(path, manifest)
        export_missing = not manifest.get('data_ready') or not all((out / name).exists() for name in ('mongo_snapshot.jsonl.gz', 'pg_snapshot_vectors.jsonl.gz'))
        actual = await scan(adapter, mongo_target, pg_target, out if command == 'create' or export_missing else None)
        if actual != manifest['source_hashes']: raise ValueError('physical snapshot differs from source provenance')
        manifest.update(actual)
        manifest['pg_indexes'] = await adapter.pg_indexes(pg_target)
        if manifest['pg_indexes'] != manifest['pg_source_indexes']: raise ValueError('PG index definitions differ')
        manifest['pg_indexes_sha256'] = fingerprint(manifest['pg_indexes'])
        manifest['data_ready'] = True
        manifest['status'] = 'pending_search_index_readiness'
        save(path, manifest)
        if command in {'create', 'create-index'}:
            existing = await adapter.mongo_index(mongo_target, search_index)
            if not existing:
                try:
                    await adapter.create_mongo_index(mongo_target, search_index, manifest['mongo_search_definition'])
                except Exception as exc:
                    if getattr(exc, 'code', None) == 20:
                        manifest.update(status='pending_search_index_capacity', failure_type=type(exc).__name__, failure_code=20)
                        save(path, manifest)
                        return manifest
                    raise
        for attempt in range(max(1, polls)):
            index = await adapter.mongo_index(mongo_target, search_index)
            if index.get('latestDefinition') is not None and index['latestDefinition'] != manifest['mongo_search_definition']:
                raise ValueError('Mongo search definition differs')
            if index.get('queryable') and index.get('latestDefinition') == manifest['mongo_search_definition']:
                manifest['status'] = 'verified_immutable'
                break
            manifest['status'] = 'pending_search_index_readiness'
            if attempt + 1 < polls: await sleep(interval)
        save(path, manifest)
        return manifest
    except Exception as exc:
        manifest.update(status='failed', failure_type=type(exc).__name__)
        # Never persist raw connection errors: they can contain credentials.
        save(path, manifest)
        raise


class DatabaseAdapter:
    def __init__(self, mongo, pg, settings):
        self.db, self.pg, self.s = mongo[settings.MONGODB_DB], pg, settings

    async def exists(self, mongo, pg):
        return mongo in await self.db.list_collection_names() or bool(await self.pg.fetchval('SELECT to_regclass($1)', pg))

    async def rows(self, kind, coordinate):
        if kind == 'mongo':
            async for doc in self.db[coordinate].find({}).sort('_id', 1): yield doc
        else:
            async with self.pg.transaction(readonly=True, isolation='repeatable_read'):
                async for row in self.pg.cursor(f'SELECT id, {self.s.PGVECTOR_VECTOR_COLUMN}::text AS embedding FROM {qualified(coordinate)} ORDER BY id'):
                    yield row

    async def mongo_index(self, collection, name):
        indexes = await self.db[collection].list_search_indexes(name).to_list(None)
        return indexes[0] if len(indexes) == 1 else {}

    async def pg_indexes(self, table):
        physical_type = await self.pg.fetchval(
            'SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid=to_regclass($1) AND attname=$2 AND NOT attisdropped',
            qualified(table), self.s.PGVECTOR_VECTOR_COLUMN)
        if physical_type != f'halfvec({self.s.MONGODB_VECTOR_DIM})':
            raise ValueError('physical vector type/dimension differs from configured halfvec dimension')
        rows = await self.pg.fetch('''SELECT i.indisprimary AS primary_key, i.indisvalid AS valid, i.indisready AS ready, am.amname AS method,
            pg_get_indexdef(i.indexrelid, 0, true) AS definition
            FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            JOIN pg_am am ON am.oid=c.relam WHERE i.indrelid=to_regclass($1)''', qualified(table))
        result = []
        for row in rows:
            if not row['valid'] or not row['ready']:
                raise ValueError('PG index is not physically valid and ready')
            # Normalize only the owned index/table identifiers, preserving opclasses/options.
            definition = row['definition'].split(' USING ', 1)[-1]
            result.append(dict(primary_key=row['primary_key'], method=row['method'], definition=definition))
        if not any(r['primary_key'] for r in result) or not any(r['method'] == 'hnsw' and 'halfvec' in r['definition'] for r in result):
            raise ValueError('source/target needs primary key and halfvec HNSW indexes')
        return sorted(result, key=lambda r: r['definition'])

    async def copy_pg(self, source, target):
        schema, name = target.split('.')
        async with self.pg.transaction(isolation='repeatable_read'):
            schema_exists = await self.pg.fetchval(
                'SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=$1)', schema)
            if not schema_exists:
                await self.pg.execute(f'CREATE SCHEMA {schema}')
            await self.pg.execute(f'CREATE TABLE {target} (LIKE {qualified(source)} INCLUDING ALL)')
            await self.pg.execute(f'INSERT INTO {target} SELECT * FROM {qualified(source)}')

    async def copy_mongo(self, source, target, name, definition):
        collection = await self.db.create_collection(target)
        batch = []
        async for doc in self.db[source].find({}).sort('_id', 1):
            batch.append(doc)
            if len(batch) == 500:
                await collection.insert_many(batch, ordered=True); batch = []
        if batch: await collection.insert_many(batch, ordered=True)

    async def create_mongo_index(self, target, name, definition):
        from pymongo.operations import SearchIndexModel
        await self.db[target].create_search_index(SearchIndexModel(name=name, definition=definition))


async def main(args):
    from app.core.config import settings as s
    kwargs = dict(command=args.command, snapshot_id=args.id, mongo_target=args.mongo_target,
                  pg_target=args.pg_target, out=args.out, source_mongo=s.MONGODB_COLLECTION,
                  source_pg=s.PGVECTOR_TABLE, search_index=s.MONGODB_TEXT_INDEX,
                  vector_column=s.PGVECTOR_VECTOR_COLUMN, dimension=s.MONGODB_VECTOR_DIM,
                  polls=args.polls, interval=args.interval)
    if args.command == 'plan': return await operate(None, **kwargs)
    import asyncpg
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(s.MONGODB_URI, serverSelectionTimeoutMS=20000)
    pg = None
    try:
        pg = await asyncpg.connect(os.getenv('PGVECTOR_SNAPSHOT_DSN') or os.getenv('PGVECTOR_SYNC_DSN') or s.PGVECTOR_DSN)
        return await operate(DatabaseAdapter(client, pg, s), **kwargs)
    finally:
        if pg: await pg.close()
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'create', 'check', 'create-index'], nargs='?', default='plan')
    parser.add_argument('--id', default='healthnews_new_20261011')
    parser.add_argument('--mongo-target', default='healthnews_exp_20261011_new')
    parser.add_argument('--pg-target', default='healthnews_exp_20261011.health_articles_chunks')
    parser.add_argument('--out', type=Path, default=Path('/tmp/healthnews-new-20261011'))
    parser.add_argument('--polls', type=int, default=30)
    parser.add_argument('--interval', type=float, default=2)
    try:
        print(json.dumps(asyncio.run(main(parser.parse_args())), ensure_ascii=False, indent=2))
    except Exception as exc:
        raise SystemExit(f'Snapshot operation failed ({type(exc).__name__}); inspect manifest and owned targets.')
