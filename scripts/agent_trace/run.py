"""本地執行一次 CARE agent，等待 LangSmith 完整 trace 並輸出驗收 JSON。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DEFAULT_CASES = ROOT / "evals/agent_trace/cases.json"


def load_case(path: Path, case_id: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("不支援的案例 schema_version")
    case = next(c for c in data["cases"] if c["id"] == case_id)
    for key in ("user_input", "expected_outputs", "required_run_names"):
        if key not in case:
            raise ValueError(f"案例缺少 {key}")
    return case


async def execute(case: dict, timeout: float) -> dict:
    from dotenv import load_dotenv

    # 既有 shell 設定優先；金鑰只由環境或本地 .env 取得。
    load_dotenv(ROOT / ".env", override=False)
    if not (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")):
        raise RuntimeError("缺少 LANGSMITH_API_KEY，尚未執行 agent")
    if not (os.getenv("LANGSMITH_ENDPOINT") or os.getenv("LANGCHAIN_ENDPOINT")):
        raise RuntimeError("請明確設定 LANGSMITH_ENDPOINT，確認帳號資料區域")
    os.environ["LANGSMITH_TRACING"] = "true"
    from langsmith import Client, traceable, tracing_context
    from langchain_core.tracers.langchain import wait_for_all_tracers
    from app.dependencies import _care_agent

    project = os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "CARE"
    client = Client()
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc).isoformat()

    @traceable(name=f"CARE Local Agent Demo - {case['id']}",
               run_type="chain", client=client, project_name=project)
    async def invoke(user_input: str) -> dict:
        result = await _care_agent.invoke(user_input=user_input)
        return {key: result.get(key) for key in
                ("response", "call_request_location", "emergency", "answer_kind")}

    started = time.monotonic()
    with tracing_context(enabled=True, client=client, project_name=project,
                         tags=["local", "manual-agent-demo"],
                         metadata={"runtime": "local CARE Python process", "case_id": case["id"]}):
        outputs = await invoke(case["user_input"], langsmith_extra={"run_id": run_id})
    wall_seconds = round(time.monotonic() - started, 3)
    await asyncio.to_thread(wait_for_all_tracers)
    await asyncio.to_thread(client.flush)

    # flush 後後端仍可能只看得到部分 spans；必須等 root 與子步驟結束。
    deadline = time.monotonic() + timeout
    root = None
    runs = []
    complete = False
    while time.monotonic() < deadline:
        try:
            root = await asyncio.to_thread(client.read_run, run_id)
            runs = await asyncio.to_thread(
                lambda: list(client.list_runs(project_name=project, trace_id=run_id)))
        except Exception as exc:
            # 只有索引尚未可見的 404 可以重試；認證等錯誤直接交回呼叫端。
            from langsmith.utils import LangSmithNotFoundError
            if not isinstance(exc, LangSmithNotFoundError):
                raise
        else:
            names = {run.name for run in runs}
            complete = bool(root.end_time and root.outputs is not None and runs
                            and all(run.end_time for run in runs)
                            and set(case["required_run_names"]).issubset(names))
            if complete:
                break
        await asyncio.sleep(2)

    checks = {
        "trace_complete": complete,
        "outputs_match": all(outputs.get(k) == v for k, v in case["expected_outputs"].items()),
        "response_matches": case.get("expected_response_contains", "") in (outputs.get("response") or ""),
        "required_runs_present": set(case["required_run_names"]).issubset({r.name for r in runs}),
        "no_run_errors": bool(runs) and all(not r.error for r in runs) and bool(root) and not root.error,
        "persisted_outputs_match": bool(root) and root.outputs == outputs,
    }
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.strip()
    return {
        "schema_version": 1,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "case_id": case["id"], "started_at": started_at,
        "runtime": "local CARE Python process", "source_commit": revision,
        "working_tree_dirty": bool(dirty), "project": project,
        "run_id": str(run_id),
        "trace_url": client.get_run_url(run=root) if root else None,
        "question": case["user_input"], "outputs": outputs,
        "wall_seconds": wall_seconds, "checks": checks,
        "root_end_time": str(root.end_time) if root and root.end_time else None,
        "runs": [{"id": str(r.id), "parent_id": str(r.parent_run_id) if r.parent_run_id else None,
                  "name": r.name, "type": r.run_type, "error": bool(r.error),
                  "end_time": str(r.end_time) if r.end_time else None}
                 for r in sorted(runs, key=lambda r: r.start_time)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", default="location_without_coordinates")
    parser.add_argument("--output", type=Path, default=ROOT / "evals/agent_trace/generated/latest.json")
    parser.add_argument("--trace-timeout", type=float, default=60,
                        help="agent 完成後等待 trace 可見的秒數，非 agent 執行 timeout")
    parser.add_argument("--validate-only", action="store_true",
                        help="離線確認案例可讀；不匯入 app、不呼叫外部服務")
    args = parser.parse_args()
    case = load_case(args.cases, args.case)
    if args.validate_only:
        print(json.dumps({"status": "CASE_VALID", "case_id": case["id"]}, ensure_ascii=False))
        return 0
    result = asyncio.run(execute(case, args.trace_timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "run_id", "trace_url", "checks")}, ensure_ascii=False))
    print(f"結果：{args.output}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
