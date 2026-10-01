#!/usr/bin/env python3
r"""國語 STT 量測：正式程式的 Gemini 語音轉文字，五種長度各量數次（時間與錯字率）。

量的是什麼：
    把一段國語 mp3 交給正式程式的 GeminiTranscriber（app/services/speech/gemini_stt.py，
    與 LINE 語音訊息同一個模型、prompt 與 8 秒逾時），量「送出音檔 → 拿到逐字稿」的
    時間，並與原句比對算字元錯誤率（CER，比對前去掉標點與空白）。

音檔從哪來：
    五句與國語 TTS 量測共用（chinese_tts_bench.SENTENCES，含標點剛好 10／25／50／100／150
    字），先用正式程式的 edge-tts 預設女聲念成 mp3，再送去轉文字。合成那一步不計時。
    限制：這是合成語音，發音標準、沒有雜音；真人（尤其長輩）錄音只會更難，錯字率要當成
    下限看待。

費用：**會呼叫 Gemini**。預設每種長度 1 次暖身＋5 次正式，共 30 次；音檔 3～40 秒，
    每次只有幾百到一千多個 token。edge-tts 免費。

用法（專案根目錄，需要 .env 裡的 GEMINI_API_KEY）：
  PowerShell：.\.venv\Scripts\python.exe scripts\speech_bench\chinese_stt_bench.py --env-label local
  Git Bash  ：.venv/Scripts/python.exe scripts/speech_bench/chinese_stt_bench.py --env-label local
  GCP 暫時 pod：兩支腳本都複製到 /tmp，cd /app && python /tmp/chinese_stt_bench.py
                --env-label gcp-pod --out /tmp/stt-reports（步驟見 evals/stt/README.md）

輸出：<out>/chinese-stt-<env-label>-YYYYMMDD-HHMMSS.json（每一次的原始數據與逐字稿）與同名 .md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# 本機從 .env 讀 GEMINI_API_KEY；pod 裡由環境變數提供（見 evals/stt/README.md）。
# 一定要在 import 任何 app 模組之前載入：設定是在 import 當下從環境變數讀的。
try:
    from dotenv import load_dotenv

    for _candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (_candidate / ".env").is_file():
            load_dotenv(_candidate / ".env")
            break
except ImportError:  # pragma: no cover - 正式映像有裝 python-dotenv
    pass

# 同一個資料夾的國語 TTS 腳本：共用五句、聲音設定與專案根目錄的找法。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chinese_tts_bench import (  # noqa: E402
    LANGUAGE,
    PROJECT_ROOT,
    RATE,
    SENTENCES,
    TAIPEI,
    VOICE,
    EdgeTTSEngine,
    _PUNCTUATION,
    _audio_seconds,
    _stats,
)

from app.services.speech.gemini_stt import (  # noqa: E402
    GEMINI_STT_MODEL,
    GEMINI_STT_THINKING_LEVEL,
    GEMINI_STT_TIMEOUT_SECONDS,
    GeminiTranscriber,
)


def _normalize(text: str) -> str:
    return _PUNCTUATION.sub("", text or "")


def _cer(reference: str, hypothesis: str) -> float:
    """字元錯誤率：編輯距離／原句字數（都先去掉標點與空白）。"""
    ref, hyp = _normalize(reference), _normalize(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / max(len(ref), 1)


async def _transcribe_once(transcriber: GeminiTranscriber, path: Path, reference: str) -> dict:
    start = time.perf_counter()
    try:
        heard = await transcriber.transcribe(path, LANGUAGE)
    except Exception as exc:  # noqa: BLE001 - 失敗（含 8 秒逾時）也要記下來，不中斷整輪
        return {
            "ok": False,
            "seconds": round(time.perf_counter() - start, 3),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }
    cer = _cer(reference, heard)
    return {
        "ok": True,
        "seconds": round(time.perf_counter() - start, 3),
        "transcript": heard,
        "cer": round(cer, 4),
        "exact": cer == 0,
    }


async def run(runs: int, warmup: int, audio_dir: Path) -> tuple[list[dict], list[dict]]:
    engine = EdgeTTSEngine()
    transcriber = GeminiTranscriber()
    rows: list[dict] = []
    sentences: list[dict] = []
    for label, text in SENTENCES:
        audio = await engine.synthesize(text, voice=VOICE, rate=RATE)
        path = audio_dir / f"zh-TW-{label}.mp3"
        path.write_bytes(audio)
        sentences.append(
            {
                "label": label,
                "chars_with_punctuation": len(text),
                "chars_without_punctuation": len(_normalize(text)),
                "audio_seconds": _audio_seconds(audio),
                "text": text,
            }
        )
        # 暖身：第一次呼叫要多建一次連線，不列入統計。
        for _ in range(warmup):
            await _transcribe_once(transcriber, path, text)
        for run_index in range(1, runs + 1):
            result = await _transcribe_once(transcriber, path, text)
            rows.append({"label": label, "run": run_index, **result})
            if result["ok"]:
                status = f"{result['seconds']:.2f}s  CER {result['cer']:.1%}  {result['transcript'][:40]}"
            else:
                status = f"失敗 {result['error']}"
            print(f"  [{label:>3} 字] 第 {run_index} 次  {status}", flush=True)
    return sentences, rows


def _summaries(sentences: list[dict], rows: list[dict]) -> list[dict]:
    summaries = []
    for sentence in sentences:
        mine = [r for r in rows if r["label"] == sentence["label"]]
        ok = [r for r in mine if r["ok"]]
        cers = [r["cer"] for r in ok]
        summaries.append(
            {
                "label": sentence["label"],
                "chars_with_punctuation": sentence["chars_with_punctuation"],
                "audio_seconds": sentence["audio_seconds"],
                "failures": len(mine) - len(ok),
                "latency_seconds": _stats([r["seconds"] for r in ok]),
                "exact": sum(1 for r in ok if r["exact"]),
                "cer_mean": round(statistics.mean(cers), 4) if cers else None,
                "cer_max": round(max(cers), 4) if cers else None,
            }
        )
    return summaries


def _markdown(meta: dict, summaries: list[dict]) -> str:
    lines = [
        f"# 國語 STT 回應時間與錯字率 {meta['started_at']}",
        "",
        f"- 執行環境：**{meta['env_label']}**（{meta['platform']}，Python {meta['python']}）",
        f"- 模型：`{meta['model']}`（thinking {meta['thinking_level']}，逾時 {meta['timeout_seconds']:.0f} 秒），"
        "正式程式的 GeminiTranscriber",
        f"- 音檔：edge-tts `{meta['voice']}` 合成的國語 mp3（合成不計時）；合成語音發音標準、無雜音，"
        "錯字率應視為下限",
        f"- 每種長度 {meta['runs']} 次（另有 {meta['warmup']} 次暖身不列入），依序執行、不併發",
        "- 時間是「送出音檔 → 拿到逐字稿」；錯字率（CER）比對前去掉標點與空白",
        "",
        "| 長度 | 音檔長度 | 中位數 | 平均 | 最快 | 最慢 | 完全正確 | 平均 CER | 最差 CER | 失敗 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        lat = s["latency_seconds"]
        fmt = (lambda key: f"{lat[key]:.2f}s") if lat.get("n") else (lambda key: "—")
        audio = f"{s['audio_seconds']:.1f}s" if s["audio_seconds"] is not None else "—"
        ok_count = lat.get("n", 0)
        cer_mean = f"{s['cer_mean']:.1%}" if s["cer_mean"] is not None else "—"
        cer_max = f"{s['cer_max']:.1%}" if s["cer_max"] is not None else "—"
        lines.append(
            f"| {s['label']} 字 | {audio} | {fmt('median')} | {fmt('mean')} | {fmt('min')} "
            f"| {fmt('max')} | {s['exact']}/{ok_count} | {cer_mean} | {cer_max} | {s['failures']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="每種長度量幾次（預設 5）")
    parser.add_argument(
        "--warmup", type=int, default=1, help="每種長度的暖身次數，不列入統計（預設 1）"
    )
    parser.add_argument(
        "--env-label", required=True, help="執行環境，寫進檔名與報告，例如 local、gcp-pod"
    )
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "evals" / "stt" / "reports"),
        help="報告輸出資料夾（預設 evals/stt/reports）",
    )
    parser.add_argument(
        "--save-audio",
        action="store_true",
        help="把送去轉文字的 mp3 留在 evals/stt/samples（不進版控）；預設用完即刪",
    )
    args = parser.parse_args()

    if not GeminiTranscriber().available():
        raise SystemExit("沒有 GEMINI_API_KEY：本機請確認 .env，pod 裡請照 evals/stt/README.md 掛上金鑰。")

    started = datetime.now(TAIPEI)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(
        f"國語 STT 量測：{args.env_label}，每種長度 {args.runs} 次＋暖身 {args.warmup} 次"
        f"（共 {len(SENTENCES) * (args.runs + args.warmup)} 次 Gemini 呼叫）"
    )
    if args.save_audio:
        audio_dir = PROJECT_ROOT / "evals" / "stt" / "samples"
        audio_dir.mkdir(parents=True, exist_ok=True)
        sentences, rows = asyncio.run(run(args.runs, args.warmup, audio_dir))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            sentences, rows = asyncio.run(run(args.runs, args.warmup, Path(tmp)))
    summaries = _summaries(sentences, rows)

    meta = {
        "started_at": started.isoformat(timespec="seconds"),
        "env_label": args.env_label,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "language": LANGUAGE,
        "model": GEMINI_STT_MODEL,
        "thinking_level": GEMINI_STT_THINKING_LEVEL,
        "timeout_seconds": GEMINI_STT_TIMEOUT_SECONDS,
        "voice": VOICE,
        "rate": RATE,
        "runs": args.runs,
        "warmup": args.warmup,
        "measures": "送出音檔到拿到逐字稿；CER 比對前去掉標點與空白；音檔為 edge-tts 合成語音",
    }
    report = {"meta": meta, "sentences": sentences, "summary": summaries, "runs": rows}

    json_path = out / f"chinese-stt-{args.env_label}-{stamp}.json"
    md_path = out / f"chinese-stt-{args.env_label}-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(meta, summaries), encoding="utf-8")
    print(f"\n{_markdown(meta, summaries)}")
    print(f"報告：{json_path}\n摘要：{md_path}")


if __name__ == "__main__":
    main()
