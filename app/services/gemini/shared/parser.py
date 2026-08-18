import json
from typing import Any
from app.services.gemini.shared.errors import GeminiParseError

#可能你叫 gemini 傳 json 檔下來 可能就包個 markdown code-fence 包裹的 JSON
def parse_json_from_model_text(raw_text: str) -> dict[str, Any]:
    # 將 Gemini 文字回覆轉成 JSON 物件，供 classifier/gemini service 共用解析
    # 支援純 JSON 與 markdown code-fence 包裹的 JSON
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise GeminiParseError("模型回傳的 JSON 不是物件格式")
    return parsed


def content_to_text(content: Any) -> str:
    """把 LangChain ChatModel 回傳的 `.content` 攤平成純文字。

    多個呼叫端（agent.py 摘要 tool 訊息、claim_verification/service.py 改寫
    查核理由）都直接對 Gemini 的回應呼叫 `str(content)`，但 Gemini 在部分
    情境下會回傳 list-of-parts（例如 `[{"type": "text", "text": "..."}]`）
    而非單純字串；`str(content)` 會把整個 Python list/dict 的 repr 原樣印出
    （例如「[{'type': 'text', 'text': '⋯'}]」），讓使用者看到內部資料結構
    而非文字內容。集中成一個共用函式，讓兩處呼叫端不必各自重寫、也不會
    有一處拆對、一處忘記拆的落差。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)
