"""正式輸入隔離與版本漂移拒絕，使用實際 JSON/檔案。"""
import hashlib
import json
from pathlib import Path

import pytest


def fixture(tmp_path):
    from scripts.healthnews_batch.config import freeze_sources
    root = Path(__file__).resolve().parents[3]
    dataset = {"cases": [{"id": "HN001", "question": "原始問題？", "language": "zh-TW",
                          "reference_answer": "不能輸入模型", "reference_subquestions": ["不能輸入模型"]}]}
    raw = json.dumps(dataset, ensure_ascii=False).encode()
    (tmp_path / "dataset.json").write_bytes(raw)
    hashed = hashlib.sha256(raw).hexdigest()
    config = {"schema_version": 1, "experiment_id": "test-care", "dataset": {
        "path": "dataset.json", "sha256": hashed, "expected_count": 1},
        "review": {"status": "user_attested_complete", "dataset_sha256": hashed},
        "code": {"base_commit": "864b6a00", "source_sha256": freeze_sources(root)},
        "rounds": 3, "selected_arms": ["B", "D"],
        "arms": {"A": {"corpus": "old", "decomposition": False},
                 "B": {"corpus": "new", "decomposition": False},
                 "C": {"corpus": "old", "decomposition": True},
                 "D": {"corpus": "new", "decomposition": True}},
        "snapshots": {"old": None, "new": "snapshots/new/manifest.json"},
        "models": {"decomposition": "gemini-3.8-flash", "rewrite": "gemini-3.8-flash",
                   "generation": "gemini-3.5-flash-lite", "crag_fallback": "gemini-3.8-flash",
                   "embedding": "gemini-embedding-001"},
        "isolation": {"web_fallback": False, "answer_cache": False, "writeback": False},
        "batch": {"max_requests": 100, "poll_interval_seconds": 30, "concurrency": 24},
        "budget": {"candidate_total_per_run": 160, "context_tokens": 16384},
        "settings": {"RAG_RETRIEVE_CANDIDATES": 40, "RAG_RERANK_TOP_N": 5}, "output": "output"}
    (tmp_path / "experiment.json").write_text(json.dumps(config))
    return tmp_path / "experiment.json", config, root


def test_model_input_excludes_all_reference_fields_and_rounds_have_distinct_identity(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, _, root = fixture(tmp_path)
    config = load_config(path, code_root=root)
    assert config.cases == [{"id": "HN001", "question": "原始問題？", "language": "zh-TW"}]
    assert config.planned_runs == 6
    assert config.run_key("HN001", "B", 1) != config.run_key("HN001", "D", 1)
    assert config.run_key("HN001", "D", 1) != config.run_key("HN001", "D", 2)
    assert config.blockers == ["new snapshot manifest missing"]


def test_dataset_byte_change_is_rejected_before_any_provider_call(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, _, root = fixture(tmp_path)
    (tmp_path / "dataset.json").write_text('{"cases": []}')
    with pytest.raises(ValueError, match="dataset SHA256"):
        load_config(path, code_root=root)


def test_source_hash_change_is_rejected(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, data, root = fixture(tmp_path)
    data["code"]["source_sha256"]["app/services/rag/question_decomposer.py"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="source SHA256"):
        load_config(path, code_root=root)


def test_review_attestation_must_bind_exact_dataset(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, data, root = fixture(tmp_path)
    data["review"]["dataset_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="review"):
        load_config(path, code_root=root)


@pytest.mark.parametrize("field", ["web_fallback", "answer_cache", "writeback"])
def test_formal_isolation_cannot_be_enabled(tmp_path, field):
    from scripts.healthnews_batch.config import load_config
    path, data, root = fixture(tmp_path)
    data["isolation"][field] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="isolation"):
        load_config(path, code_root=root)


def test_no_paid_execution_with_missing_snapshot(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, _, root = fixture(tmp_path)
    with pytest.raises(ValueError, match="snapshot manifest missing"):
        load_config(path, code_root=root, require_snapshots=True)


def test_config_change_produces_new_version_not_same_resume_identity(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, data, root = fixture(tmp_path)
    before = load_config(path, code_root=root)
    data["rounds"] = 1
    path.write_text(json.dumps(data))
    after = load_config(path, code_root=root)
    assert before.version != after.version


def test_credentials_cannot_be_embedded_in_persisted_config(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, data, root = fixture(tmp_path)
    data["settings"]["GEMINI_API_KEY"] = "secret-value"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="secret"):
        load_config(path, code_root=root)


def test_context_format_dependency_change_invalidates_freeze(tmp_path):
    from scripts.healthnews_batch.config import load_config, freeze_sources
    path, data, _ = fixture(tmp_path)
    root = tmp_path / "code"
    (root / "app/core").mkdir(parents=True)
    (root / "app/services/rag").mkdir(parents=True)
    (root / "app/services/rag/question_decomposer.py").write_text("gate = 1")
    date = root / "app/core/publish_date.py"
    date.write_text("date_format = 'original'")
    data["code"]["source_sha256"] = freeze_sources(root)
    path.write_text(json.dumps(data))
    load_config(path, code_root=root)
    date.write_text("date_format = 'changed'")
    with pytest.raises(ValueError, match="source SHA256"):
        load_config(path, code_root=root)


def live_fixture(tmp_path):
    path, data, root = fixture(tmp_path)
    data['corpus_mode'] = 'live_observed'
    data['live_corpus'] = {'mongo_collection': 'health_articles_chunks',
        'pg_table': 'public.health_articles_chunks', 'mongo_search_index': 'care_text_index'}
    manifest = {'status': 'live_observed', 'snapshot_id': 'live-test',
        **data['live_corpus'], 'mongo_sha256': 'a'*64, 'pg_sha256': 'b'*64,
        'mongo_count': 1, 'pg_count': 1, 'mongo_ids_sha256': 'c'*64, 'pg_ids_sha256': 'c'*64,
        'mongo_search_definition_sha256': 'd'*64, 'pg_indexes_sha256': 'e'*64,
        'immutable': False, 'observed_at': '2026-10-13T00:00:00Z'}
    target = tmp_path/'snapshots/new/manifest.json';target.parent.mkdir(parents=True);target.write_text(json.dumps(manifest))
    path.write_text(json.dumps(data))
    return path, data, root


def test_explicit_live_mode_accepts_existing_corpus_without_claiming_immutable(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, _, root = live_fixture(tmp_path)
    config = load_config(path, code_root=root, require_snapshots=True)
    assert config.corpus_mode == 'live_observed'
    assert config.snapshots['new']['immutable'] is False
    assert config.blockers == []


def test_live_mode_rejects_old_arm_and_silent_target_change(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, data, root = live_fixture(tmp_path)
    data['selected_arms'] = ['A','D'];path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='B/D'):load_config(path, code_root=root)
    data['selected_arms']=['B','D'];data['live_corpus']['pg_table']='public.other';path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='target'):load_config(path, code_root=root, require_snapshots=True)


def test_live_mode_cannot_relabel_immutable_snapshot_or_omit_hashes(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path, _, root = live_fixture(tmp_path)
    p=tmp_path/'snapshots/new/manifest.json';m=json.loads(p.read_text());m['immutable']=True;p.write_text(json.dumps(m))
    with pytest.raises(ValueError, match='live'):load_config(path, code_root=root, require_snapshots=True)


def test_live_vector_sql_identifier_rejected_before_client_creation(tmp_path):
    from scripts.healthnews_batch.config import load_config
    path,data,root=live_fixture(tmp_path)
    data['settings']['PGVECTOR_VECTOR_COLUMN']='embedding_half; SELECT dangerous()';path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='vector column'):load_config(path,code_root=root)
