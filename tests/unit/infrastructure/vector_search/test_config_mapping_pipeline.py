from app.infrastructure.vector_search.config import VectorSearchConfig
from app.infrastructure.vector_search.mapping import mongo_document_to_chunk_hit
from app.infrastructure.vector_search.pipeline import build_vector_search_pipeline


def test_mongo_document_to_chunk_hit_handles_missing_fields():
    hit = mongo_document_to_chunk_hit({}, text_field="chunk_text")
    assert hit == {"id": "None", "text": None, "score": None}


def test_build_vector_search_pipeline_contains_expected_projection():
    cfg = VectorSearchConfig(
        mongo_uri="mongodb://localhost",
        db_name="db",
        collection_name="coll",
        vector_index="idx",
        vector_field="embedding",
        text_field="chunk_text",
    )

    pipeline = build_vector_search_pipeline(
        cfg, query_embedding=[0.1, 0.2], k=5, num_candidates=100
    )

    assert pipeline[0]["$vectorSearch"]["index"] == "idx"
    assert pipeline[0]["$vectorSearch"]["queryVector"] == [0.1, 0.2]
    assert pipeline[0]["$vectorSearch"]["limit"] == 5
    assert pipeline[0]["$vectorSearch"]["numCandidates"] == 100
    assert pipeline[1]["$project"]["chunk_text"] == 1
    assert pipeline[1]["$project"]["score"] == {"$meta": "vectorSearchScore"}


def test_vector_search_config_resolve_num_candidates():
    cfg = VectorSearchConfig(
        mongo_uri="mongodb://localhost",
        db_name="db",
        collection_name="coll",
        vector_index="idx",
        vector_field="embedding",
        text_field="text",
    )
    assert cfg.resolve_num_candidates(3) == 90


def test_vector_search_config_from_settings_handles_zero_dim(monkeypatch):
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "MONGODB_URI", "mongodb://local")
    monkeypatch.setattr(config_module.settings, "MONGODB_DB", "db")
    monkeypatch.setattr(config_module.settings, "MONGODB_COLLECTION", "coll")
    monkeypatch.setattr(config_module.settings, "MONGODB_VECTOR_INDEX", "idx")
    monkeypatch.setattr(config_module.settings, "MONGODB_VECTOR_FIELD", "embedding")
    monkeypatch.setattr(config_module.settings, "MONGODB_TEXT_FIELD", "text")
    monkeypatch.setattr(config_module.settings, "MONGODB_VECTOR_DIM", 0)

    cfg = VectorSearchConfig.from_settings()
    assert cfg.vector_dim is None


def test_vector_search_config_from_settings_keeps_positive_dim(monkeypatch):
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "MONGODB_URI", "mongodb://local")
    monkeypatch.setattr(config_module.settings, "MONGODB_DB", "db")
    monkeypatch.setattr(config_module.settings, "MONGODB_COLLECTION", "coll")
    monkeypatch.setattr(config_module.settings, "MONGODB_VECTOR_INDEX", "idx")
    monkeypatch.setattr(config_module.settings, "MONGODB_VECTOR_FIELD", "embedding")
    monkeypatch.setattr(config_module.settings, "MONGODB_TEXT_FIELD", "text")
    monkeypatch.setattr(config_module.settings, "MONGODB_VECTOR_DIM", 123)

    cfg = VectorSearchConfig.from_settings()
    assert cfg.vector_dim == 123
