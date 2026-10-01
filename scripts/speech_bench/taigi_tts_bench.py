#!/usr/bin/env python3
r"""台語 TTS 回應時間量測：正式程式的 Taigi 台語 TTS 與轉檔，五種長度各量數次。

量的是什麼：
    正式程式念台語語音回覆的後兩步（tts_service._synthesize_taiwanese_or_none）：
    1. Taigi API：TaigiClient.synthesize_wav，「送出台語漢字 → 收到完整 WAV」；
    2. 轉檔：WAV → 16 kHz、48 kbps 單聲道 mp3（與正式程式同一組設定）。
    兩段分開計時。Taigi 在廠商的伺服器上合成，第 1 段反映「這台機器的網路到廠商」加上廠商
    的合成時間；第 2 段在本機 CPU 上做，受資源上限影響（暫時 pod 限 0.5 核）。

不含：
    - 念稿之前的 Gemini 改寫（TaigiTextConverter，華語 → 台語漢字）。正式環境要先等它，
      2026-09-15～09-22 中位數約 1.8 秒（當時是 3.8-flash，現在改 flash-lite）。本腳本
      直接用寫死的台語漢字句子，不呼叫 Gemini。
    - 存檔、產生音檔網址與 LINE 推播。使用者實際等待的時間比這裡長。

句子：
    國語 TTS 量測五句（chinese_tts_bench.SENTENCES）的台語漢字版，含標點剛好 10／25／50／
    100／150 字。正式程式不會把華語直接交給 Taigi（廠商文件：華語句子可能導致發音錯誤），
    所以這裡也不送華語。150 字仍在正式程式的念稿上限（taigi_text.TAIGI_SPEECH_HARD_MAX_CHARS
    ＝180）內。

費用與速率：
    **會呼叫 Taigi API**，用的是正式環境的金鑰，與使用者共用額度與速率限制。預設每種長度
    1 次暖身＋5 次正式，共 30 次。2026-09-19 實測 Taigi STT 連續打到第 11 次起回 429、
    每 5 秒一次則全過，TTS 假設同一套限制：每次呼叫之間預設間隔 6 秒（--interval），整輪
    約 3～4 分鐘。建議避開使用者多的時段。不呼叫 Gemini、不呼叫 edge-tts。

用法（專案根目錄，需要 .env 裡的 TAIGI_API_KEY）：
  PowerShell：.\.venv\Scripts\python.exe scripts\speech_bench\taigi_tts_bench.py --env-label local
  Git Bash  ：.venv/Scripts/python.exe scripts/speech_bench/taigi_tts_bench.py --env-label local
  GCP 暫時 pod：本腳本與 chinese_tts_bench.py 都複製到 /tmp，cd /app && python
                /tmp/taigi_tts_bench.py --env-label gcp-pod --out /tmp/taigi-tts-reports
                （步驟見 evals/tts/README.md）

輸出：<out>/taigi-tts-<env-label>-YYYYMMDD-HHMMSS.json（每一次的原始數據）與同名 .md（摘要）。
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# 本機從 .env 讀 TAIGI_API_KEY；pod 裡由環境變數提供（見 evals/tts/README.md）。
# 一定要在 import 任何 app 模組之前載入：設定是在 import 當下從環境變數讀的。
try:
    from dotenv import load_dotenv

    for _candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (_candidate / ".env").is_file():
            load_dotenv(_candidate / ".env")
            break
except ImportError:  # pragma: no cover - 正式映像有裝 python-dotenv
    pass

# 同一個資料夾的國語 TTS 腳本：共用專案根目錄的找法、標點規則與統計。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chinese_tts_bench import PROJECT_ROOT, TAIPEI, _PUNCTUATION, _stats  # noqa: E402

from app.core.user_language import TAIWANESE_LANGUAGE  # noqa: E402
from app.services.line_messaging.reply.tts_service import (  # noqa: E402
    TAIGI_MP3_BIT_RATE,
    TAIGI_MP3_COMPRESSION_LEVEL,
    TAIGI_MP3_SAMPLE_RATE,
)
from app.services.speech import audio  # noqa: E402
from app.services.speech.taigi_client import (  # noqa: E402
    DEFAULT_SPEED,
    DEFAULT_VOICE_LABEL,
    TTS_TIMEOUT_SECONDS,
    TaigiClient,
)

# 使用者沒設定時實際聽到的聲音與語速（taigi_client 的預設：女聲 normal_f2、speed 1.2）。
VOICE_LABEL = DEFAULT_VOICE_LABEL
SPEED = DEFAULT_SPEED

# 國語 TTS 五句（chinese_tts_bench.SENTENCES）的台語漢字版，用字照 taigi_text.PROMPT 的
# 規則（教育部《臺灣台語常用詞辭典》推薦用字）。標籤就是含標點的字數；報告另外記錄不含
# 標點的字數。改句子要重新對字數。
SENTENCES: list[tuple[str, str]] = [
    ("10", "記得照時食藥，加歇睏"),
    ("25", "若一直頭疼閣發燒，建議加歇睏，注意看身體有啥物變化"),
    (
        "50",
        "若是最近一直頭疼、發燒、咧嗽，抑是規身軀攏無力，建議先歇睏予飽、加啉寡水，"
        "同時注意看身體有啥物變化。",
    ),
    (
        "100",
        "若是最近一直頭疼、發燒、咧嗽、嚨喉疼，抑是規身軀攏無力，建議先歇睏予飽、加啉寡水，"
        "逐工共體溫佮症狀有啥物變化攏記落來。若是症狀過幾若工攏無較好，抑是喘袂過氣、胸坎疼、"
        "頭殼霧霧，就愛趕緊去病院予醫生看。",
    ),
    (
        "150",
        "若是最近一直頭疼、發燒、咧嗽、嚨喉疼，抑是規身軀無力，建議先歇睏予飽、加啉寡水，"
        "逐工共體溫、症狀的變化，佮食藥了後身體的反應記落來。若是本身有慢性病，抑是當咧食"
        "別種藥仔，看醫生的時愛家己共醫護人員講。若是症狀過幾若工攏無較好，抑是雄雄喘袂過氣、"
        "胸坎疼甲足厲害、一直發懸燒、頭殼霧霧，就愛緊去予醫生看。",
    ),
]


def _transcode(wav: bytes) -> tuple[bytes, int]:
    """與 tts_service._synthesize_taiwanese_or_none 同一組轉檔設定，回傳 (mp3, 音檔毫秒)。"""
    pcm, rate = audio.decode_to_pcm16_mono(io.BytesIO(wav), TAIGI_MP3_SAMPLE_RATE)
    mp3 = audio.encode_mp3(
        pcm,
        rate,
        bit_rate=TAIGI_MP3_BIT_RATE,
        compression_level=TAIGI_MP3_COMPRESSION_LEVEL,
    )
    return mp3, audio.pcm_duration_ms(pcm, rate)


def _synthesize_once(client: TaigiClient, text: str) -> dict:
    start = time.perf_counter()
    try:
        wav = client.synthesize_wav(text, voice_label=VOICE_LABEL, speed=SPEED)
    except Exception as exc:  # noqa: BLE001 - 失敗（含逾時、429）也要記下來，不中斷整輪
        return {
            "ok": False,
            "timeout": isinstance(exc, requests.Timeout),
            "api_seconds": round(time.perf_counter() - start, 3),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }
    api_seconds = time.perf_counter() - start
    start = time.perf_counter()
    try:
        mp3, duration_ms = _transcode(wav)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "timeout": False,
            "api_seconds": round(api_seconds, 3),
            "error": f"轉檔失敗 {type(exc).__name__}: {exc}"[:200],
        }
    transcode_seconds = time.perf_counter() - start
    return {
        "ok": True,
        "timeout": False,
        "api_seconds": round(api_seconds, 3),
        "transcode_seconds": round(transcode_seconds, 3),
        "total_seconds": round(api_seconds + transcode_seconds, 3),
        "wav_bytes": len(wav),
        "mp3_bytes": len(mp3),
        "audio_seconds": round(duration_ms / 1000, 2),
        "_audio": mp3,
    }


def run(
    runs: int, warmup: int, interval: float, samples_dir: Path | None
) -> tuple[list[dict], list[dict]]:
    client = TaigiClient()
    rows: list[dict] = []
    sentences: list[dict] = []
    first_call = True

    def call(text: str) -> dict:
        # 每次呼叫之間都等 interval 秒，含暖身：連續打會撞到 Taigi 的速率限制（429）。
        nonlocal first_call
        if not first_call:
            time.sleep(interval)
        first_call = False
        return _synthesize_once(client, text)

    for label, text in SENTENCES:
        sentences.append(
            {
                "label": label,
                "chars_with_punctuation": len(text),
                "chars_without_punctuation": len(_PUNCTUATION.sub("", text)),
                "text": text,
            }
        )
        # 暖身：第一次連線要多建一次 TLS，不列入統計。
        for _ in range(warmup):
            call(text)
        for run_index in range(1, runs + 1):
            result = call(text)
            mp3 = result.pop("_audio", None)
            if samples_dir is not None and mp3 and run_index == 1:
                (samples_dir / f"{TAIWANESE_LANGUAGE}-{label}.mp3").write_bytes(mp3)
            rows.append({"label": label, "run": run_index, **result})
            if result["ok"]:
                status = (
                    f"{result['api_seconds']:.2f}s  轉檔 {result['transcode_seconds']:.2f}s"
                    f"  音檔 {result['audio_seconds']:.1f}s"
                )
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
        audio_lengths = [r["audio_seconds"] for r in ok]
        summaries.append(
            {
                "label": sentence["label"],
                "chars_with_punctuation": sentence["chars_with_punctuation"],
                "chars_without_punctuation": sentence["chars_without_punctuation"],
                "failures": len(mine) - len(ok) - timeouts,
                "timeouts": timeouts,
                "api_seconds": _stats([r["api_seconds"] for r in ok]),
                "transcode_seconds": _stats([r["transcode_seconds"] for r in ok]),
                "total_seconds": _stats([r["total_seconds"] for r in ok]),
                "audio_seconds": (
                    round(statistics.median(audio_lengths), 2)
                    if audio_lengths
                    else None
                ),
            }
        )
    return summaries


def _markdown(meta: dict, summaries: list[dict]) -> str:
    lines = [
        f"# 台語 TTS 回應時間 {meta['started_at']}",
        "",
        f"- 執行環境：**{meta['env_label']}**（{meta['platform']}，Python {meta['python']}）",
        f"- 引擎：Taigi AI Labs TTS v6，聲音 `{meta['voice_label']}`，speed {meta['speed']}"
        f"（逾時 {meta['timeout_seconds']} 秒）；轉檔 {meta['mp3_sample_rate'] // 1000} kHz、"
        f"{meta['mp3_bit_rate'] // 1000} kbps、LAME 等級 {meta['mp3_compression_level']}",
        f"- 每種長度 {meta['runs']} 次（另有 {meta['warmup']} 次暖身不列入），依序執行、不併發，"
        f"每次呼叫間隔 {meta['interval_seconds']:g} 秒",
        "- 輸入是寫死的台語漢字（國語五句的台語版），不經過 Gemini 改寫",
        "- 中位數／平均／最快／最慢＝「送出台語漢字 → 收到完整 WAV」；轉檔＝WAV → mp3；合計＝兩者相加。"
        "不含 Gemini 改寫（正式環境要先等它）、存檔、產生網址與 LINE 推播。",
        "",
        "| 長度 | 實際字數（含標點／不含） | 音檔長度 | 中位數 | 平均 | 最快 | 最慢 "
        "| 轉檔中位數 | 轉檔最慢 | 合計中位數 | 失敗 | 逾時 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    def fmt(stats: dict, key: str) -> str:
        return f"{stats[key]:.2f}s" if stats.get("n") else "—"

    for s in summaries:
        api, tc, total = s["api_seconds"], s["transcode_seconds"], s["total_seconds"]
        audio_len = (
            f"{s['audio_seconds']:.1f}s" if s["audio_seconds"] is not None else "—"
        )
        lines.append(
            f"| {s['label']} 字 | {s['chars_with_punctuation']}／{s['chars_without_punctuation']} "
            f"| {audio_len} | {fmt(api, 'median')} | {fmt(api, 'mean')} | {fmt(api, 'min')} "
            f"| {fmt(api, 'max')} | {fmt(tc, 'median')} | {fmt(tc, 'max')} "
            f"| {fmt(total, 'median')} | {s['failures']} | {s['timeouts']} |"
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
        default=6.0,
        help="每次呼叫 Taigi 之間等幾秒，避免撞到速率限制（預設 6）",
    )
    parser.add_argument(
        "--env-label",
        required=True,
        help="執行環境，寫進檔名與報告，例如 local、gcp-pod",
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

    if not TaigiClient().available():
        raise SystemExit(
            "沒有 TAIGI_API_KEY：本機請確認 .env，pod 裡請照 evals/tts/README.md 掛上金鑰。"
        )

    started = datetime.now(TAIPEI)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    samples_dir = None
    if args.save_audio:
        samples_dir = PROJECT_ROOT / "evals" / "tts" / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

    calls = len(SENTENCES) * (args.runs + args.warmup)
    print(
        f"台語 TTS 量測：{args.env_label}，每種長度 {args.runs} 次＋暖身 {args.warmup} 次"
        f"（共 {calls} 次 Taigi 呼叫，間隔 {args.interval:g} 秒）"
    )
    sentences, rows = run(args.runs, args.warmup, args.interval, samples_dir)
    summaries = _summaries(sentences, rows)

    meta = {
        "started_at": started.isoformat(timespec="seconds"),
        "env_label": args.env_label,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "language": TAIWANESE_LANGUAGE,
        "voice_label": VOICE_LABEL,
        "speed": SPEED,
        "timeout_seconds": TTS_TIMEOUT_SECONDS,
        "mp3_sample_rate": TAIGI_MP3_SAMPLE_RATE,
        "mp3_bit_rate": TAIGI_MP3_BIT_RATE,
        "mp3_compression_level": TAIGI_MP3_COMPRESSION_LEVEL,
        "runs": args.runs,
        "warmup": args.warmup,
        "interval_seconds": args.interval,
        "measures": (
            "api_seconds：送出台語漢字到收到完整 WAV；轉檔：WAV 到 mp3；"
            "不含 Gemini 改寫、存檔、產生網址與 LINE 推播"
        ),
    }
    report = {"meta": meta, "sentences": sentences, "summary": summaries, "runs": rows}

    json_path = out / f"taigi-tts-{args.env_label}-{stamp}.json"
    md_path = out / f"taigi-tts-{args.env_label}-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_markdown(meta, summaries), encoding="utf-8")
    print(f"\n{_markdown(meta, summaries)}")
    print(f"報告：{json_path}\n摘要：{md_path}")


if __name__ == "__main__":
    main()
