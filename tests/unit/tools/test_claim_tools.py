import json
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


def _uri_actions(node) -> list[dict]:
    actions: list[dict] = []

    def walk(n):
        if isinstance(n, dict):
            action = n.get("action")
            if isinstance(action, dict) and action.get("type") == "uri":
                actions.append(action)
            for value in n.values():
                walk(value)
        elif isinstance(n, list):
            for item in n:
                walk(item)

    walk(node)
    return actions


@pytest.mark.asyncio
async def test_verify_claim_matched_returns_flex_json_with_verdict_question_reasoning_and_source():
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

    payload = json.loads(output)
    assert payload["type"] == "flex"
    assert "錯誤" in payload["altText"]

    rendered = str(payload["contents"])
    assert "網傳吃鳳梨心可以溶解血栓，是真的嗎？" in rendered
    assert "查核報告指出這是缺乏醫學根據的說法，血栓需以藥物治療。" in rendered
    assert "台灣事實查核中心" in rendered

    actions = _uri_actions(payload["contents"])
    assert len(actions) == 1
    assert actions[0]["uri"] == "https://tfc-taiwan.org.tw/fact-check-reports/xxx"
    service.verify.assert_awaited_once_with("網傳吃鳳梨心可以溶解血栓，是真的嗎？")


@pytest.mark.asyncio
async def test_verify_claim_unmatched_returns_flex_with_related_info_and_no_source_action():
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

    payload = json.loads(output)
    rendered = str(payload["contents"])
    assert "證據不足" in payload["altText"]
    assert "相關衛教資訊" in rendered
    assert "檸檬水的營養成分與一般水果類似，並無排毒之特殊功效。" in rendered
    # 未命中沒有可查證的來源，不得出現來源標題或任何連結按鈕。
    # 「台灣事實查核中心」一詞仍會合法出現在 reasoning 本文裡（說明「TFC
    # 沒查過這則說法」），因此不能整段禁止出現該詞，只能鎖住「判定來源」
    # 這個屬性標籤本身沒有出現。
    assert "判定來源" not in rendered
    assert "資料來源" not in rendered
    assert _uri_actions(payload["contents"]) == []


@pytest.mark.asyncio
async def test_verify_claim_without_service_returns_readable_message():
    # reset_tool_state 已把 _claim_verification_service 設回 None
    output = await verify_claim.ainvoke({"query": "隨便問一句"})
    assert "未初始化" in output
    # 這條路徑沒有查核結果可組 Flex，仍是既有的純文字訊息，不是 JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)


@pytest.mark.asyncio
async def test_verify_claim_falls_back_to_plain_text_when_flex_assembly_fails():
    """Flex 組裝失敗（例如上游欄位型別非預期）不得讓使用者拿到例外，必須
    降級為 Flex 化之前的純文字格式。

    這裡刻意讓 verdict 帶入非字串值：`build_verdict_flex` 會把它直接放進
    Flex 節點的 text 欄位，FlexContainer.from_dict 對 text 欄位是嚴格字串
    （StrictStr），非字串會被 pydantic 拒絕而丟出例外；`_format_verdict_reply`
    對同一個值是透過 f-string 帶入，f-string 對任何型別都能安全轉成字串，
    因此能在不 monkey patch 任何一步、純粹靠建構子輸入的情況下，讓 Flex
    路徑失敗、純文字路徑存活。
    """
    result = VerificationResult(
        user_question="網傳喝薑茶可以退燒？",
        verdict=12345,  # type: ignore[arg-type]  # 刻意非字串，見上方 docstring
        reasoning="這是理由文字，不含任何判定字樣。",
        source_title="",
        source_url="",
        matched=False,
        related_info="",
    )
    configure_claim_tool(_fake_service(result))

    output = await verify_claim.ainvoke({"query": "網傳喝薑茶可以退燒？"})

    assert "判定：12345" in output
    assert "你問的：網傳喝薑茶可以退燒？" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)


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


def _unmatched_result(related_info: str) -> VerificationResult:
    return VerificationResult(
        user_question="網傳蜂蜜可以抗癌",
        verdict="證據不足",
        reasoning="台灣事實查核中心目前沒有針對這則說法的查核報告。",
        source_title="",
        source_url="",
        matched=False,
        related_info=related_info,
        verdict_slug="not-enough-evidence",
    )


@pytest.mark.asyncio
async def test_oversized_verdict_card_falls_back_to_text():
    """related_info 沒有長度上限，塞爆時必須退回純文字而不是送出被拒收的卡片。

    未命中時 related_info 放的是檢索到的衛教文章全文。實測一則 1,136 字的
    真實卡片已達 10 KB 上限的 79%，再多一篇就會超過；超過時
    build_verdict_flex 不會拋例外，既有的組裝失敗 fallback 因此不會觸發。
    """
    configure_claim_tool(_fake_service(_unmatched_result("衛" * 3000)))

    output = await verify_claim.ainvoke({"query": "網傳蜂蜜可以抗癌"})

    assert not output.strip().startswith("{")
    assert "判定：證據不足" in output
    assert "衛衛衛" in output


@pytest.mark.asyncio
async def test_normal_verdict_card_stays_flex():
    """防線不得誤殺正常大小的卡片。"""
    configure_claim_tool(
        _fake_service(_unmatched_result("蜂蜜不需要放冰箱，室溫避光即可。"))
    )

    output = await verify_claim.ainvoke({"query": "網傳蜂蜜可以抗癌"})

    payload = json.loads(output)
    assert payload["type"] == "flex"
    assert payload["contents"]["type"] == "bubble"
