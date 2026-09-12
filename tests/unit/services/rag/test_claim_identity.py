"""ClaimIdentityVerifier 單元測試（結構化輸出以 DI 注入，禁止 monkey patch）。

驗證器與其餘查核判定卡元件不同：fail-closed 而非 fail-open。這裡刻意把
「呼叫失敗」與「兩個依賴都沒注入」分成兩組不同斷言——見 identity.py 模組
docstring：前者要 fail-closed 回 False，後者是接線疏漏，要在送出 LLM 請求
前就大聲失敗（RuntimeError），不能被吞成一個看似安全、實則永遠靜默失效
的 False。
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.services.rag.claim_verification.identity import GeminiClaimIdentityVerifier

_USER_CLAIM = "吃鳳梨心可以溶解血栓"
_CHECKED_CLAIM = "鳳梨酵素可以抗病毒"


@pytest.mark.asyncio
async def test_is_same_claim_true_when_invoke_reports_same():
    invoke = AsyncMock(return_value={"same": True})
    verifier = GeminiClaimIdentityVerifier(invoke_identity=invoke)

    result = await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    assert result is True
    invoke.assert_awaited_once()
    prompt = invoke.await_args.args[0]
    assert _USER_CLAIM in prompt
    assert _CHECKED_CLAIM in prompt


@pytest.mark.asyncio
async def test_is_same_claim_false_when_invoke_reports_different():
    invoke = AsyncMock(return_value={"same": False})
    verifier = GeminiClaimIdentityVerifier(invoke_identity=invoke)

    result = await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    assert result is False


@pytest.mark.asyncio
async def test_is_same_claim_fails_closed_to_false_when_invoke_raises():
    """核心規格：呼叫失敗要視為「不同主張」，不能悄悄變成 True 而誤採用他篇判定。"""
    invoke = AsyncMock(side_effect=RuntimeError("gemini timeout"))
    verifier = GeminiClaimIdentityVerifier(invoke_identity=invoke)

    result = await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    assert result is False


@pytest.mark.asyncio
async def test_is_same_claim_false_when_payload_is_not_a_dict():
    invoke = AsyncMock(return_value=True)  # 假設性錯誤回應：直接回布林值而非物件
    verifier = GeminiClaimIdentityVerifier(invoke_identity=invoke)

    result = await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    assert result is False


@pytest.mark.asyncio
async def test_is_same_claim_false_when_same_key_missing():
    invoke = AsyncMock(return_value={})
    verifier = GeminiClaimIdentityVerifier(invoke_identity=invoke)

    result = await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    assert result is False


@pytest.mark.asyncio
async def test_is_same_claim_false_when_same_value_is_not_boolean():
    """schema 只約束模型端的輸出；防禦性處理萬一 same 是字串等非 bool 型別，一樣 fail-closed。"""
    invoke = AsyncMock(return_value={"same": "true"})
    verifier = GeminiClaimIdentityVerifier(invoke_identity=invoke)

    result = await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    assert result is False


@pytest.mark.asyncio
async def test_is_same_claim_raises_when_neither_dependency_given():
    """兩個依賴都沒注入是接線疏漏，不是驗證失敗——刻意不 fail-closed 吞掉，
    否則這支驗證器會永遠靜默回 False，變成沒人發現的隱性故障。"""
    verifier = GeminiClaimIdentityVerifier()

    with pytest.raises(RuntimeError):
        await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)



# --- stage=claim_identity 觀測 -------------------------------------------
#
# 回傳型別維持 bool（刻意的設計，見模組 docstring），所以「模型判定不同」與
# 「這次根本沒判成」在呼叫端是同一個 False。要分開量就只能記在這裡：把上游
# 不穩算成「防線攔截率上升」，會把門檻校準帶往完全錯誤的方向。

_IDENTITY_LOGGER = "app.services.rag.claim_verification.identity"


def _identity_stages(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _IDENTITY_LOGGER
        and record.getMessage().startswith("stage=")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"same": True}, "outcome=same"), ({"same": False}, "outcome=different")],
)
async def test_is_same_claim_logs_judgement_outcome(caplog, payload, expected):
    verifier = GeminiClaimIdentityVerifier(
        invoke_identity=AsyncMock(return_value=payload)
    )

    with caplog.at_level(logging.INFO, logger=_IDENTITY_LOGGER):
        await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    stages = _identity_stages(caplog)
    assert len(stages) == 1
    assert "stage=claim_identity" in stages[0]
    assert expected in stages[0]


@pytest.mark.asyncio
async def test_is_same_claim_logs_error_outcome_when_call_fails(caplog):
    """呼叫失敗記 error，不得記成 different——這正是要與判定分開的那一半。"""
    verifier = GeminiClaimIdentityVerifier(
        invoke_identity=AsyncMock(side_effect=RuntimeError("gemini timeout"))
    )

    with caplog.at_level(logging.INFO, logger=_IDENTITY_LOGGER):
        result = await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    assert result is False
    stages = _identity_stages(caplog)
    assert len(stages) == 1
    assert "outcome=error" in stages[0]


@pytest.mark.asyncio
async def test_is_same_claim_logs_unparsable_outcome_when_payload_is_broken(caplog):
    """回應解析不出 bool 也不是「判定不同」，同樣要能從攔截量裡扣掉。"""
    verifier = GeminiClaimIdentityVerifier(
        invoke_identity=AsyncMock(return_value={"same": "yes"})
    )

    with caplog.at_level(logging.INFO, logger=_IDENTITY_LOGGER):
        result = await verifier.is_same_claim(_USER_CLAIM, _CHECKED_CLAIM)

    assert result is False
    stages = _identity_stages(caplog)
    assert len(stages) == 1
    assert "outcome=unparsable" in stages[0]
