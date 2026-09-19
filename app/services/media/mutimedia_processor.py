from pathlib import Path
from typing import Any, Optional
from datetime import datetime
import mimetypes
from app.core.config import settings
from app.core.request_logging import log_stage, stage_timer
from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    get_request_language,
    set_detected_speech_language,
)
from app.services.speech import audio
from app.services.speech.gemini_stt import GEMINI_STT_TIMEOUT_SECONDS, GeminiTranscriber
from app.services.speech.speech_language import choose_transcript
from app.services.speech.taigi_client import TaigiClient

import asyncio
import logging
import secrets
import requests

logger = logging.getLogger(__name__)

# 媒體解析 webhook URL
MEDIA_PARSE_WEBHOOK_URL = settings.MEDIA_PARSE_WEBHOOK_URL
WEBHOOK_TIMEOUT_SECONDS = 120
LINE_MESSAGE_CONTENT_API = (
    "https://api-data.line.me/v2/bot/message/{message_id}/content"
)

# 媒體暫存目錄：所有下載檔案都先落在這裡，並在 finally 進行清理
TMP_DIR = Path("app_data") / "tmp"

# 單檔下載大小上限10MB
MAX_MEDIA_SIZE_BYTES = 10 * 1024 * 1024

# 允許處理的媒體類型白名單，其他一律拒絕
ALLOWED_MEDIA_TYPES = {"image", "video", "audio", "file"}

# 各媒體類型對應的 MIME 前綴，用於驗證下載來源回傳內容是否合理
EXPECTED_MIME_PREFIXES = {
    "image": ("image/",),
    "video": ("video/",),
    "audio": ("audio/",),
    "file": ("application/", "text/"),
}
# URL 未提供副檔名時的保底對應值
MEDIA_EXTENSIONS = {
    "image": ".jpg",
    "video": ".mp4",
    "audio": ".m4a",
    "file": ".bin",
}

# 長錄音切成好幾段時，同時送台語 STT 的段數上限。廠商沒寫速率限制，只說太密會回
# 429，所以不一次全丟；一般的 LINE 語音在 25 秒內，只有一段。
TAIGI_STT_CONCURRENCY = 3

# 平行辨識時台語那路的總預算。兩條路是一起 await 的，慢的那條決定使用者要等多久，
# 而台語 STT 自己的逾時是「每一段」15 秒（taigi_client.STT_TIMEOUT_SECONDS）、長錄音
# 還會分批，不設總預算的話最壞情況會比現在只走 Gemini 還久。對齊 Gemini 的 8 秒
# （gemini_stt.GEMINI_STT_TIMEOUT_SECONDS），超過就當台語那路沒回，用 Gemini 那份。
TAIGI_PARALLEL_BUDGET_SECONDS = GEMINI_STT_TIMEOUT_SECONDS

# 語音裡沒有聽到內容時，跟 webhook 沒抽到字時回同一句。
NO_CONTENT_TEXT = "Unable to extract text from media file (no content extracted)"


class MediaProcessingError(Exception):
    """媒體辨識失敗的基底。子類別區分「使用者能自己改的」與「服務的問題」——

    以前所有失敗都回同一個字串「發生錯誤」，handler 再把它跟「沒辨識出文字」
    混成一句「請確認內容清晰並重新傳送」：檔案太大的人重拍一張一樣太大，
    n8n 掛掉的人重傳十次也一樣，而且完全看不出來是服務出了問題。
    """


class MediaTooLargeError(MediaProcessingError, ValueError):
    """超過 MAX_MEDIA_SIZE_BYTES。使用者壓縮或裁切就能解決。"""

    def __init__(self, size_bytes: int, limit_bytes: int = MAX_MEDIA_SIZE_BYTES):
        super().__init__(f"Media too large: {size_bytes} bytes > {limit_bytes} bytes")
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class MediaUnsupportedError(MediaProcessingError, ValueError):
    """類型不在白名單，或 LINE 回的 MIME 跟宣稱的類型對不上。"""


class MediaServiceUnavailableError(MediaProcessingError):
    """下載、n8n、STT 等外部服務失敗或逾時。使用者只能等一下再傳。"""


class MediaProcessorService:
    """Handle incoming LINE text and send replies based on Gemini tool output."""

    def __init__(
        self,
        taigi_client: Optional[TaigiClient] = None,
        transcriber: Optional[GeminiTranscriber] = None,
    ):
        self._taigi_client = taigi_client if taigi_client is not None else TaigiClient()
        self._transcriber = transcriber if transcriber is not None else GeminiTranscriber()
        logger.info("MediaProcessorService initialized")

    async def process_media(
        self,
        media_message_id: str,
        user_media_type: str,
        source_file_name: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        temp_file_path = None
        try:
            logger.info(f"Processing {user_media_type} message from user {user_id}...")
            # 兩步都以 asyncio.to_thread 移出事件迴圈。底下的 helper 用的是同步的
            # requests，直接 await 這個 async 函式會讓單執行緒的事件迴圈整個停住：
            # 下載最久 20 秒、webhook 最久 WEBHOOK_TIMEOUT_SECONDS（120 秒），
            # 期間所有 LINE 訊息、LIFF API 與背景排程都無法推進，連 /health 都回
            # 不了——readinessProbe 連續失敗後 pod 會被移出 Service，等於整個後端
            # 暫時下線。
            #
            # 用 to_thread 而不是改寫成 httpx：本專案已在 tts_service.py:110／:193
            # 以同一手法處理同一類問題，沿用既有做法可以讓 helper 維持同步、
            # 既有單元測試不受影響，改動面也只有這裡。
            temp_file_path = await asyncio.to_thread(
                self._download_media_to_tmp,
                media_message_id,
                user_media_type,
                source_file_name=source_file_name,
            )
            # 語音：說中文的使用者同時送台語 STT 與 Gemini，回來再選一份（見
            # _transcribe_zh_or_taiwanese）；其他語言只走 Gemini。兩邊都不行才送
            # n8n／faster-whisper。圖片、影片、文件照舊走 n8n。
            user_text = None
            is_audio = user_media_type.lower().strip() == "audio"
            if is_audio and get_request_language() == DEFAULT_USER_LANGUAGE:
                user_text = await self._transcribe_zh_or_taiwanese(temp_file_path)
            elif is_audio:
                user_text = await self._transcribe_with_gemini_or_none(temp_file_path)
            if user_text is None:
                user_text = await asyncio.to_thread(
                    self._extract_user_text_via_webhook, temp_file_path
                )
            # TODO: 清洗user_text，移除不必要的空白或控制字元，確保回覆格式整潔。
            logger.info(f"Successfully processed and replied to user {user_id}")
            return user_text

        except MediaProcessingError:
            # 已分類的失敗原樣往上丟，由 media_handler 換成對應的說明。
            raise
        except Exception as e:
            # 沒預期到的錯誤（ffmpeg 解碼、暫存目錄寫不進去…）對使用者來說
            # 都是「服務暫時有問題」，不是他傳的東西有問題。
            logger.error(f"Error in process_media: {e}", exc_info=True)
            raise MediaServiceUnavailableError(f"unexpected failure: {e}") from e
        finally:
            # 不論成功或失敗都嘗試清理，避免暫存檔堆積。
            if temp_file_path:
                self._cleanup_temp_file(temp_file_path)

    async def _transcribe_zh_or_taiwanese(self, file_path: Path) -> Optional[str]:
        """台語 STT 與 Gemini 同時聽，回來選一份，並記下聽出來的語言。

        使用者不必先到設定頁把語言切成台語：講什麼就用什麼辨識，回覆也用同一種念。
        兩條路平行送，等待時間是兩者取大的那個，不是相加：2026-09-19 跑 48 段測試
        音檔，這個函式從頭到尾的中位數 1.59 秒（最長 4.26 秒），跟先前只走 Gemini
        的 1.4～2.0 秒（2026-09-15 實測）差不多。

        兩邊都失敗回 None，由呼叫端退回 n8n／faster-whisper。
        """
        taigi_task = (
            self._taigi_within_budget(file_path)
            if self._taigi_client.available()
            else None
        )
        gemini_task = self._transcribe_with_gemini_or_none(file_path)
        if taigi_task is None:
            # 沒設 TAIGI_API_KEY：只有華語那路，語言就是華語。
            text = await gemini_task
            set_detected_speech_language(DEFAULT_USER_LANGUAGE)
            return text
        taigi_text, gemini_text = await asyncio.gather(taigi_task, gemini_task)
        # 「聽不到內容」的哨兵不是逐字稿，不能拿去判語言，但要留著分辨「靜音」與
        # 「那一路失敗了」——前者該回哨兵讓使用者知道沒聽到，後者才退 n8n。
        taigi_heard = None if taigi_text in (None, NO_CONTENT_TEXT) else taigi_text
        gemini_heard = None if gemini_text in (None, NO_CONTENT_TEXT) else gemini_text
        chosen, language = choose_transcript(taigi_heard, gemini_heard)
        set_detected_speech_language(language)
        log_stage(
            logger,
            "stt_lang",
            lang=language,
            taigi_chars=len(taigi_heard or ""),
            gemini_chars=len(gemini_heard or ""),
        )
        if chosen is not None:
            return chosen
        if NO_CONTENT_TEXT in (taigi_text, gemini_text):
            return NO_CONTENT_TEXT
        return None

    async def _taigi_within_budget(self, file_path: Path) -> Optional[str]:
        """台語那路超過 TAIGI_PARALLEL_BUDGET_SECONDS 就放掉，理由見該常數。"""
        try:
            return await asyncio.wait_for(
                self._transcribe_taiwanese_or_none(file_path),
                timeout=TAIGI_PARALLEL_BUDGET_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning("台語 STT 超過 %s 秒，改用華語那份", TAIGI_PARALLEL_BUDGET_SECONDS)
            return None

    async def _transcribe_taiwanese_or_none(self, file_path: Path) -> Optional[str]:
        """Taigi 台語 STT。說中文的使用者一律跑這一路（與 Gemini 平行），由
        `_transcribe_zh_or_taiwanese` 決定要不要採用它的結果。

        失敗回 None：只剩 Gemini 那份，再不行才 n8n／faster-whisper（此時送的
        語言提示是文字語言 zh-TW，兩者都不認得台語）。
        """
        if not self._taigi_client.available():
            logger.warning("TAIGI_API_KEY 未設定，台語語音改走一般辨識")
            return None
        try:
            with stage_timer(logger, "taigi_stt", chunks=0, ok="False") as t_stt:
                chunks, rate = await asyncio.to_thread(self._decode_and_split, file_path)
                t_stt["chunks"] = len(chunks)
                semaphore = asyncio.Semaphore(TAIGI_STT_CONCURRENCY)

                async def _transcribe(chunk: bytes) -> str:
                    async with semaphore:
                        return await asyncio.to_thread(
                            self._taigi_client.transcribe_wav, audio.pcm16_to_wav(chunk, rate)
                        )

                parts = await asyncio.gather(*(_transcribe(c) for c in chunks))
                t_stt["ok"] = "True"
        except Exception:
            logger.warning("台語 STT 失敗，改走一般辨識", exc_info=True)
            return None
        text = " ".join(p for p in parts if p)
        return text or NO_CONTENT_TEXT

    async def _transcribe_with_gemini_or_none(self, file_path: Path) -> Optional[str]:
        """語音交給 Gemini 聽寫（理由與實測見 app/services/speech/gemini_stt.py）。

        語言提示用文字語言：台語使用者退到這裡時是 zh-TW。失敗或逾時回 None，
        由呼叫端改走 n8n／faster-whisper。
        """
        if not self._transcriber.available():
            logger.warning("GEMINI_API_KEY 未設定，語音改走 faster-whisper")
            return None
        try:
            with stage_timer(logger, "gemini_stt", ok="False") as t_stt:
                text = await self._transcriber.transcribe(file_path, get_request_language())
                t_stt["ok"] = "True"
        except Exception:
            logger.warning("Gemini 語音辨識失敗，改走 faster-whisper", exc_info=True)
            return None
        return text or NO_CONTENT_TEXT

    @staticmethod
    def _decode_and_split(file_path: Path) -> tuple[list[bytes], int]:
        # LINE 錄音是 m4a，台語 STT 不收（見 app/services/speech/audio.py）；
        # 過長的錄音後段會亂掉，要先在停頓處切段。
        pcm, rate = audio.decode_to_pcm16_mono(file_path, audio.STT_SAMPLE_RATE)
        return audio.split_on_pauses(pcm, rate), rate

    def _download_media_to_tmp(
        self,
        media_message_id: str,
        media_type: str,
        source_file_name: Optional[str] = None,
    ) -> Path:
        # 媒體類型白名單過濾，阻擋未知類型。
        normalized_type = media_type.lower().strip()
        if normalized_type not in ALLOWED_MEDIA_TYPES:
            raise MediaUnsupportedError(f"Unsupported media type: {media_type}")

        if not media_message_id:
            raise ValueError("Missing media message id")

        extension = Path(source_file_name).suffix.lower() if source_file_name else ""
        if not extension:
            extension = MEDIA_EXTENSIONS.get(normalized_type, ".bin")

        # 產生可追蹤且低碰撞風險的檔名：類型_時間戳_隨機碼。
        safe_type = "".join(ch for ch in normalized_type if ch.isalnum()) or "media"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = secrets.token_hex(4)

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        target = TMP_DIR / f"{safe_type}_{timestamp}_{random_suffix}{extension}"

        from app.dependencies import get_line_token_manager

        access_token = get_line_token_manager().get_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        content_url = LINE_MESSAGE_CONTENT_API.format(message_id=media_message_id)

        try:
            response_cm = requests.get(
                content_url, headers=headers, timeout=20, stream=True
            )
        except requests.RequestException as exc:
            # LINE 內容伺服器連不上或逾時：不是使用者的問題。
            raise MediaServiceUnavailableError(
                f"Failed to download media from LINE: {exc}"
            ) from exc

        with response_cm as response:
            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                raise MediaServiceUnavailableError(
                    f"LINE content API returned {response.status_code}"
                ) from exc

            content_length_header = response.headers.get("Content-Length")
            if content_length_header:
                try:
                    content_length = int(content_length_header)
                except ValueError as exc:
                    raise ValueError("Invalid Content-Length header") from exc
                if content_length > MAX_MEDIA_SIZE_BYTES:
                    raise MediaTooLargeError(content_length)

            # 驗證回應 MIME 與宣稱 media_type 大致一致，降低內容偽裝風險。
            content_type = (
                response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            )
            expected_prefixes = EXPECTED_MIME_PREFIXES[normalized_type]
            if content_type and not any(
                content_type.startswith(prefix) for prefix in expected_prefixes
            ):
                raise MediaUnsupportedError(
                    f"Unexpected content type '{content_type}' for media type '{normalized_type}'"
                )

            if extension == ".bin":
                guessed_extension = mimetypes.guess_extension(content_type)
                if guessed_extension:
                    extension = guessed_extension.lower()
                    target = target.with_suffix(extension)

            downloaded_size = 0
            with target.open("wb") as temp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    downloaded_size += len(chunk)
                    # 下載中仍要檢查，防止 Content-Length 缺失或不可信。
                    if downloaded_size > MAX_MEDIA_SIZE_BYTES:
                        raise MediaTooLargeError(downloaded_size)
                    temp_file.write(chunk)

        logger.info(f"Downloaded media content from LINE API to {target}")
        return target

    def _extract_user_text_via_webhook(self, file_path: Path) -> str:
        """Upload file via form-data (key=file) and return parsed text from webhook."""
        if not MEDIA_PARSE_WEBHOOK_URL:
            logger.warning(
                "MEDIA_PARSE_WEBHOOK_URL is empty; using placeholder extracted text"
            )
            return "Test text extracted from media"

        mime_type = (
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )

        try:
            with file_path.open("rb") as media_file:
                files = {
                    "file": (
                        file_path.name,
                        media_file,
                        mime_type,
                    )
                }
                response = requests.post(
                    MEDIA_PARSE_WEBHOOK_URL,
                    files=files,
                    # 使用者設定的語言（zh-TW、id…）交給 faster-whisper 當語言提示；n8n 會把
                    # 這個欄位轉給 local-asr，由那邊換成 whisper 的語言碼。沒有提示時 small
                    # 模型會把真實的 LINE 語音判成緬甸語、日文而轉出亂碼，亂碼再觸發重解碼，
                    # 2026-09-14 一則 9.7 秒的語音因此轉了 133 秒、撞上下面的逾時。
                    # 圖片與文件也會帶著這個欄位，n8n 那兩條分支不讀它。
                    # 送的是文字語言：選台語的使用者在台語 STT 失敗時退到這裡，whisper
                    # 不認得台語，給 zh-TW。
                    data={"language": get_request_language()},
                    timeout=WEBHOOK_TIMEOUT_SECONDS,
                )

            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Webhook request failed: {e}")
            raise MediaServiceUnavailableError(f"Failed to reach webhook: {e}") from e

        content_type = response.headers.get("Content-Type", "").lower()
        response_text = response.text.strip()

        logger.debug(
            f"Webhook response: status={response.status_code}, "
            f"content_type={content_type}, text_length={len(response_text)}"
        )

        parsed_text = ""
        if not response_text:
            # n8n 的 workflow 跑了但什麼都沒回：是 workflow 壞了，不是圖片沒字。
            logger.warning("Webhook returned empty response body")
            raise MediaServiceUnavailableError("empty webhook response")

        if "application/json" in content_type:
            try:
                payload: Any = response.json()
                if isinstance(payload, dict):
                    parsed_text = str(
                        payload.get("user_text")
                        or payload.get("text")
                        or payload.get("result")
                        or ""
                    ).strip()
                elif isinstance(payload, str):
                    parsed_text = payload.strip()
                else:
                    logger.warning(f"Unexpected JSON payload type: {type(payload)}")
            except requests.exceptions.JSONDecodeError as e:
                logger.error(
                    f"Failed to parse webhook JSON response: {e}, text={response_text[:200]}"
                )
                raise MediaServiceUnavailableError("invalid JSON from webhook") from e
        else:
            parsed_text = response_text

        if not parsed_text:
            logger.warning("Webhook returned no extractable text content")
            return NO_CONTENT_TEXT

        return parsed_text

    def _cleanup_temp_file(self, file_path: Path) -> None:
        """Best-effort temp file cleanup; never raise to main flow."""
        try:
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Cleaned up temp file: {file_path}")
        except Exception as e:
            # 清理失敗不影響主流程，只記錄警告供後續排查。
            logger.warning(f"Failed to cleanup temp file {file_path}: {e}")


media_processor_service = MediaProcessorService()
