"""固定題庫、原始碼與快照 identity；此模組不建立外部 client。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
ARM_DEFINITIONS = {"A": {"corpus": "old", "decomposition": False},
                   "B": {"corpus": "new", "decomposition": False},
                   "C": {"corpus": "old", "decomposition": True},
                   "D": {"corpus": "new", "decomposition": True}}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_sources(root: Path = ROOT) -> dict[str, str]:
    paths = [*root.glob("app/**/*.py"), *root.glob("scripts/healthnews_batch/*.py"),
             *root.glob("resources/certs/*.pem")]
    paths += [root / p for p in ("scripts/healthnews_experiment.py", "scripts/healthnews_batch_experiment.py",
                                "scripts/healthnews_snapshot.py", "app/core/config.py", "app/i18n/messages.py",
                                "app/services/gemini/shared/parser.py", "app/core/ca_bundle.py", "pyproject.toml", "uv.lock")]
    return {str(p.relative_to(root)): file_hash(p) for p in sorted(set(paths)) if p.is_file()}


def _no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(r"(?:API_KEY|PASSWORD|SECRET|(?:^|_)DSN$|(?:^|_)URI$|(?:^|_)TOKEN$)", key.upper()):
                raise ValueError("secret credential fields cannot be embedded in experiment config")
            _no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _no_secrets(item)


def _relative(base: Path, value: str) -> Path:
    p = Path(value)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("experiment artifact paths must be relative and inside config directory")
    resolved = (base / p).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError("experiment artifact symlink escapes config directory")
    return resolved


@dataclass(frozen=True)
class ExperimentConfig:
    raw: dict
    path: Path
    cases: list[dict]
    snapshots: dict[str, dict]
    blockers: list[str]
    version: str

    @property
    def planned_runs(self):
        return len(self.cases) * len(self.raw["selected_arms"]) * self.raw["rounds"]

    @property
    def output(self):
        return _relative(self.path.parent, self.raw["output"])

    def run_key(self, case_id, arm, round_number):
        return f"{self.version}:{case_id}:{arm}:r{round_number}"

    def identity(self):
        return {"configuration": self.raw, "snapshot_manifests": self.snapshots,
                "dataset_sha256": self.raw["dataset"]["sha256"], "config_version": self.version}


def load_config(path: Path, *, code_root: Path = ROOT, require_snapshots=False) -> ExperimentConfig:
    path = Path(path).resolve()
    raw = json.loads(path.read_text())
    _no_secrets(raw)
    if raw.get("schema_version") != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", raw.get("experiment_id", "")):
        raise ValueError("invalid experiment schema/version/id")
    if raw.get("rounds") not in (1, 2, 3) or type(raw["rounds"]) is not int:
        raise ValueError("rounds must be 1, 2 or 3")
    if raw.get("arms") != ARM_DEFINITIONS:
        raise ValueError("arm definitions must preserve old/new and decomposition on/off")
    selected = raw.get("selected_arms", [])
    if not selected or len(set(selected)) != len(selected) or any(a not in ARM_DEFINITIONS for a in selected):
        raise ValueError("invalid selected_arms")
    if raw.get("isolation") != {"web_fallback": False, "answer_cache": False, "writeback": False}:
        raise ValueError("formal isolation requires no web/cache/writeback")
    for section, fields in (("batch", ("max_requests", "concurrency")),
                            ("budget", ("candidate_total_per_run", "context_tokens"))):
        for name in fields:
            if type(raw.get(section, {}).get(name)) is not int or raw[section][name] <= 0:
                raise ValueError(f"positive {section}.{name} required")
    if not isinstance(raw["batch"].get("poll_interval_seconds"), (int, float)) or raw["batch"]["poll_interval_seconds"] < 0:
        raise ValueError("invalid poll interval")
    for name in ("decomposition", "rewrite", "generation", "crag_fallback", "embedding"):
        if not isinstance(raw.get("models", {}).get(name), str) or not raw["models"][name]:
            raise ValueError(f"model {name} must be pinned")
    settings = raw.get("settings", {})
    if settings.get("RAG_RETRIEVE_CANDIDATES", 0) <= 0 or settings.get("RAG_RERANK_TOP_N", 0) <= 0:
        raise ValueError("retrieval/generation candidate limits required")
    source_hashes = raw.get("code", {}).get("source_sha256", {})
    current_sources = freeze_sources(Path(code_root))
    if source_hashes != current_sources or not source_hashes:
        raise ValueError("source SHA256 freeze differs from current code; create a new config version")
    dataset_path = _relative(path.parent, raw["dataset"]["path"])
    if file_hash(dataset_path) != raw["dataset"]["sha256"]:
        raise ValueError("dataset SHA256 differs from frozen input")
    review = raw.get("review", {})
    if review.get("status") != "user_attested_complete" or review.get("dataset_sha256") != raw["dataset"]["sha256"]:
        raise ValueError("review must attest this exact dataset SHA256")
    dataset = json.loads(dataset_path.read_text())
    cases = dataset.get("cases", [])
    if len(cases) != raw["dataset"]["expected_count"] or not cases:
        raise ValueError("dataset case count differs from frozen input")
    ids = [c.get("id") for c in cases]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not re.fullmatch(r"HN\d{3}", i) for i in ids):
        raise ValueError("case IDs must be unique HN identifiers")
    if any(not isinstance(c.get("question"), str) or not c["question"].strip()
           or c.get("language", "zh-TW") != "zh-TW" for c in cases):
        raise ValueError("cases require original questions and fixed zh-TW")
    snapshots, blockers = {}, []
    for corpus in sorted({ARM_DEFINITIONS[a]["corpus"] for a in selected}):
        manifest_ref = raw.get("snapshots", {}).get(corpus)
        manifest_path = _relative(path.parent, manifest_ref) if manifest_ref else None
        if manifest_path is None or not manifest_path.is_file():
            blockers.append(f"{corpus} snapshot manifest missing")
            continue
        snapshot = json.loads(manifest_path.read_text())
        required = ("snapshot_id", "mongo_collection", "pg_table", "mongo_search_index",
                    "mongo_sha256", "pg_sha256", "mongo_count", "pg_count")
        if snapshot.get("status") != "verified_immutable" or any(not snapshot.get(k) for k in required):
            blockers.append(f"{corpus} snapshot is not verified_immutable")
        if any(not re.fullmatch(r"[0-9a-f]{64}", snapshot.get(k, "")) for k in ("mongo_sha256", "pg_sha256")):
            blockers.append(f"{corpus} snapshot content SHA256 missing")
        live_collection = snapshot.get("source_mongo_collection", settings.get("MONGODB_COLLECTION", "health_articles_chunks"))
        live_table = snapshot.get("source_pg_table", settings.get("PGVECTOR_TABLE", "health_articles_chunks"))
        if snapshot.get("mongo_collection") == live_collection or snapshot.get("pg_table") in (live_table, "public." + live_table):
            blockers.append(f"{corpus} snapshot points to live corpus")
        snapshots[corpus] = snapshot
    if require_snapshots and blockers:
        raise ValueError("; ".join(blockers))
    _relative(path.parent, raw["output"])
    version = digest({"configuration": raw, "snapshot_manifests": snapshots})
    return ExperimentConfig(raw, path, [{"id": c["id"], "question": c["question"],
                                        "language": c.get("language", "zh-TW")} for c in cases], snapshots, blockers, version)
