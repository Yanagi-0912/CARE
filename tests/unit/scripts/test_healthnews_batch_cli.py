"""CLI 的提交閘門與唯讀預檢，不替換任何全域 client。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.scripts.test_healthnews_batch_config import fixture


async def test_run_requires_submit_before_any_client_is_created(tmp_path):
    from scripts.healthnews_batch_experiment import main
    path, _, _ = fixture(tmp_path)
    args = SimpleNamespace(command="run", submit=False, config=path)
    def forbidden(*args, **kwargs):
        raise AssertionError("CLI created provider without submit")
    with pytest.raises(ValueError, match="submit"):
        await main(args, provider_factory=forbidden)
    assert not (tmp_path / "output/journal.sqlite").exists()


async def test_blocked_preflight_creates_no_provider_and_reports_zero_requests(tmp_path):
    from scripts.healthnews_batch_experiment import main
    path, _, _ = fixture(tmp_path)
    args = SimpleNamespace(command="preflight", config=path, verify_snapshot=False, report=None)
    def forbidden(*args, **kwargs):
        raise AssertionError("preflight submitted provider request")
    report = await main(args, provider_factory=forbidden)
    assert report["status"] == "BLOCKED"
    assert report["planned_runs"] == 6
    assert report["batch_jobs_submitted"] == report["model_requests_submitted"] == 0
    assert not (tmp_path / "output").exists()


async def test_manual_receipt_reconciliation_requires_matching_hash_and_no_provider(tmp_path):
    import json
    from scripts.healthnews_batch_experiment import main
    from scripts.healthnews_batch.config import load_config
    from scripts.healthnews_batch.store import Journal
    path, _, root = fixture(tmp_path)
    config = load_config(path, code_root=root)
    config.output.mkdir()
    run_key = config.run_key("HN001", "B", 1)
    journal = Journal(config.output / "journal.sqlite")
    journal.bind_config(config.version, config.identity())
    journal.save_receipt(run_key, "cohere:0", "a" * 64, "unknown", {"error_type": "CancelledError"})
    journal.close()
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"input_hash": "b" * 64, "status": "done", "payload": {"results": []}}))
    args = SimpleNamespace(command="reconcile-receipt", config=path, run_key=run_key,
                           stage="cohere:0", receipt_file=receipt)
    def forbidden(*args, **kwargs):
        raise AssertionError("receipt reconciliation created provider")
    with pytest.raises(ValueError, match="hash"):
        await main(args, provider_factory=forbidden)
    receipt.write_text(json.dumps({"input_hash": "a" * 64, "status": "done", "payload": {"results": []}}))
    result = await main(args, provider_factory=forbidden)
    assert result["status"] == "RECEIPT_RECONCILED"
    journal = Journal(config.output / "journal.sqlite")
    journal.assert_no_uncertain_receipts()
    assert journal.get_receipt(run_key, "cohere:0", "a" * 64)["payload"] == {"results": []}
    journal.close()
