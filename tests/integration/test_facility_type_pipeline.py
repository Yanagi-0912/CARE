"""
院所類型篩選的跨層整合測試：nodes → tool → service → repository query。

為什麼需要這一支：本 change 其餘所有測試都是「對著下一層的 mock」測——
nodes 測到 tool_call 的 args 為止、tool 測到 mock service 為止、service 測到
fake repository 為止。最終審查實跑復現的三個問題（正式 type 值在意圖閘門靜默
失效、牙醫診所的科別×類型雙維度、空字串 facility_type 讓核心流程壞掉）全部住在
這些 mock 遮住的接縫上：每一層單獨看都「通過」，串起來卻不會產生任何過濾條件。

因此這支測試只在最底層放假物件（fake repository），中間的 agent 決策、
LangChain 工具、MedicalService 查詢組合全部走真的程式碼，並直接斷言最終
真的送進 MongoDB 的查詢條件長什麼樣。

不需要任何外部服務（不連真實 DB、不呼叫 LLM），因此刻意不加 `integration`
標記——它必須在每次 `pytest` 都跑到，才擋得住同類的接縫問題。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.schemas import MedicalFacility
from app.services.agent.utils.nodes import AgentNodes
from app.services.medical.facility_type_matcher import FACILITY_TYPE_CATEGORIES
from app.services.medical.medical_service import MedicalService
from app.tools import medical_tools
from app.tools.registry import get_all_tools

LOCATION_TEXT = "這是我的目前位置：lat=25.0478, lng=121.517"

HOSPITAL_TYPE_QUERY = {"type": {"$in": list(FACILITY_TYPE_CATEGORIES["醫院"])}}
CLINIC_TYPE_QUERY = {"type": {"$in": list(FACILITY_TYPE_CATEGORIES["診所"])}}


class FakeMedicalFacilityRepository:
    """
    只記錄查詢條件、回傳固定院所的假 repository。

    刻意不模擬 Mongo 的過濾行為：本測試要驗證的是「查詢條件有沒有被組出來並
    送到最底層」，而不是 Mongo 自己的比對是否正確——後者是資料庫的責任，
    在這裡模擬反而會讓斷言失焦。
    """

    def __init__(self) -> None:
        self.queries: list[dict[str, Any] | None] = []

    async def find_near(
        self,
        lat: float,
        lng: float,
        radius_meters: int,
        limit: int,
        query: dict[str, Any] | None = None,
    ) -> list[MedicalFacility]:
        self.queries.append(query)
        return [
            MedicalFacility(
                id=f"id-{i}",
                name=f"測試院所{i}",
                latitude=25.0,
                longitude=121.0,
                address="測試地址",
                type="綜合醫院",
                departments=["內科", "牙科"],
                distance_meters=500 * (i + 1),
            )
            for i in range(5)
        ]


@pytest.fixture
def pipeline():
    """
    以依賴注入把 fake repository 串進整條路徑，測試結束後還原工具層的 service。

    用 configure_medical_tools()（專案既有的 DI 進入點）而不是直接改寫模組全域
    變數，行為與正式啟動流程一致。
    """
    original_service = medical_tools._medical_service
    repository = FakeMedicalFacilityRepository()
    medical_tools.configure_medical_tools(MedicalService(repository=repository))
    yield repository
    medical_tools.configure_medical_tools(original_service)


def _agent_nodes() -> AgentNodes:
    """LLM 一律不主動呼叫工具，強迫走 nodes 的強制注入路徑（即真實的失敗情境）。"""
    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(content="（模型沒有主動呼叫工具）")
    )
    return AgentNodes(llm=llm, guardrail_service=MagicMock())


async def _run_conversation(user_text: str) -> tuple[dict, str]:
    """
    跑完「使用者提出需求 → 分享位置 → agent 決策 → 實際執行工具」一輪，
    回傳 (tool_call, 工具回傳字串)。工具由真正的 registry 依名稱取得，
    確保 nodes 注入的工具名稱與 args 真的餵得進那個工具。
    """
    state = {
        "messages": [
            HumanMessage(content=user_text),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }
    result = await _agent_nodes().agent_node(state)
    tool_call = result["messages"][0].tool_calls[0]

    tools = {tool.name: tool for tool in get_all_tools(include_rag_tool=False)}
    payload = await tools[tool_call["name"]].ainvoke(tool_call["args"])
    return tool_call, payload


@pytest.mark.asyncio
async def test_canonical_type_value_reaches_repository_query(pipeline):
    """
    I1 的接縫：「附近有綜合醫院嗎」解析出的是資料庫正式 type 值「綜合醫院」。
    這一句必須一路產生 type $in 條件，而不是在意圖閘門被默默丟掉、
    退回不分類型的搜尋（也就是本 change 原本要修的那個 bug）。
    """
    tool_call, payload = await _run_conversation("附近有綜合醫院嗎")

    assert tool_call["name"] == "find_nearby_hospitals"
    assert tool_call["args"]["facility_type"] == "綜合醫院"
    assert pipeline.queries == [HOSPITAL_TYPE_QUERY]
    assert "測試院所0" in payload


@pytest.mark.asyncio
async def test_dental_clinic_reaches_repository_with_both_dimensions(pipeline):
    """
    I2／spec scenario「科別詞隱含的類型詞」：「附近有牙醫診所嗎」必須同時以
    科別（牙科）與類型（診所）過濾，最終查詢條件是兩者的 $and。
    """
    tool_call, _ = await _run_conversation("附近有牙醫診所嗎")

    assert tool_call["name"] == "find_nearby_facilities_by_department"
    assert tool_call["args"]["facility_type"] == "牙醫診所"
    assert pipeline.queries == [
        {
            "$and": [
                {"departments": {"$regex": "牙科", "$options": "i"}},
                CLINIC_TYPE_QUERY,
            ]
        }
    ]


@pytest.mark.asyncio
async def test_bare_hospital_keeps_unfiltered_query(pipeline):
    """
    向後相容的對照組：泛稱「醫院」仍不套類型過濾，查詢條件必須維持 None，
    否則 18,935 家診所會被整批排除。
    """
    tool_call, _ = await _run_conversation("附近有醫院嗎")

    assert "facility_type" not in tool_call["args"]
    assert pipeline.queries == [None]


@pytest.mark.asyncio
async def test_blank_facility_type_from_model_still_queries_database(pipeline):
    """
    I4 的接縫：模型對選填字串參數送 "" 時，工具層仍須實際查詢（query 為 None），
    而不是回「我不確定「」對應到哪一種院所類型」且完全不查 DB。
    """
    payload = await medical_tools.find_nearby_hospitals.ainvoke(
        {"lat": 25.0478, "lng": 121.517, "facility_type": ""}
    )

    assert pipeline.queries == [None]
    assert "測試院所0" in payload
