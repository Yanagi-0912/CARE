"""拿使用者自己的藥單回答問題的 agent 工具。

`get_medication_status` 只把清單原樣送出，`get_rag_answer` 看不到使用者在吃
什麼——這個工具是兩者中間那一格（見 MedicationQuestionService 的模組說明）。
回覆由 agent.py 的 `medication_question_direct` 原樣送出：藥名與時間是程式從
資料庫算的，藥理那段已經過 RAG 生成，交回模型只是再改寫一次成品。
"""

from langchain_core.tools import tool

from app.core.request_context import get_line_user_id
from app.i18n.messages import t

MEDICATION_QUESTION_TOOL_NAME = "ask_about_my_medications"

_medication_question_service = None


def configure_medication_question_tool(medication_question_service) -> None:
    """DI 初始化時呼叫，注入 MedicationQuestionService 實例。"""
    global _medication_question_service
    _medication_question_service = medication_question_service


@tool(MEDICATION_QUESTION_TOOL_NAME)
async def ask_about_my_medications(
    question: str,
    person: str = "",
    relationship: str = "",
) -> str:
    """回答與「使用者自己正在吃的那些藥」有關的問題：這樣吃可不可以、和某樣食物或
    另一種藥會不會衝突、吃藥和吃飯的先後、這一頓晚了或漏了怎麼辦、兩頓之間該隔多久。

    只要問題牽涉到「我的藥」「我在吃的藥」「我剛剛吃的那頓」就用這個工具，不要用
    `get_rag_answer`——它看不到使用者登記的藥。純粹問「我今天要吃什麼藥」「吃了沒」
    請改用 `get_medication_status`。

    question：使用者問題的原話，包含他提到的時間與食物（例如「我 11 點喝了牛奶、
    12 點吃藥，等等要吃午餐，這樣可以嗎」）；不要改寫成醫學名詞，也不要自己補上藥名。
    person：使用者用來指稱對象的原話，例如「媽媽」「王美玲」；問自己時留空。
    relationship：person 是親屬稱謂時，換成 parent（父母）、child（子女）、spouse（配偶）、
    sibling（兄弟姊妹）、grandparent（祖父母）、grandchild（孫子女）其中之一；是名字或問自己時留空。
    """
    if _medication_question_service is None:
        return t("medstatus.error")
    asker_id = get_line_user_id()
    if not asker_id:
        return t("medstatus.error")
    return await _medication_question_service.answer(
        asker_id,
        question,
        person=person,
        relationship=relationship,
    )
