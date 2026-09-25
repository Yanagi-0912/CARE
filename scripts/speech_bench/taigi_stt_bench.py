#!/usr/bin/env python3
r"""台語 STT 量測：正式程式的 Taigi 台語語音轉文字，五種長度各量數次（時間與錯字率）。

量的是什麼：
    把一段台語音檔交給正式程式的台語那一路（MediaProcessorService._transcribe_taiwanese_or_none：
    解碼成 16 kHz → 在停頓處切成每段不超過 25 秒 → 最多 3 段同時送 Taigi STT → 用空白接起來），
    量「交出音檔 → 拿到逐字稿」的時間，含解碼與切段。外面套上與正式程式相同的 8 秒總預算
    （mutimedia_processor.TAIGI_PARALLEL_BUDGET_SECONDS），超過記為逾時——正式環境此時會
    放掉台語那路、改用 Gemini 的華語逐字稿。逐字稿與原句比對算字元錯誤率（CER，比對前去掉
    標點與空白）。

不含：LINE 下載音檔、Gemini 那一路（正式環境與台語這路平行跑，使用者等的是較慢的那條）、
    選用哪份逐字稿（speech_language.choose_transcript）與後面的回答。

音檔從哪來：
    五句與台語 TTS 量測共用（taigi_tts_bench.SENTENCES，台語漢字，含標點剛好 10／25／50／100／
    150 字），先用正式程式的 Taigi TTS 預設女聲念成 WAV，再送去轉文字。合成那一步不計時。
    限制：
    - 這是合成語音，發音標準、沒有雜音；真人（尤其長輩）錄音只會更難，錯字率要當成下限。
    - 正式環境收到的是手機錄的 m4a，這裡送的是 Taigi 回的 WAV；兩者都先解碼成同一種 PCM
      才切段送出，差別只在解碼時間。
    - 台語漢字的寫法不統一（例如「疼／痛」「啉／飲」），STT 用了別的寫法也會算錯，CER 會
      偏高；要看逐字稿（json 的 transcript）才知道是真的聽錯還是寫法不同。

費用與速率：
    **會呼叫 Taigi API（STT 與 TTS），不呼叫 Gemini。** 用的是正式環境的金鑰，與使用者共用額度
    與速率限制。預設每種長度 1 次暖身＋5 次正式，共 30 次轉文字；100 字那句切成 2 段、150 字
    那句切成 3 段（2026-09-25 實測），每次同時送出，所以 STT 請求共 48 個，另外合成音檔用 5 次
    TTS。2026-09-25 以 8 秒間隔跑，150 字最後一次（第 48 個請求左右）撞到 429；長句一次送好幾段，
    間隔要比單段的長，所以改成每次轉文字之間預設等 15 秒（--interval），整輪約 10 分鐘。建議避開使用者多的時段。

用法（專案根目錄，需要 .env 裡的 TAIGI_API_KEY）：
  PowerShell：.\.venv\Scripts\python.exe scripts\speech_bench\taigi_stt_bench.py --env-label local
  Git Bash  ：.venv/Scripts/python.exe scripts/speech_bench/taigi_stt_bench.py --env-label local
  GCP 暫時 pod：chinese_tts_bench.py、chinese_stt_bench.py、taigi_tts_bench.py 與本腳本都複製到
                /tmp，cd /app && python /tmp/taigi_stt_bench.py --env-label gcp-pod
                --out /tmp/taigi-stt-reports（步驟見 evals/stt/README.md）

輸出：<out>/taigi-stt-<env-label>-YYYYMMDD-HHMMSS.json（每一次的原始數據與逐字稿）與同名 .md。
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

# 本機從 .env 讀 TAIGI_API_KEY；pod 裡由環境變數提供（見 evals/stt/README.md）。
# 一定要在 import 任何 app 模組之前載入：設定是在 import 當下從環境變數讀的。
try:
    from dotenv import load_dotenv

    for _candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (_candidate / ".env").is_file():
            load_dotenv(_candidate / ".env")
            break
except ImportError:  # pragma: no cover - 正式映像有裝 python-dotenv
    pass

# 同一個資料夾的其他量測腳本：共用五句台語、聲音設定、CER 算法與統計。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chinese_stt_bench import _cer, _normalize  # noqa: E402
from chinese_tts_bench import PROJECT_ROOT, TAIPEI, _stats  # noqa: E402
from taigi_tts_bench import SENTENCES, SPEED, VOICE_LABEL  # noqa: E402

from app.core.user_language import TAIWANESE_LANGUAGE  # noqa: E402
from app.services.media.mutimedia_processor import (  # noqa: E402
    NO_CONTENT_TEXT,
    TAIGI_PARALLEL_BUDGET_SECONDS,
    TAIGI_STT_CONCURRENCY,
    MediaProcessorService,
)
from app.services.speech import audio  # noqa: E402
from app.services.speech.taigi_client import (  # noqa: E402
    STT_TIMEOUT_SECONDS,
    TaigiClient,
)


async def _transcribe_once(
    processor: MediaProcessorService, path: Path, reference: str
) -> dict:
    start = time.perf_counter()
    try:
        # 與 MediaProcessorService._taigi_within_budget 相同的總預算，但逾時與失敗分開記。
        heard = await asyncio.wait_for(
            processor._transcribe_taiwanese_or_none(path),
            timeout=TAIGI_PARALLEL_BUDGET_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError):
        return {
            "ok": False,
            "timeout": True,
            "seconds": round(time.perf_counter() - start, 3),
            "error": f"超過 {TAIGI_PARALLEL_BUDGET_SECONDS:g} 秒總預算",
        }
    seconds = round(time.perf_counter() - start, 3)
    # 正式程式把錯誤吞掉、回 None（原因寫在 log 的 warning），這裡照樣記為失敗。
    if heard is None:
        return {"ok": False, "timeout": False, "seconds": seconds, "error": "台語 STT 失敗（見上方 log）"}
    if heard == NO_CONTENT_TEXT:
        return {"ok": False, "timeout": False, "seconds": seconds, "error": "沒有聽到內容"}
    cer = _cer(reference, heard)
    return {
        "ok": True,
        "timeout": False,
        "seconds": seconds,
        "transcript": heard,
        "cer": round(cer, 4),
        "exact": cer == 0,
    }


async def run(
    runs: int, warmup: int, interval: float, audio_dir: Path
) -> tuple[list[dict], list[dict]]:
    client = TaigiClient()
    # 只用台語那一路；Gemini 那一路不會被呼叫（GeminiTranscriber 第一次用到才建模型）。
    processor = MediaProcessorService(taigi_client=client)
    rows: list[dict] = []
    sentences: list[dict] = []
    first_call = True

    async def call(path: Path, text: str) -> dict:
        # 每次轉文字之間都等 interval 秒，含暖身：連續打會撞到 Taigi 的速率限制（429）。
        nonlocal first_call
        if not first_call:
            await asyncio.sleep(interval)
        first_call = False
        return await _transcribe_once(processor, path, text)

    for label, text in SENTENCES:
        if not first_call:
            await asyncio.sleep(interval)
        wav = await asyncio.to_thread(
            client.synthesize_wav, text, voice_label=VOICE_LABEL, speed=SPEED
        )
        path = audio_dir / f"{TAIWANESE_LANGUAGE}-{label}.wav"
        path.write_bytes(wav)
        # 段數與正式程式同一套切法（不計時），長句會切成好幾段、同時送出。
        chunks, rate = MediaProcessorService._decode_and_split(path)
        sentences.append(
            {
                "label": label,
                "chars_with_punctuation": len(text),
                "chars_without_punctuation": len(_normalize(text)),
                "audio_seconds": round(sum(audio.pcm_duration_ms(c, rate) for c in chunks) / 1000, 2),
                "chunks": len(chunks),
                "text": text,
            }
        )
        # 暖身：第一次呼叫要多建一次連線，不列入統計。
        for _ in range(warmup):
            await call(path, text)
        for run_index in range(1, runs + 1):
            result = await call(path, text)
            rows.append({"label": label, "run": run_index, **result})
            if result["ok"]:
                status = f"{result['seconds']:.2f}s  CER {result['cer']:.1%}  {result['transcript'][:40]}"
            else:
                status = f"{'逾時' if result['timeout'] else '失敗'} {result['error']}"
            print(f"  [{label:>3} 字] 第 {run_index} 次  {status}", flush=True)
    return sentences, rows


def _summaries(sentences: list[dict], rows: list[dict]) -> list[dict]:
    summaries = []
    for sentence in sentences:
        mine = [r for r in rows if r["label"] == sentence["label"]]
        ok = [r for r in mine if r["ok"]]
        timeouts = sum(1 for r in mine if r["timeout"])
        cers = [r["cer"] for r in ok]
        summaries.append(
            {
                "label": sentence["label"],
                "chars_with_punctuation": sentence["chars_with_punctuation"],
                "audio_seconds": sentence["audio_seconds"],
                "chunks": sentence["chunks"],
                "failures": len(mine) - len(ok) - timeouts,
                "timeouts": timeouts,
                "latency_seconds": _stats([r["seconds"] for r in ok]),
                "exact": sum(1 for r in ok if r["exact"]),
                "cer_mean": round(statistics.mean(cers), 4) if cers else None,
                "cer_max": round(max(cers), 4) if cers else None,
            }
        )
    return summaries


def _markdown(meta: dict, summaries: list[dict]) -> str:
    lines = [
        f"# 台語 STT 回應時間與錯字率 {meta['started_at']}",
        "",
        f"- 執行環境：**{meta['env_label']}**（{meta['platform']}，Python {meta['python']}）",
        f"- 引擎：Taigi AI Labs STT（stt_best_billing），正式程式的台語那一路；每段逾時 "
        f"{meta['stt_timeout_seconds']} 秒、總預算 {meta['budget_seconds']:g} 秒，長錄音在停頓處切成每段"
        f"不超過 25 秒、最多 {meta['concurrency']} 段同時送",
        f"- 音檔：Taigi TTS `{meta['voice_label']}`（speed {meta['speed']}）念寫死的台語漢字，合成不計時；"
        "合成語音發音標準、無雜音，錯字率應視為下限",
        f"- 每種長度 {meta['runs']} 次（另有 {meta['warmup']} 次暖身不列入），依序執行、不併發，"
        f"每次間隔 {meta['interval_seconds']:g} 秒",
        "- 時間是「交出音檔 → 拿到逐字稿」，含解碼與切段；不含 LINE 下載與平行的 Gemini 那一路",
        "- 錯字率（CER）＝編輯距離 ÷ 原句字數，比對前去掉標點與空白；台語漢字寫法不同（例如「疼／痛」）"
        "也算錯，CER 會偏高，逐字稿見 json",
        "",
        "| 文字長度 | 音檔長度 | 段數 | 中位數 | 平均 | 最快 | 最慢 | 完全正確 | 平均 CER | 最差 CER "
        "| 失敗 | 逾時 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        lat = s["latency_seconds"]
        fmt = (lambda key: f"{lat[key]:.2f}s") if lat.get("n") else (lambda key: "—")
        audio_len = f"{s['audio_seconds']:.1f}s" if s["audio_seconds"] is not None else "—"
        ok_count = lat.get("n", 0)
        cer_mean = f"{s['cer_mean']:.1%}" if s["cer_mean"] is not None else "—"
        cer_max = f"{s['cer_max']:.1%}" if s["cer_max"] is not None else "—"
        lines.append(
            f"| {s['label']} 字 | {audio_len} | {s['chunks']} | {fmt('median')} | {fmt('mean')} "
            f"| {fmt('min')} | {fmt('max')} | {s['exact']}/{ok_count} | {cer_mean} | {cer_max} "
            f"| {s['failures']} | {s['timeouts']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="每種長度量幾次（預設 5）")
    parser.add_argument(
        "--warmup", type=int, default=1, help="每種長度的暖身次數，不列入統計（預設 1）"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="每次轉文字之間等幾秒，避免撞到速率限制（預設 15）",
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
        help="把送去轉文字的 WAV 留在 evals/stt/samples（不進版控）；預設用完即刪",
    )
    args = parser.parse_args()

    if not TaigiClient().available():
        raise SystemExit(
            "沒有 TAIGI_API_KEY：本機請確認 .env，pod 裡請照 evals/stt/README.md 掛上金鑰。"
        )

    started = datetime.now(TAIPEI)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(
        f"台語 STT 量測：{args.env_label}，每種長度 {args.runs} 次＋暖身 {args.warmup} 次"
        f"（共 {len(SENTENCES) * (args.runs + args.warmup)} 次轉文字、{len(SENTENCES)} 次合成，"
        f"間隔 {args.interval:g} 秒）"
    )
    if args.save_audio:
        audio_dir = PROJECT_ROOT / "evals" / "stt" / "samples"
        audio_dir.mkdir(parents=True, exist_ok=True)
        sentences, rows = asyncio.run(run(args.runs, args.warmup, args.interval, audio_dir))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            sentences, rows = asyncio.run(run(args.runs, args.warmup, args.interval, Path(tmp)))
    summaries = _summaries(sentences, rows)

    meta = {
        "started_at": started.isoformat(timespec="seconds"),
        "env_label": args.env_label,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "language": TAIWANESE_LANGUAGE,
        "stt_timeout_seconds": STT_TIMEOUT_SECONDS,
        "budget_seconds": TAIGI_PARALLEL_BUDGET_SECONDS,
        "concurrency": TAIGI_STT_CONCURRENCY,
        "voice_label": VOICE_LABEL,
        "speed": SPEED,
        "runs": args.runs,
        "warmup": args.warmup,
        "interval_seconds": args.interval,
        "measures": (
            "交出音檔到拿到逐字稿（含解碼與切段）；CER 比對前去掉標點與空白；"
            "音檔為 Taigi TTS 合成語音"
        ),
    }
    report = {"meta": meta, "sentences": sentences, "summary": summaries, "runs": rows}

    json_path = out / f"taigi-stt-{args.env_label}-{stamp}.json"
    md_path = out / f"taigi-stt-{args.env_label}-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(meta, summaries), encoding="utf-8")
    print(f"\n{_markdown(meta, summaries)}")
    print(f"報告：{json_path}\n摘要：{md_path}")


if __name__ == "__main__":
    main()
