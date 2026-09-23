"""在聊天裡回報吃過藥了的 agent 工具：「我剛剛 12 點吃了藥」。

在這之前，確認只認推播卡片上的按鈕——使用者在訊息裡說了也不算，清單照樣顯示
「逾時未確認」（見 MedicationReportService 的模組說明）。
"""

from langchain_core.tools import tool

from app.core.request_context import get_line_user_id
from app.i18n.messages import t

MEDICATION_REPORT_TOOL_NAME = "record_medication_taken"

_medication_report_service = None


def configure_medication_report_tool(medication_report_service) -> None:
    """DI 初始化時呼叫，注入 MedicationReportService 實例。"""
    global _medication_report_service
    _medication_report_service = medication_report_service


@tool(MEDICATION_REPORT_TOOL_NAME)
async def record_medication_taken(
    slot: str = "",
    taken_time: str = "",
    person: str = "",
    relationship: str = "",
) -> str:
    """使用者說自己已經吃過某一頓藥時呼叫，把那一頓標記成已確認服用。
    例如「我剛剛吃藥了」「我 12 點吃了藥」「早上那頓我吃過了」。

    只在使用者陳述「已經吃了」時呼叫。問「我吃了沒」是查詢，要用
    `get_medication_status`；說「等一下要吃」「忘記吃了」都不算吃過，不要呼叫。

    slot：使用者指明是哪一頓時填 morning（早）、noon（中午）、evening（晚）、
    bedtime（睡前）其中之一；沒指明就留空。
    taken_time：使用者說出服藥時刻時填 24 小時制 HH:MM（「剛剛 12 點吃的」填 "12:00"）；
    沒說就留空，系統會用現在的時間。
    person／relationship：使用者是在講別人吃了藥時才填（填法同 `get_medication_status`）；
    講自己時兩個都留空。
    """
    if _medication_report_service is None:
        return t("medreport.error")
    asker_id = get_line_user_id()
    if not asker_id:
        return t("medreport.error")
    return await _medication_report_service.record_taken(
        asker_id,
        person=person,
        relationship=relationship,
        slot=slot,
        taken_time=taken_time,
    )
