import json
import pytest
from scripts.healthnews_snapshot import fingerprint

class Adapter:
    async def rows(self, kind, coordinate):
        if kind=='mongo':yield {'_id':'1','chunk_content':'evidence'}
        else:yield {'id':'1','embedding':'[1,0]'}
    async def mongo_index(self, collection, name):
        return {'queryable':True,'latestDefinition':{'mappings':{'dynamic':False}}}
    async def pg_indexes(self, table):return [{'primary_key':True,'method':'btree','definition':'btree (id)'},{'primary_key':False,'method':'hnsw','definition':'hnsw (embedding_half halfvec_cosine_ops)'}]

async def test_live_observation_has_hashes_no_copy_no_immutable_claim():
    from scripts.healthnews_batch.live import observe
    m=await observe(Adapter(),{'mongo_collection':'health_articles_chunks','pg_table':'public.health_articles_chunks','mongo_search_index':'care_text_index'})
    assert m['status']=='live_observed' and m['immutable'] is False
    assert m['mongo_count']==m['pg_count']==1
    assert m['mongo_ids_sha256']==m['pg_ids_sha256']
    assert m['mongo_search_definition_sha256']==fingerprint({'mappings':{'dynamic':False}})

async def test_live_observation_rejects_nonqueryable_index():
    from scripts.healthnews_batch.live import observe
    class Pending(Adapter):
        async def mongo_index(self,*args):return {'queryable':False}
    with pytest.raises(ValueError,match='queryable'):
        await observe(Pending(),{'mongo_collection':'health_articles_chunks','pg_table':'public.health_articles_chunks','mongo_search_index':'care_text_index'})


def test_publish_baseline_never_overwrites_existing_observation(tmp_path):
    from scripts.healthnews_batch.live import publish_baseline
    p=tmp_path/'baseline.json'
    publish_baseline(p,{'observed':'first'})
    with pytest.raises(FileExistsError):publish_baseline(p,{'observed':'second'})
    assert json.loads(p.read_text())=={'observed':'first'}
