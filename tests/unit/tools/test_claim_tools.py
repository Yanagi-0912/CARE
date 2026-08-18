from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.rag.claim_verification.service import VerificationResult
from app.tools.claim_tools import configure_claim_tool, verify_claim


@pytest.fixture(autouse=True)
def reset_tool_state():
    configure_claim_tool(None)
    yield
    configure_claim_tool(None)


def _fake_service(result: VerificationResult) -> MagicMock:
    service = MagicMock()
    service.verify = AsyncMock(return_value=result)
    return service


@pytest.mark.asyncio
async def test_verify_claim_matched_includes_verdict_question_reasoning_and_source():
    result = VerificationResult(
        user_question="網傳吃鳳梨心可以溶解血栓，是真的嗎？",
        verdict="錯誤",
        reasoning="查核報告指出這是缺乏醫學根據的說法，血栓需以藥物治療。",
        source_title="鳳梨心溶血栓查核報告",
        source_url="https://tfc-taiwan.org.tw/fact-check-reports/xxx",
        matched=True,
        related_info="",
    )
    service = _fake_service(result)
    configure_claim_tool(service)

    output = await verify_claim.ainvoke(
        {"query": "網傳吃鳳梨心可以溶解血栓，是真的嗎？"}
    )

    assert "判定：錯誤" in output
    assert "你問的：網傳吃鳳梨心可以溶解血栓，是真的嗎？" in output
    assert "查核報告指出這是缺乏醫學根據的說法，血栓需以藥物治療。" in output
    assert "https://tfc-taiwan.org.tw/fact-check-reports/xxx" in output
    service.verify.assert_awaited_once_with("網傳吃鳳梨心可以溶解血栓，是真的嗎？")


@pytest.mark.asyncio
async def test_verify_claim_unmatched_includes_related_info_and_omits_source():
    result = VerificationResult(
        user_question="網傳喝檸檬水可以排毒？",
        verdict="證據不足",
        reasoning="台灣事實查核中心目前沒有針對這則說法的查核報告。",
        source_title="",
        source_url="",
        matched=False,
        related_info="檸檬水的營養成分與一般水果類似，並無排毒之特殊功效。",
    )
    configure_claim_tool(_fake_service(result))

    output = await verify_claim.ainvoke({"query": "網傳喝檸檬水可以排毒？"})

    assert "判定：證據不足" in output
    assert "台灣事實查核中心目前沒有針對這則說法的查核報告。" in output
    assert "相關衛教資訊" in output
    assert "檸檬水的營養成分與一般水果類似，並無排毒之特殊功效。" in output
    # 未命中沒有可查證的來源，不得出現來源標題或網址行
    assert "資料來源" not in output
    assert "tfc-taiwan.org.tw" not in output


@pytest.mark.asyncio
async def test_verify_claim_without_service_returns_readable_message():
    # reset_tool_state 已把 _claim_verification_service 設回 None
    output = await verify_claim.ainvoke({"query": "隨便問一句"})
    assert "未初始化" in output


@pytest.mark.asyncio
async def test_verify_claim_output_contains_no_markdown_symbols():
    result = VerificationResult(
        user_question="網傳每天喝一杯醋可以降血脂？",
        verdict="部分錯誤",
        reasoning="適量攝取醋對血脂的實證效果有限，過量恐傷腸胃，不宜自行大量飲用。",
        source_title="喝醋降血脂查核報告",
        source_url="https://tfc-taiwan.org.tw/fact-check-reports/yyy",
        matched=True,
        related_info="",
    )
    configure_claim_tool(_fake_service(result))

    output = await verify_claim.ainvoke({"query": "網傳每天喝一杯醋可以降血脂？"})

    assert "**" not in output
    assert "##" not in output
    assert not any(line.strip().startswith("- ") for line in output.splitlines())


def test_verify_claim_docstring_distinguishes_from_get_rag_answer():
    """design.md 決策 7：docstring 是代理選工具的唯一依據，兩邊描述的問句
    形態不能混淆，且要明確指向 get_rag_answer 作為衛教知識問句的替代工具。"""
    doc = verify_claim.description or verify_claim.__doc__ or ""
    assert "查證" in doc
    assert "get_rag_answer" in doc
