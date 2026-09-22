"""Taigi AI Labs 的台語 TTS／STT（https://learn-language.tokyo）。

廠商文件原文放在工作區的 taigi-api-docs/（不進 repo：文件的禁止事項限制轉載）。
只用兩支端點：
- TTS v6：高速、無浮水印、直接回 WAV（22,050 Hz 單聲道）。v7／WebSocket 是邊產生
  邊送的 PCM 串流，但 LINE 語音訊息只收完整檔案的網址與長度，用不上；v4／v5 多一道
  AudioSeal 浮水印，那一步失敗時整個請求報錯（文件寫明不會降級）。
- STT `stt_best_billing`：台語專用模型，只回最佳一筆。

2026-09-14 以正式金鑰從本機實測：TTS 13 字 2.0～2.2 秒、約 110 字 5.4 秒；STT 6 秒
錄音 1.1～1.7 秒、24 秒 3.0 秒。

呼叫端都放在 asyncio.to_thread 裡，所以這裡維持同步 requests，跟 tts_service 呼叫
n8n 的做法一樣。
"""

from __future__ import annotations

import logging
import time

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

TTS_PATH = "/taigiStripe/synth_convert_api_limit_gender_v6"
STT_PATH = "/taigiSTT/stt_best_billing"

# 性別 → 廠商的固定聲音；未知性別照 tts_service 的慣例退回女聲。
VOICE_LABEL_BY_GENDER = {"female": "normal_f2", "male": "normal_m2"}
DEFAULT_VOICE_LABEL = VOICE_LABEL_BY_GENDER["female"]

# 語速檔位 → speed。normal 用廠商預設 1.2；slow／fast 比照 edge-tts 的 ±25%
# （tts_service.RATE_PERCENT）。廠商接受 0.5～2.0。
SPEED_BY_RATE = {"slow": 0.9, "normal": 1.2, "fast": 1.5}
DEFAULT_SPEED = SPEED_BY_RATE["normal"]

# TTS 在文字送出之後才合成、再 push 音檔（reply._push_tts_audio），逾時就改念
# 國語。實測 110 字 5.4 秒；比照 N8N_TTS_TIMEOUT_SECONDS 的 20 秒。
TTS_TIMEOUT_SECONDS = 20
# 每段最長 25 秒（audio.MAX_STT_CHUNK_SECONDS），實測 24 秒一段 3.0 秒，給 5 倍。
# 逾時改走 faster-whisper，那邊自己還有 120 秒的 webhook 預算。
STT_TIMEOUT_SECONDS = 15

# 只有 5xx 重試，429 不重試。2026-09-19 實測這支的速率限制：一次接一次連打，第 11 次
# 起回 429（前 10 次都成功，共 15.7 秒）；改成每 5 秒一次打 8 次則全過。窗口是分鐘級的，
# 所以 429 之後短暫退避再送必然再撞一次——當天用 0.5 秒退避重試，重試也是 429。等到窗口
# 放行要幾十秒，遠超過平行辨識的 8 秒預算（mutimedia_processor
# .TAIGI_PARALLEL_BUDGET_SECONDS），使用者不可能等。撞到 429 就放掉台語那路，這一則用
# Gemini 的華語逐字稿回答。
#
# 5xx 是廠商自建推論伺服器的暫時性錯誤（文件 7.1 節），那種退避一下重試有用。
STT_RETRY_STATUS = frozenset({500, 502, 503, 504})
# 單次實測 1.3～1.6 秒，加這一下仍在 8 秒預算內。
STT_RETRY_BACKOFF_SECONDS = 0.5


class TaigiError(RuntimeError):
    """Taigi API 沒有回預期的內容。"""


class TaigiClient:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self._api_key = (settings.TAIGI_API_KEY if api_key is None else api_key).strip()
        self._base_url = (settings.TAIGI_BASE_URL if base_url is None else base_url).rstrip("/")

    def available(self) -> bool:
        return bool(self._api_key)

    def synthesize_wav(
        self,
        text: str,
        *,
        voice_label: str = DEFAULT_VOICE_LABEL,
        speed: float = DEFAULT_SPEED,
    ) -> bytes:
        response = requests.post(
            self._base_url + TTS_PATH,
            headers={"x-api-key": self._api_key},
            json={"text": text, "voice_label": voice_label, "speed": speed, "user": ""},
            timeout=TTS_TIMEOUT_SECONDS,
        )
        content_type = response.headers.get("Content-Type", "")
        # 只有成功是 audio/wav；錯誤與「靜音對象」都回 JSON，不先分流就會把 JSON 當音檔。
        if response.status_code != 200 or not content_type.startswith("audio/"):
            raise TaigiError(
                f"Taigi TTS HTTP {response.status_code} {content_type}: {response.text[:200]}"
            )
        return response.content

    def transcribe_wav(self, wav: bytes) -> str:
        response = self._post_stt(wav)
        if response.status_code in STT_RETRY_STATUS:
            logger.warning(
                "Taigi STT HTTP %s（暫時性），退避 %s 秒重試一次",
                response.status_code,
                STT_RETRY_BACKOFF_SECONDS,
            )
            time.sleep(STT_RETRY_BACKOFF_SECONDS)
            response = self._post_stt(wav)
        if response.status_code != 200:
            raise TaigiError(f"Taigi STT HTTP {response.status_code}: {response.text[:200]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise TaigiError(f"Taigi STT 回應不是 JSON：{response.text[:200]}") from exc
        if not isinstance(payload, dict):
            raise TaigiError(f"Taigi STT 回應格式不符：{str(payload)[:200]}")
        # 無聲的音訊 best 會是 null（文件 3.2 節）。
        return (payload.get("best") or "").strip()

    def _post_stt(self, wav: bytes) -> requests.Response:
        return requests.post(
            self._base_url + STT_PATH,
            headers={"x-api-key": self._api_key},
            files={"voiceFile": ("audio.wav", wav, "audio/wav")},
            timeout=STT_TIMEOUT_SECONDS,
        )
