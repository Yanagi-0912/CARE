#!/usr/bin/env python3
r"""國語 TTS 回應時間量測：正式程式的 edge-tts 引擎，五種長度各量數次。

量的是什麼：
    「送出文字 → 收到完整 mp3」的時間。edge-tts 在微軟的伺服器上合成，這台機器只
    負責送文字、收音檔，所以數字反映的是「從這台機器的網路呼叫 edge-tts 要多久」，
    幾乎與本機運算能力無關。要代表正式環境，就在 GCP VM 上用 backend 的映像開一個
    暫時獨立 pod 跑同一支腳本（步驟見 evals/tts/README.md）。千萬不要在 care-backend
    pod 裡跑：它只有 1 份、正在服務使用者，和腳本共用同一個容器的記憶體與 CPU 上限，
    碰到上限 backend 會被 OOMKilled。

不含：存檔、產生音檔網址與 LINE 推播。使用者實際等待的時間比這裡長。

用法（專案根目錄）：
  PowerShell：.\.venv\Scripts\python.exe scripts\speech_bench\chinese_tts_bench.py --env-label local
  Git Bash  ：.venv/Scripts/python.exe scripts/speech_bench/chinese_tts_bench.py --env-label local
  GCP 暫時 pod：cd /app && python /tmp/chinese_tts_bench.py --env-label gcp-pod --out /tmp/tts-reports

輸出：<out>/chinese-tts-<env-label>-YYYYMMDD-HHMMSS.json（每一次的原始數據）與同名 .md（摘要）。
edge-tts 免費、不需金鑰；本腳本不呼叫 Gemini。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import platform
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path


def _project_root() -> Path:
    """找含有 CARE 程式（app/services）的目錄：先看工作目錄，再從本檔往上逐層找。

    在 repo 裡執行時是 repo 根目錄；被 kubectl cp 到 pod 的 /tmp、在 /app 底下執行時
    是 /app。不能固定往上取第幾層：/tmp/chinese_tts_bench.py 往上只有兩層。
    """
    here = Path(__file__).resolve()
    for candidate in (Path.cwd(), *here.parents):
        if (candidate / "app" / "services").is_dir():
            return candidate
    raise SystemExit(
        "找不到 CARE 的 app 套件：請在專案根目錄（pod 裡是 /app）底下執行。"
    )


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.line_messaging.reply.tts_service import (  # noqa: E402
    DEFAULT_VOICE_GENDER,
    VOICE_BY_LANGUAGE,
    EdgeTTSEngine,
)

LANGUAGE = "zh-TW"
# 使用者沒設定時實際聽到的聲音（tts_service.DEFAULT_VOICE_GENDER）。
VOICE = VOICE_BY_LANGUAGE[LANGUAGE][DEFAULT_VOICE_GENDER]
RATE = "+0%"
TAIPEI = timezone(timedelta(hours=8))

# 標籤就是含標點的字數（2026-09-25 調整成剛好 10／25／50／100／150 字）；報告另外記錄不含標點
# 的字數。國語 STT 量測（chinese_stt_bench.py）也用這五句，改這裡兩邊一起變。
SENTENCES: list[tuple[str, str]] = [
    ("10", "記得按時服藥並多休息"),
    ("25", "若持續出現頭痛和發燒症狀，建議多休息並注意身體變化"),
    (
        "50",
        "如果最近持續出現頭痛、發燒、咳嗽或全身無力等症狀，建議先充分休息並補充水分，"
        "同時持續觀察身體的狀況。",
    ),
    (
        "100",
        "如果最近持續出現頭痛、發燒、咳嗽、喉嚨疼痛或全身無力等症狀，建議先充分休息並適量"
        "補充水分，同時記錄每天的體溫與症狀變化。如果症狀持續數日仍未改善，或出現呼吸困難、"
        "胸痛及意識不清等情形，請儘速就醫治療。",
    ),
    (
        "150",
        "如果最近持續出現頭痛、發燒、咳嗽、喉嚨疼痛或全身無力等症狀，建議先充分休息並適量"
        "補充水分，同時記錄每天的體溫、症狀變化以及服藥後的身體反應。若本身有慢性疾病或正在"
        "服用其他藥物，就醫時應主動告知醫療人員。如果症狀持續數日仍未改善，或突然出現呼吸"
        "困難、劇烈胸痛、持續高燒及意識不清等情形，應儘速就醫治療。",
    ),
]

_PUNCTUATION = re.compile(r"[，。、！？；：,.!?;:「」\s]")


def _audio_seconds(mp3: bytes) -> float | None:
    try:
        from mutagen.mp3 import MP3

        return round(MP3(io.BytesIO(mp3)).info.length, 2)
    except Exception:  # noqa: BLE001
        return None


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3),
        "mean": round(statistics.mean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


async def _synthesize_once(engine: EdgeTTSEngine, text: str) -> dict:
    start = time.perf_counter()
    try:
        audio = await engine.synthesize(text, voice=VOICE, rate=RATE)
    except Exception as exc:  # noqa: BLE001 - 失敗也要記下來，不中斷整輪
        return {
            "ok": False,
            "seconds": round(time.perf_counter() - start, 3),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }
    return {
        "ok": True,
        "seconds": round(time.perf_counter() - start, 3),
        "bytes": len(audio),
        "audio_seconds": _audio_seconds(audio),
        "_audio": audio,
    }


async def run(
    runs: int, warmup: int, samples_dir: Path | None
) -> tuple[list[dict], list[dict]]:
    engine = EdgeTTSEngine()
    rows: list[dict] = []
    sentences: list[dict] = []
    for label, text in SENTENCES:
        sentences.append(
            {
                "label": label,
                "chars_with_punctuation": len(text),
                "chars_without_punctuation": len(_PUNCTUATION.sub("", text)),
                "text": text,
            }
        )
        # 暖身：第一次連線要多建一次 WebSocket 與 TLS，不列入統計。
        for _ in range(warmup):
            await _synthesize_once(engine, text)
        for run_index in range(1, runs + 1):
            result = await _synthesize_once(engine, text)
            audio = result.pop("_audio", None)
            if samples_dir is not None and audio and run_index == 1:
                (samples_dir / f"zh-TW-{label}.mp3").write_bytes(audio)
            rows.append({"label": label, "run": run_index, **result})
            status = (
                f"{result['seconds']:.2f}s"
                if result["ok"]
                else f"失敗 {result['error']}"
            )
            print(f"  [{label:>3} 字] 第 {run_index} 次  {status}", flush=True)
    return sentences, rows


def _summaries(sentences: list[dict], rows: list[dict]) -> list[dict]:
    summaries = []
    for sentence in sentences:
        mine = [r for r in rows if r["label"] == sentence["label"]]
        ok = [r for r in mine if r["ok"]]
        audio = [r["audio_seconds"] for r in ok if r.get("audio_seconds") is not None]
        summaries.append(
            {
                "label": sentence["label"],
                "chars_with_punctuation": sentence["chars_with_punctuation"],
                "chars_without_punctuation": sentence["chars_without_punctuation"],
                "failures": len(mine) - len(ok),
                "latency_seconds": _stats([r["seconds"] for r in ok]),
                "audio_seconds": round(statistics.median(audio), 2) if audio else None,
            }
        )
    return summaries


def _markdown(meta: dict, summaries: list[dict]) -> str:
    lines = [
        f"# 國語 TTS 回應時間 {meta['started_at']}",
        "",
        f"- 執行環境：**{meta['env_label']}**（{meta['platform']}，Python {meta['python']}）",
        f"- 引擎：edge-tts {meta['edge_tts_version']}，聲音 `{meta['voice']}`，語速 `{meta['rate']}`",
        f"- 每種長度 {meta['runs']} 次（另有 {meta['warmup']} 次暖身不列入），依序執行、不併發",
        "- 量的是「送出文字 → 收到完整 mp3」；edge-tts 在微軟伺服器合成，數字反映這台機器"
        "的網路到微軟的延遲，不含存檔、產生網址與 LINE 推播。",
        "",
        "| 長度 | 實際字數（含標點／不含） | 音檔長度 | 中位數 | 平均 | 最快 | 最慢 | 失敗 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        lat = s["latency_seconds"]
        fmt = (lambda key: f"{lat[key]:.2f}s") if lat.get("n") else (lambda key: "—")
        audio = f"{s['audio_seconds']:.1f}s" if s["audio_seconds"] is not None else "—"
        lines.append(
            f"| {s['label']} 字 | {s['chars_with_punctuation']}／{s['chars_without_punctuation']} "
            f"| {audio} | {fmt('median')} | {fmt('mean')} | {fmt('min')} | {fmt('max')} "
            f"| {s['failures']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="每種長度量幾次（預設 5）")
    parser.add_argument(
        "--warmup", type=int, default=1, help="每種長度的暖身次數，不列入統計（預設 1）"
    )
    parser.add_argument(
        "--env-label",
        required=True,
        help="執行環境，寫進報告與摘要，例如 local、gcp-pod",
    )
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "evals" / "tts" / "reports"),
        help="報告輸出資料夾（預設 evals/tts/reports）",
    )
    parser.add_argument(
        "--save-audio",
        action="store_true",
        help="把每種長度第一次的 mp3 存到 evals/tts/samples（不進版控）",
    )
    args = parser.parse_args()

    started = datetime.now(TAIPEI)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    samples_dir = None
    if args.save_audio:
        samples_dir = PROJECT_ROOT / "evals" / "tts" / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"國語 TTS 量測：{args.env_label}，每種長度 {args.runs} 次＋暖身 {args.warmup} 次"
    )
    sentences, rows = asyncio.run(run(args.runs, args.warmup, samples_dir))
    summaries = _summaries(sentences, rows)

    meta = {
        "started_at": started.isoformat(timespec="seconds"),
        "env_label": args.env_label,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "edge_tts_version": metadata.version("edge-tts"),
        "language": LANGUAGE,
        "voice": VOICE,
        "rate": RATE,
        "runs": args.runs,
        "warmup": args.warmup,
        "measures": "送出文字到收到完整 mp3；不含存檔、產生網址與 LINE 推播",
    }
    report = {"meta": meta, "sentences": sentences, "summary": summaries, "runs": rows}

    # 檔名帶執行環境，本機與 GCP 的報告放在同一個資料夾也分得出來。
    json_path = out / f"chinese-tts-{args.env_label}-{stamp}.json"
    md_path = out / f"chinese-tts-{args.env_label}-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_markdown(meta, summaries), encoding="utf-8")
    print(f"\n{_markdown(meta, summaries)}")
    print(f"報告：{json_path}\n摘要：{md_path}")


if __name__ == "__main__":
    main()
