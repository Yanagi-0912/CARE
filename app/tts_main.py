"""care-tts：獨立的語音合成服務（CARE-infra 的 care-tts Deployment）。

合成 LINE 語音回覆、把 mp3 存在 PVC（掛在 app_data/tmp）、提供 /tts 給 LINE 下載。
backend 設了 TTS_SERVICE_URL 就把合成交給這裡（remote_tts_service.RemoteTTSService）。

為什麼拆出來：音檔原本存在 backend 容器裡，部署換 pod 就全丟——2026-09-15 10:55 送出的
語音，10:56 部署後 11:00 按播放，LINE 來拿檔得到 404，畫面顯示「語音訊息的保存期限
已過」。backend 以後開多份，A 產的檔被分到 B 去拿也一樣 404。

刻意不 import app.main／app.dependencies：那邊一載入就建藥證庫與 n-gram 索引，
care-scheduler 用同一個 image 閒置就 467Mi（2026-09-14 kubectl top）。這裡只載入語音
相關的模組，2026-09-15 在 macOS 量 import 完 120 MiB。

/internal/synthesize 只給叢集內的 backend 呼叫：ingress 只把 /tts 導過來，這條路徑
從外面打不到。與 local-asr、local-parser 一樣不另外驗證。
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from app.core.logging_setup import configure_logging
from app.core.request_context import reset_request_id, set_request_id
from app.routers.tts.tts import router as tts_router
from app.services.line_messaging.reply.remote_tts_service import (
    REQUEST_ID_HEADER,
    SYNTHESIZE_PATH,
)
from app.services.line_messaging.reply.tts_service import (
    DEFAULT_VOICE_GENDER,
    build_local_tts_service,
    public_audio_url,
)

configure_logging()

app = FastAPI(title="CARE TTS", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(tts_router, prefix="/tts")

_tts_service = build_local_tts_service()


class SynthesizeRequest(BaseModel):
    text: str
    language: str = "zh-TW"
    voice_rate: str = "normal"
    voice_gender: str = DEFAULT_VOICE_GENDER


class SynthesizeResponse(BaseModel):
    audio_url: str
    duration_ms: Optional[int] = None


@app.post(SYNTHESIZE_PATH, response_model=SynthesizeResponse)
async def synthesize(body: SynthesizeRequest, request: Request) -> SynthesizeResponse:
    rid_token = set_request_id(request.headers.get(REQUEST_ID_HEADER) or "-")
    try:
        try:
            _, output, duration_ms = await _tts_service.synthesize(
                body.text,
                language=body.language,
                voice_rate=body.voice_rate,
                voice_gender=body.voice_gender,
            )
        except Exception:
            # TTSService.synthesize 已經 logger.exception 過，這裡只回狀態碼。
            raise HTTPException(status_code=502, detail="TTS synthesis failed")

        audio_url = public_audio_url(output)
        if audio_url is None:
            # PUBLIC_BASE_URL 沒設或檔案不在；public_audio_url 已記 warning。
            raise HTTPException(status_code=503, detail="audio URL unavailable")
        return SynthesizeResponse(audio_url=audio_url, duration_ms=duration_ms)
    finally:
        reset_request_id(rid_token)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
