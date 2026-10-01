#!/usr/bin/env python3
r"""科別推薦回應時間量測：正式程式的 SymptomDepartmentService，依比對路徑分組各量數次。

量的是什麼：
    使用者問「XX 要掛哪一科」時，SymptomDepartmentService.suggest 從收到說法到給出建議科別
    （或保底）的時間。正規化層依向量分數走不同路徑，時間差很多，所以分開報：
    - 直接命中：向量分數 ≥ 0.95，只呼叫一次 embedding；
    - 中間帶：0.87～0.95，embedding 之後再請 LLM 從前 5 名挑一個；
    - 未命中：< 0.87，只呼叫 embedding，直接給保底科別；
    - 過長：超過 60 字，不呼叫任何 API，直接給保底。
    每句「預期」走的路徑是依 2026-09 的資料集分數挑的；實際走哪條由正式程式的 log 判斷，
    報告依實際路徑分組。embedding 另外計時，LLM 的時間約為總時間減 embedding。

    正規化層會用快取記住比過的說法，所以每一次都重建 SymptomNormalizer，量到的是沒有快取的情況。

不含：Gemini agent 判斷要呼叫科別推薦工具的時間、卡片產生與 LINE 推播。急迫度判斷擋在前面，
    也不在這裡量。

費用：
    **會呼叫 Gemini。** 預設 10 句 ×（1 次暖身＋5 次）：embedding（EMBEDDING_MODEL）約 54 次，
    每次不到 20 token；LLM（MODEL_NAME，正式環境 gemini-3.8-flash）只有中間帶的 3 句會叫，
    約 18 次，每次輸入約 350 token、輸出含思考約 500 token。以 2026-09 價目表估計合計約
    0.04 美元。為防止未命中的句子意外落進中間帶而多叫 LLM，LLM 呼叫超過 --max-llm-calls
    （預設 30）就停止。用的是正式環境的 API 金鑰，與使用者共用額度。

用法（專案根目錄，需要 .env 裡的 GEMINI_API_KEY）：
  PowerShell：.\.venv\Scripts\python.exe scripts\medical_bench\symptom_department_bench.py --env-label local
  Git Bash  ：.venv/Scripts/python.exe scripts/medical_bench/symptom_department_bench.py --env-label local
  GCP 暫時 pod：本腳本與 facility_search_bench.py 都複製到 /tmp，cd /app && python
                /tmp/symptom_department_bench.py --env-label gcp-pod --out /tmp/symptom-department-reports

輸出：<out>/symptom-department-<env-label>-YYYYMMDD-HHMMSS.json（每一次的原始數據）與同名 .md（摘要）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

# 同一個資料夾的院所查詢量測：共用專案根目錄的找法、.env 載入與統計。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from facility_search_bench import PROJECT_ROOT, TAIPEI, _stats  # noqa: E402

from langchain_google_genai import GoogleGenerativeAIEmbeddings  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.gemini.services.gemini_service import GeminiService  # noqa: E402
from app.services.medical.symptom_classification import (  # noqa: E402
    SymptomDepartmentService,
    SymptomNormalizer,
    load_symptom_table,
)
from app.services.medical.symptom_classification.normalizer import (  # noqa: E402
    DEFAULT_MAX_INPUT_CHARS,
)
from app.services.medical.symptom_classification.symptom_department_service import (  # noqa: E402
    RESULT_FALLBACK,
)
from app.services.medical.symptom_classification.vector_index import (  # noqa: E402
    AUTO_ACCEPT_SCORE,
    DEFAULT_VECTOR_PATH,
    EMBEDDING_TASK_TYPE,
    MIN_MATCH_SCORE,
    VECTOR_DIM,
    SymptomVectorIndex,
    table_content_hash,
)

PATH_AUTO = "auto"
PATH_LLM = "llm"
PATH_MISS = "miss"
PATH_TOO_LONG = "too_long"
PATH_LLM_ERROR = "llm_error"

PATH_TITLES = {
    PATH_AUTO: f"直接命中（≥ {AUTO_ACCEPT_SCORE}）",
    PATH_LLM: f"中間帶，交 LLM（{MIN_MATCH_SCORE}～{AUTO_ACCEPT_SCORE}）",
    PATH_MISS: f"未命中（< {MIN_MATCH_SCORE}）",
    PATH_TOO_LONG: f"過長（> {DEFAULT_MAX_INPUT_CHARS} 字）",
    PATH_LLM_ERROR: "LLM 呼叫失敗（降級為保底）",
}

# (預期路徑, 說法)。直接命中用對照表裡原樣的條目；中間帶取自
# evals/symptom_normalizer/dataset.jsonl 分數 0.90～0.92 的句子；未命中是與身體無關的話。
PHRASES: list[tuple[str, str]] = [
    (PATH_AUTO, "頭痛"),
    (PATH_AUTO, "咳嗽"),
    (PATH_AUTO, "腹痛"),
    (PATH_LLM, "拉肚子拉了三天"),
    (PATH_LLM, "吃東西就想吐"),
    (PATH_LLM, "常常忘東忘西"),
    (PATH_MISS, "今天天氣很好"),
    (PATH_MISS, "我想買一支新手機"),
    (PATH_MISS, "明天要去哪裡玩"),
    (
        PATH_TOO_LONG,
        "我最近這一個多月晚上都睡不好，常常半夜醒來就再也睡不著，白天上班沒精神、頭也昏昏的，"
        "吃東西也沒什麼胃口，體重好像掉了兩三公斤",
    ),
]

# 正規化層的 log 訊息 → 實際路徑（normalizer.py 的 logger.info／warning 文字）。
_PATH_MARKERS = [
    ("向量直接命中", PATH_AUTO),
    ("落在中間帶", PATH_LLM),
    ("低於門檻", PATH_MISS),
    ("說法過長", PATH_TOO_LONG),
]


class PathRecorder(logging.Handler):
    """從正式程式的 log 判斷這一次走了哪條路徑、LLM 有沒有失敗。依序執行，不需要分呼叫。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    def take_path(self) -> str | None:
        messages, self.messages = self.messages, []
        if any("LLM 兜底呼叫失敗" in m for m in messages):
            return PATH_LLM_ERROR
        for marker, path in _PATH_MARKERS:
            if any(marker in m for m in messages):
                return path
        return None


class TimedEmbedding:
    """包住正式的 aembed_query，只多記下每次 embedding 花的時間，行為不變。"""

    def __init__(self, embed) -> None:
        self._embed = embed
        self.seconds: list[float] = []

    async def __call__(self, text: str) -> list[float]:
        start = time.perf_counter()
        try:
            return await self._embed(text)
        finally:
            self.seconds.append(time.perf_counter() - start)


def build_dependencies():
    """與 app/dependencies.py 同一套組裝（不 import 它：那邊一載入就建整個 backend）。"""
    table = load_symptom_table()
    index = SymptomVectorIndex.load(
        DEFAULT_VECTOR_PATH,
        expected_hash=table_content_hash(table.terms),
        expected_model=settings.EMBEDDING_MODEL,
    )
    if index is None:
        # 沒有索引時正式程式改由 LLM 在全表裡挑，每句都叫一次大 prompt，費用與時間都不具代表性。
        raise SystemExit(
            "症狀向量檔不存在或與對照表不同步，請先執行 scripts/build_symptom_vectors.py。"
        )
    embeddings = GoogleGenerativeAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        task_type=EMBEDDING_TASK_TYPE,
        output_dimensionality=VECTOR_DIM,
    )
    gemini = GeminiService(api_key=settings.GEMINI_API_KEY, model_name=settings.MODEL_NAME)
    return table, index, embeddings, gemini


async def _suggest_once(table, index, embed: TimedEmbedding, gemini, text: str) -> dict:
    # 每次重建正規化器：它的快取以說法為 key，重複跑同一句會只量到快取。
    service = SymptomDepartmentService(
        table=table,
        normalizer=SymptomNormalizer(
            table_terms=table.terms,
            vector_index=index,
            embed_query=embed,
            gemini_service=gemini,
        ),
    )
    embed.seconds.clear()
    start = time.perf_counter()
    try:
        result = await service.suggest(text)
    except Exception as exc:  # noqa: BLE001 - 失敗也要記下來，不中斷整輪
        return {
            "ok": False,
            "seconds": round(time.perf_counter() - start, 3),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }
    seconds = time.perf_counter() - start
    return {
        "ok": True,
        "seconds": round(seconds, 3),
        "embed_seconds": round(sum(embed.seconds), 3) if embed.seconds else None,
        "kind": result.kind,
        "matched_term": result.matched_term,
        "departments": [c.canonical for c in result.candidates],
    }


async def run(runs: int, warmup: int, max_llm_calls: int) -> tuple[list[dict], int, int, bool]:
    table, index, embeddings, gemini = build_dependencies()
    embed = TimedEmbedding(embeddings.aembed_query)
    recorder = PathRecorder()
    normalizer_logger = logging.getLogger(
        "app.services.medical.symptom_classification.normalizer"
    )
    normalizer_logger.setLevel(logging.INFO)
    normalizer_logger.addHandler(recorder)
    normalizer_logger.propagate = False

    rows: list[dict] = []
    embed_calls = llm_calls = 0
    stopped = False
    for expected, text in PHRASES:
        for run_index in range(1 - warmup, runs + 1):
            result = await _suggest_once(table, index, embed, gemini, text)
            path = recorder.take_path()
            embed_calls += len(embed.seconds)
            llm_calls += path in (PATH_LLM, PATH_LLM_ERROR)
            if run_index >= 1:
                rows.append(
                    {"text": text, "expected_path": expected, "path": path, "run": run_index, **result}
                )
                status = (
                    f"{result['seconds']:.2f}s（{path}）→ "
                    f"{result['matched_term'] or '保底'}：{'、'.join(result['departments']) or '—'}"
                    if result["ok"]
                    else f"失敗 {result['error']}"
                )
                print(f"  [{text[:12]}] 第 {run_index} 次  {status}", flush=True)
            if llm_calls >= max_llm_calls:
                print(f"LLM 已呼叫 {llm_calls} 次，達到 --max-llm-calls，停止。", flush=True)
                stopped = True
                return rows, embed_calls, llm_calls, stopped
    return rows, embed_calls, llm_calls, stopped


def _summaries(rows: list[dict]) -> list[dict]:
    summaries = []
    for path, title in PATH_TITLES.items():
        mine = [r for r in rows if r["path"] == path or (not r["ok"] and r["expected_path"] == path)]
        if not mine:
            continue
        ok = [r for r in mine if r["ok"]]
        embed = [r["embed_seconds"] for r in ok if r.get("embed_seconds") is not None]
        summaries.append(
            {
                "path": path,
                "title": title,
                "texts": sorted({r["text"] for r in mine}, key=lambda t: len(t)),
                "failures": len(mine) - len(ok),
                "latency_seconds": _stats([r["seconds"] for r in ok]),
                "embed_seconds": _stats(embed),
                "fallback": sum(r["kind"] == RESULT_FALLBACK for r in ok),
            }
        )
    return summaries


def _markdown(meta: dict, summaries: list[dict], rows: list[dict]) -> str:
    lines = [
        f"# 科別推薦回應時間 {meta['started_at']}",
        "",
        f"- 執行環境：**{meta['env_label']}**（{meta['platform']}，Python {meta['python']}）",
        f"- 模型：LLM `{meta['model']}`，embedding `{meta['embedding_model']}`",
        f"- 每句 {meta['runs']} 次（另有 {meta['warmup']} 次暖身不列入），依序執行、不併發；"
        "每次重建正規化器，沒有快取",
        "- 量的是 SymptomDepartmentService.suggest 從收到說法到給出科別；不含 agent 選工具、"
        "卡片與 LINE 推播。依正式程式 log 判斷的實際路徑分組。",
        f"- 這次呼叫：embedding {meta['embed_calls']} 次、LLM {meta['llm_calls']} 次（皆含暖身）"
        + ("；**達到 LLM 次數上限，提早停止**" if meta["stopped_early"] else ""),
        "",
        "| 路徑 | 句數 | 中位數 | 平均 | 最快 | 最慢 | embedding 中位數 | 保底 | 失敗 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        lat = s["latency_seconds"]
        fmt = (lambda key: f"{lat[key]:.2f}s") if lat.get("n") else (lambda key: "—")
        emb = s["embed_seconds"]
        emb_text = f"{emb['median']:.2f}s" if emb.get("n") else "—"
        lines.append(
            f"| {s['title']} | {len(s['texts'])} | {fmt('median')} | {fmt('mean')} "
            f"| {fmt('min')} | {fmt('max')} | {emb_text} | {s['fallback']} | {s['failures']} |"
        )
    moved = sorted({(r["text"], r["expected_path"], r["path"]) for r in rows
                    if r["ok"] and r["path"] != r["expected_path"]})
    lines += ["", "各句結果（第 1 次）：", ""]
    for r in rows:
        if r["run"] == 1 and r["ok"]:
            lines.append(
                f"- 「{r['text'][:20]}{'…' if len(r['text']) > 20 else ''}」→ "
                f"{r['matched_term'] or '保底'}：{'、'.join(r['departments']) or '—'}"
            )
    if moved:
        lines += ["", "實際路徑與預期不同："]
        lines += [f"- 「{t[:20]}」預期 {e}，實際 {p}" for t, e, p in moved]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="每句量幾次（預設 5）")
    parser.add_argument(
        "--warmup", type=int, default=1, help="每句的暖身次數，不列入統計（預設 1）"
    )
    parser.add_argument(
        "--env-label", required=True, help="執行環境，寫進報告與摘要，例如 local、gcp-pod"
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=30,
        help="LLM 呼叫達到這個次數就停止，防止費用失控（預設 30，正常約 18）",
    )
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "evals" / "symptom_department" / "reports"),
        help="報告輸出資料夾（預設 evals/symptom_department/reports）",
    )
    args = parser.parse_args()

    if not settings.GEMINI_API_KEY:
        raise SystemExit("缺少 GEMINI_API_KEY。")
    started = datetime.now(TAIPEI)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    per_phrase = args.runs + args.warmup
    print(
        f"科別推薦量測：{args.env_label}，{len(PHRASES)} 句，每句 {args.runs} 次＋暖身 {args.warmup} 次；"
        f"預計 embedding 約 {per_phrase * sum(p != PATH_TOO_LONG for p, _ in PHRASES)} 次、"
        f"LLM 約 {per_phrase * sum(p == PATH_LLM for p, _ in PHRASES)} 次（上限 {args.max_llm_calls}）"
    )
    rows, embed_calls, llm_calls, stopped = asyncio.run(
        run(args.runs, args.warmup, args.max_llm_calls)
    )
    summaries = _summaries(rows)

    meta = {
        "started_at": started.isoformat(timespec="seconds"),
        "env_label": args.env_label,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "model": settings.MODEL_NAME,
        "embedding_model": settings.EMBEDDING_MODEL,
        "runs": args.runs,
        "warmup": args.warmup,
        "embed_calls": embed_calls,
        "llm_calls": llm_calls,
        "max_llm_calls": args.max_llm_calls,
        "stopped_early": stopped,
        "measures": "SymptomDepartmentService.suggest；不含 agent 選工具、卡片與 LINE 推播",
    }
    phrases = [{"expected_path": p, "text": t} for p, t in PHRASES]
    report = {"meta": meta, "phrases": phrases, "summary": summaries, "runs": rows}

    json_path = out / f"symptom-department-{args.env_label}-{stamp}.json"
    md_path = out / f"symptom-department-{args.env_label}-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(meta, summaries, rows), encoding="utf-8")
    print(f"\n{_markdown(meta, summaries, rows)}")
    print(f"報告：{json_path}\n摘要：{md_path}")


if __name__ == "__main__":
    main()
