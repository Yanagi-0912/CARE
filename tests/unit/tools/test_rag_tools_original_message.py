"""get_rag_answer 要把本輪使用者原文交給 RAG 服務（複合問題拆題看的是原句）。"""

import pytest

import app.tools.rag_tools as rag_tools
from app.core.user_message import reset_request_user_message, set_request_user_message


class _RecordingRagService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def answer(self, query: str, *, original_message: str | None = None) -> str:
        self.calls.append((query, original_message))
        return "知識庫回覆"


@pytest.fixture
def rag_service():
    service = _RecordingRagService()
    previous = rag_tools._rag_answer_service
    rag_tools.configure_rag_tool(service)
    yield service
    rag_tools.configure_rag_tool(previous)


async def test_passes_request_user_message_as_original_message(rag_service):
    token = set_request_user_message("魚油跟魚肝油有什麼差別？吃降血脂藥要注意什麼？")
    try:
        out = await rag_tools.get_rag_answer.ainvoke({"query": "魚油 魚肝油 差別"})
    finally:
        reset_request_user_message(token)

    assert out == "知識庫回覆"
    assert rag_service.calls == [
        ("魚油 魚肝油 差別", "魚油跟魚肝油有什麼差別？吃降血脂藥要注意什麼？")
    ]


async def test_original_message_is_none_outside_agent_request(rag_service):
    await rag_tools.get_rag_answer.ainvoke({"query": "高血壓飲食"})
    assert rag_service.calls == [("高血壓飲食", None)]
