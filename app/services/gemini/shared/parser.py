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
