"""查服藥狀況的 agent 工具：「我今天要吃什麼藥」「媽媽昨天有沒有吃藥」。

工具只把發問者與模型抽出的參數交給 MedicationStatusService；回覆由 agent.py 的
medication_direct 原樣送出，不再經過模型。
"""

from langchain_core.tools import tool

from app.core.request_context import get_line_user_id
from app.i18n.messages import t

MEDICATION_STATUS_TOOL_NAME = "get_medication_status"

_medication_status_service = None


def configure_medication_status_tool(medication_status_service) -> None:
    """DI 初始化時呼叫，注入 MedicationStatusService 實例。"""
    global _medication_status_service
    _medication_status_service = medication_status_service


@tool(MEDICATION_STATUS_TOOL_NAME)
async def get_medication_status(
    person: str = "",
    relationship: str = "",
    days_ago: int = 0,
    last_n_days: int = 0,
) -> str:
    """查使用者本人或家人的用藥安排與服藥紀錄：今天要吃哪些藥、有沒有按下已服用、最近幾天有沒有沒確認的時段。

    person：使用者用來指稱對象的原話，例如「媽媽」「王美玲」「阿嬤」；問自己時留空。
    relationship：person 是親屬稱謂時，換成 parent（父母）、child（子女）、spouse（配偶）、
    sibling（兄弟姊妹）、grandparent（祖父母）、grandchild（孫子女）其中之一；是名字或問自己時留空。
    days_ago：只問某一天時填幾天前，今天 0、昨天 1、前天 2。
    last_n_days：問一段期間時填天數，例如「這禮拜」「最近幾天」填 7；只問某一天時留 0。
    """
    if _medication_status_service is None:
        return t("medstatus.error")
    asker_id = get_line_user_id()
    if not asker_id:
        return t("medstatus.error")
    return await _medication_status_service.describe(
        asker_id,
        person=person,
        relationship=relationship,
        days_ago=days_ago,
        last_n_days=last_n_days,
    )
