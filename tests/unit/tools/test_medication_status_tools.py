"""查服藥狀況的 agent 工具：把發問者與模型抽出的參數交給服務，其餘不做。"""

import pytest

from app.core.request_context import reset_line_user_id, set_line_user_id
from app.i18n.messages import t
from app.tools import medication_status_tools
from app.tools.medication_status_tools import (
    configure_medication_status_tool,
    get_medication_status,
)


class RecordingService:
    def __init__(self):
        self.calls = []

    async def describe(self, asker_id, **kwargs):
        self.calls.append((asker_id, kwargs))
        return "回覆"


@pytest.fixture(autouse=True)
def reset_service():
    # 還原成測試前的值而不是 None：dependencies 在 import 時注入的正式服務要留著，
    # test_dependencies 會檢查那條接線。
    previous = medication_status_tools._medication_status_service
    configure_medication_status_tool(None)
    yield
    configure_medication_status_tool(previous)


@pytest.fixture()
def asker():
    token = set_line_user_id("U_ASKER")
    yield "U_ASKER"
    reset_line_user_id(token)


def test_tool_name_matches_what_the_prompt_and_router_expect():
    assert get_medication_status.name == "get_medication_status"


async def test_passes_the_asker_and_the_model_arguments(asker):
    service = RecordingService()
    configure_medication_status_tool(service)

    out = await get_medication_status.ainvoke(
        {"person": "媽媽", "relationship": "parent", "days_ago": 1}
    )

    assert out == "回覆"
    assert service.calls == [
        (
            "U_ASKER",
            {"person": "媽媽", "relationship": "parent", "days_ago": 1, "last_n_days": 0},
        )
    ]


async def test_no_arguments_means_the_asker_today(asker):
    service = RecordingService()
    configure_medication_status_tool(service)

    await get_medication_status.ainvoke({})

    assert service.calls == [
        ("U_ASKER", {"person": "", "relationship": "", "days_ago": 0, "last_n_days": 0})
    ]


async def test_unconfigured_service_returns_the_fixed_error(asker):
    assert await get_medication_status.ainvoke({}) == t("medstatus.error")


async def test_unknown_asker_returns_the_fixed_error():
    configure_medication_status_tool(RecordingService())
    assert await get_medication_status.ainvoke({}) == t("medstatus.error")
