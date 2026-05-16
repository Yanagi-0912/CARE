from __future__ import annotations


class GeminiError(Exception):
    """所有 Gemini 相關錯誤的基底類別。"""


class GeminiNetworkError(GeminiError):
    """網路層錯誤（連線失敗、逾時等）。"""


class GeminiHttpError(GeminiError):
    """HTTP 狀態碼錯誤（如 429、400）；額外帶 `status_code`。"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class GeminiSchemaError(GeminiError):
    """模型回應格式不符預期（缺欄位、非預期型別等）。"""


class GeminiParseError(GeminiError):
    """模型輸出無法解析（如 JSON 解析失敗）。"""


class GeminiUnknownError(GeminiError):
    """無法歸類的其他 Gemini 錯誤。"""


def raise_mapped_gemini_error(exc: BaseException) -> None:
    """將 LangChain／Google API／httpx 例外對應為專案內 Gemini* 例外並 raise。

    舊：`GeminiClient` 自己針對 httpx 例外 / KeyError 做 mapping。
    新：改由 LangChain 觸發底層例外後，集中在這裡 map 成 application 認識的型別。
    """
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        raise GeminiNetworkError("請求超時，請檢查網路連線") from exc
    if isinstance(exc, httpx.NetworkError):
        raise GeminiNetworkError("無法連線到 AI 服務，請檢查網路連線") from exc
    try:
        from google.api_core import exceptions as google_exc
    except ImportError:
        google_exc = None  # type: ignore[assignment]
    if google_exc is not None:
        if isinstance(exc, google_exc.ResourceExhausted):
            raise GeminiHttpError(429, "AI 服務發生錯誤（狀態碼: 429）") from exc
        if isinstance(exc, google_exc.InvalidArgument):
            raise GeminiHttpError(400, "AI 服務請求參數錯誤") from exc
    low = str(exc).lower()
    if "429" in low or "resource exhausted" in low or "quota" in low:
        raise GeminiHttpError(429, "AI 服務發生錯誤（狀態碼: 429）") from exc
    raise GeminiUnknownError(f"AI 服務發生未預期錯誤。詳情：{exc}") from exc
