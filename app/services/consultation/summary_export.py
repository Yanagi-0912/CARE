# 把諮詢摘要排成純文字檔，給 LIFF「下載所有摘要」用。
# 使用者多半是長輩、用手機，JSON 檔幾乎沒辦法讀，所以輸出成排好版的 txt。
from __future__ import annotations

import json
from datetime import datetime

from app.i18n.messages import t
from app.models.consultation import ConsultationSummary
from app.services.gemini.shared.errors import GeminiParseError
from app.services.gemini.shared.parser import parse_json_from_model_text


def _format_value(value: object) -> str:
    # 陣列比照 LIFF 卡片一行一項，不用「、」——英文、泰文摘要接頓號很怪
    if isinstance(value, list):
        return "\n".join(f"- {str(item).strip()}" for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if value is None:
        return ""
    return str(value).strip()


def _parse_sections(raw: str) -> list[tuple[str, str]] | None:
    # 解析規則與 LIFF 的 toSummarySections 一致；「無」照樣保留——這個佔位詞
    # 隨摘要語言而變，要過濾就得維護各語言的對照表。
    try:
        data = parse_json_from_model_text(raw)
    except (ValueError, GeminiParseError):
        return None
    sections = [(str(key), _format_value(value)) for key, value in data.items()]
    return [(key, value) for key, value in sections if value]


def render_summaries_txt(
    summaries: list[ConsultationSummary], language: str, exported_at: datetime
) -> str:
    # 欄位名稱直接用摘要 JSON 的 key：Gemini 已依摘要語言翻好，不另外翻譯。
    # 欄位列不加冒號，因為每筆摘要的語言可能不同，全形半形冒號無法統一。
    lines = [
        t("consultation_export.title", language),
        f"{t('consultation_export.exported_at', language)}{exported_at:%Y-%m-%d %H:%M}",
        "",
    ]
    if not summaries:
        lines.append(t("consultation_export.empty", language))

    for summary in summaries:
        lines += [f"==== {summary.summary_date.isoformat()} ====", ""]
        sections = _parse_sections(summary.summary)
        if sections is None:
            # 不是 JSON（例如「該日期尚無諮詢記錄。」）就整段照印，不丟例外
            lines += [summary.summary.strip(), ""]
            continue
        for key, value in sections:
            lines += [f"■ {key}", value, ""]

    return "\n".join(lines).rstrip() + "\n"
