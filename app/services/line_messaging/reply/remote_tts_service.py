"""把語音合成交給獨立的 care-tts（app/tts_main.py）。

音檔原本存在 backend 容器裡，部署換 pod 就全丟：2026-09-15 10:55 送出的語音，10:56
部署後 11:00 按播放，LINE 來拿檔得到 404。backend 以後開多份，A 產的檔被分到 B 去拿
也一樣 404。所以正式環境由 care-tts 合成、把檔案存在 PVC、提供 /tts 給 LINE 下載，
所有 backend 都呼叫它。

介面與 TTSService.synthesize 相同，LineReplier 分不出是哪一種：回傳的第二個值是公開
網址，reply._resolve_audio_url 原樣使用。呼叫失敗就拋例外，由
reply._push_tts_audio 接住、放棄語音（文字已經送出），跟本地合成失敗一樣。
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional, Tuple

import requests

from app.core.request_context import get_request_id

__all__ = ["RemoteTTSService", "REQUEST_ID_HEADER", "SYNTHESIZE_PATH"]

SYNTHESIZE_PATH = "/internal/synthesize"
# care-tts 拿這個值當自己 log 的 rid：同一則回覆在兩個 pod 的 log 用同一個 rid 串得起來。
REQUEST_ID_HEADER = "X-Request-ID"

# 連線逾時：叢集內連線正常是毫秒級。care-tts 沒起來時要快點放棄、改回純文字，不要讓
# 使用者陪著等。
CONNECT_TIMEOUT_SECONDS = 3
# 讀取逾時：不能比搬出去之前更早放棄。care-tts 裡每一段都有逾時的最長路徑是台語改寫
# 10 秒（taigi_text.TAIGI_TEXT_TIMEOUT_SECONDS）＋ Taigi TTS 20 秒
# （taigi_client.TTS_TIMEOUT_SECONDS）＋轉 mp3，失敗後改念國語，edge-tts 連線 5 秒＋
# 接收 15 秒（tts_service.EDGE_TTS_*），合計約 55 秒。gTTS 備援沒有逾時，所以這也是
# 整段合成的上限。這是放棄的時間，不是平常的等待：2026-09-14 正式環境第一則台語語音，
# 語音階段 22.1 秒。
READ_TIMEOUT_SECONDS = 60


class RemoteTTSService:
    """呼叫 care-tts 合成；音檔留在 care-tts，backend 只拿網址與長度。"""

    def __init__(
        self,
        base_url: str,
        *,
        post: Callable[..., requests.Response] = requests.post,
    ) -> None:
        self._url = base_url.strip().rstrip("/") + SYNTHESIZE_PATH
        self._post = post

    def available(self) -> bool:
        return True

    async def synthesize(
        self,
        text: str,
        language: str = "zh-TW",
        voice_rate: str = "normal",
        voice_gender: str = "female",
    ) -> Tuple[bytes, str, Optional[int]]:
        """回傳 (b"", 公開網址, duration_ms)，與 TTSService.synthesize 同形。"""
        # 同步 requests 放進 to_thread，理由同 tts_service 呼叫 n8n：不能卡住事件迴圈
        # （voice-reply 規格：合成期間仍可處理其他 webhook）。
        response = await asyncio.to_thread(
            self._post,
            self._url,
            json={
                "text": text,
                "language": language,
                "voice_rate": voice_rate,
                "voice_gender": voice_gender,
            },
            headers={REQUEST_ID_HEADER: get_request_id()},
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
        )
        response.raise_for_status()

        data = response.json()
        audio_url = data.get("audio_url")
        if not isinstance(audio_url, str) or not audio_url.startswith(
            ("https://", "http" + "://")
        ):
            raise RuntimeError("care-tts response missing audio_url")
        duration_ms = data.get("duration_ms")
        return b"", audio_url, int(duration_ms) if duration_ms is not None else None
