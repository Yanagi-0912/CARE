"""CARE v3 離線 Batch；預設只預檢，run --submit 才會提交付費請求。"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.healthnews_batch.config import load_config
from scripts.healthnews_batch.store import Journal
from scripts.healthnews_batch.transport import BatchCoordinator, GeminiBatchProvider


@contextmanager
def exclusive(output):
    import fcntl
    output.mkdir(parents=True, exist_ok=True)
    with (output / "execution.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another process owns this experiment journal") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


async def verify_physical(config, vectors):
    from scripts.healthnews_experiment import verify_snapshot
    from scripts.healthnews_snapshot import DatabaseAdapter, fingerprint
    from types import SimpleNamespace
    options = SimpleNamespace(**config.raw["settings"])
    verified = {}
    for corpus, vector in vectors.items():
        snapshot = config.snapshots[corpus]
        physical = await verify_snapshot(vector, snapshot)
        indexes = await vector._ensure_collection().list_search_indexes(snapshot["mongo_search_index"]).to_list(None)
        if fingerprint(indexes[0].get("latestDefinition")) != snapshot.get("mongo_search_definition_sha256"):
            raise ValueError("snapshot Atlas definition changed")
        pool = await vector._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                adapter = DatabaseAdapter(vector._ensure_collection().database.client, conn, options)
                if fingerprint(await adapter.pg_indexes(vector.table_name)) != snapshot.get("pg_indexes_sha256"):
                    raise ValueError("snapshot PostgreSQL index definitions changed")
        verified[corpus] = physical
    return verified


def status(config):
    path = config.output / "journal.sqlite"
    if not path.is_file():
        return {"status": "NOT_STARTED", "planned_runs": config.planned_runs, "jobs": 0, "completed_runs": 0}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        identity = db.execute("SELECT version FROM config").fetchone()
        if identity and identity[0] != config.version:
            raise ValueError("journal config version differs")
        return {"status": "JOURNAL_PRESENT", "planned_runs": config.planned_runs,
                "completed_runs": db.execute("SELECT count(*) FROM results").fetchone()[0],
                "jobs_by_state": dict(db.execute("SELECT state,count(*) FROM jobs GROUP BY state")),
                "requests_by_state": dict(db.execute("SELECT state,count(*) FROM requests GROUP BY state"))}


async def preflight(config, physical=False, provider_factory=GeminiBatchProvider):
    report = {"status": "BLOCKED" if config.blockers else "STATIC_READY", "config_version": config.version,
              "experiment_id": config.raw["experiment_id"], "planned_runs": config.planned_runs,
              "arms": config.raw["selected_arms"], "rounds": config.raw["rounds"],
              "dataset_sha256": config.raw["dataset"]["sha256"], "review": config.raw["review"],
              "blockers": list(config.blockers), "snapshot_physical_checked": False,
              "corpus_mode": config.corpus_mode, "immutable_corpus": config.corpus_mode == "immutable_snapshot",
              "corpus_physical_checked": False, "continuous_immutability_proven": False,
              "model_requests_submitted": 0, "batch_jobs_submitted": 0}
    if not physical or config.blockers:
        return report
    from app.core.config import settings
    from scripts.healthnews_batch.runtime import build_factory
    with tempfile.TemporaryDirectory(prefix="care-batch-preflight-") as directory:
        journal = Journal(Path(directory) / "journal.sqlite")
        provider = provider_factory(settings.GEMINI_API_KEY)
        coordinator = BatchCoordinator(journal, provider, config.version)
        close = None
        try:
            _, vectors, close = build_factory(config, journal, coordinator, settings)
            report["snapshot_verification"] = await verify_physical(config, vectors)
            report.update(status="READY_LIVE_OBSERVED" if config.corpus_mode == "live_observed" else "READY",
                          corpus_physical_checked=True, snapshot_physical_checked=config.corpus_mode == "immutable_snapshot",
                          credential_presence_only={k: bool(getattr(settings, k, "")) for k in
                            ("GEMINI_API_KEY", "COHERE_API_KEY", "TYPESAFE_API_KEY", "MONGODB_URI", "PGVECTOR_DSN")})
        finally:
            if close:
                await close()
            await provider.close()
            journal.close()
    return report


async def main(args, *, provider_factory=GeminiBatchProvider):
    if args.command == "run" and not args.submit:
        raise ValueError("run requires explicit --submit; use preflight to prepare without paid requests")
    config = load_config(args.config, require_snapshots=args.command in ("run", "reconcile"))
    if args.command == "status":
        return status(config)
    if args.command == "preflight":
        report = await preflight(config, args.verify_snapshot, provider_factory)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return report
    from app.core.config import settings
    if args.command == "observe-live":
        from scripts.healthnews_batch.live import capture
        with exclusive(config.output):
            return await capture(config, settings)
    from scripts.healthnews_batch.runtime import build_factory, run_experiment
    with exclusive(config.output):
        journal = Journal(config.output / "journal.sqlite")
        journal.bind_config(config.version, config.identity())
        if args.command == "reconcile-receipt":
            try:
                if not args.run_key or not args.stage or not args.receipt_file:
                    raise ValueError("reconcile-receipt requires run-key, stage and receipt-file")
                row = journal.db.execute("SELECT * FROM receipts WHERE run_key=? AND stage=?", (args.run_key, args.stage)).fetchone()
                data = json.loads(args.receipt_file.read_text())
                if row is None or row["status"] != "unknown":
                    raise ValueError("receipt is not awaiting reconciliation")
                if data.get("input_hash") != row["input_hash"]:
                    raise ValueError("manual receipt input hash differs")
                if data.get("status") not in ("done", "error") or "payload" not in data:
                    raise ValueError("manual receipt requires done/error status and recorded payload")
                journal.save_receipt(args.run_key, args.stage, row["input_hash"], data["status"], data["payload"])
                return {"status": "RECEIPT_RECONCILED", "run_key": args.run_key, "stage": args.stage,
                        "operator_supplied_record": True}
            finally:
                journal.close()
        provider = provider_factory(settings.GEMINI_API_KEY)
        coordinator = BatchCoordinator(journal, provider, config.version,
            poll_interval=config.raw["batch"]["poll_interval_seconds"], max_requests=config.raw["batch"]["max_requests"])
        close = None
        try:
            if args.command == "reconcile":
                if not args.job_id or not args.provider_job_name:
                    raise ValueError("reconcile requires --job-id and --provider-job-name")
                coordinator.attach_job(args.job_id, args.provider_job_name)
                return {"status": "ATTACHED", "local_job_id": args.job_id, "provider_job_name": args.provider_job_name}
            factory, vectors, close = build_factory(config, journal, coordinator, settings)
            verification_path = config.output / "corpus_verification.json"
            verification = {"corpus_mode": config.corpus_mode, "status": "CHECKING_BEFORE",
                            "continuous_immutability_proven": False}
            def record_verification():
                from scripts.healthnews_snapshot import save
                save(verification_path, verification)
            record_verification()
            try:
                before = await verify_physical(config, vectors)
                verification.update(status="BEFORE_MATCHED_AFTER_PENDING", before=before)
                record_verification()
                rows = await run_experiment(config, journal, coordinator, factory)
                after = await verify_physical(config, vectors)
                verification.update(status="ENDPOINTS_MATCHED", after=after)
                record_verification()
            except Exception as exc:
                verification.update(status="FAILED_OR_DRIFTED", error_type=type(exc).__name__)
                record_verification()
                raise
            return {"status": "COMPLETED_PENDING_REVIEW", "completed_runs": len(rows), "planned_runs": config.planned_runs,
                    "snapshot_verified_before": before, "snapshot_verified_after": after,
                    "corpus_mode": config.corpus_mode, "immutable_corpus": config.corpus_mode == "immutable_snapshot",
                    "corpus_verification": verification,
                    "run_status_counts": dict(Counter(r["run_status"] for r in rows))}
        finally:
            if close:
                await close()
            await provider.close()
            journal.close()


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "run", "status", "reconcile", "reconcile-receipt", "observe-live"), nargs="?", default="preflight")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--verify-snapshot", "--verify-corpus", dest="verify_snapshot", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--job-id")
    parser.add_argument("--provider-job-name")
    parser.add_argument("--run-key")
    parser.add_argument("--stage")
    parser.add_argument("--receipt-file", type=Path)
    args = parser.parse_args()
    try:
        report = asyncio.run(main(args))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if report.get("status") == "BLOCKED" else 0
    except Exception as exc:
        # Exception strings from provider/DB clients may contain keys; do not print them.
        if isinstance(exc, ValueError):
            print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
