"""ClaimNormalizer 單元測試（結構化輸出以 DI 注入，禁止 monkey patch）。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.rag.claim_verification.normalizer import GeminiClaimNormalizer


@pytest.mark.asyncio
async def test_normalize_strips_wrapper_words():
    invoke = AsyncMock(return_value={"claim": "吃鳳梨心可以溶解血栓"})
    normalizer = GeminiClaimNormalizer(invoke_normalize=invoke)

    result = await normalizer.normalize("網傳吃鳳梨心可以溶解血栓，是真的嗎？")

    assert result == "吃鳳梨心可以溶解血栓"
    invoke.assert_awaited_once()
    prompt = invoke.await_args.args[0]
    assert "網傳吃鳳梨心可以溶解血栓，是真的嗎？" in prompt


@pytest.mark.asyncio
async def test_normalize_falls_back_to_raw_text_when_invoke_raises():
    invoke = AsyncMock(side_effect=RuntimeError("gemini timeout"))
    normalizer = GeminiClaimNormalizer(invoke_normalize=invoke)
    user_text = "網傳打疫苗會磁化，是真的嗎？"

    result = await normalizer.normalize(user_text)

    assert result == user_text


@pytest.mark.asyncio
async def test_normalize_falls_back_when_claim_key_missing():
    invoke = AsyncMock(return_value={})
    normalizer = GeminiClaimNormalizer(invoke_normalize=invoke)
    user_text = "聽說喝檸檬水可以排毒？"

    result = await normalizer.normalize(user_text)

    assert result == user_text


@pytest.mark.asyncio
async def test_normalize_falls_back_when_claim_is_empty_string():
    invoke = AsyncMock(return_value={"claim": ""})
    normalizer = GeminiClaimNormalizer(invoke_normalize=invoke)
    user_text = "有人說吃薑黃可以治百病？"

    result = await normalizer.normalize(user_text)

    assert result == user_text


@pytest.mark.asyncio
async def test_normalize_falls_back_when_payload_is_not_a_dict():
    invoke = AsyncMock(return_value="吃鳳梨心可以溶解血栓")
    normalizer = GeminiClaimNormalizer(invoke_normalize=invoke)
    user_text = "網傳吃鳳梨心可以溶解血栓，是真的嗎？"

    result = await normalizer.normalize(user_text)

    assert result == user_text


@pytest.mark.asyncio
async def test_normalize_returns_input_unchanged_for_blank_text_without_calling_model():
    invoke = AsyncMock(return_value={"claim": "不應該被呼叫"})
    normalizer = GeminiClaimNormalizer(invoke_normalize=invoke)

    result = await normalizer.normalize("   ")

    assert result == "   "
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_normalize_requires_gemini_service_or_invoke_normalize():
    normalizer = GeminiClaimNormalizer()

    # 兩個依賴都沒注入時內部會 raise RuntimeError，但 normalize 要 fail-open
    # 回退到原問句，不能讓例外逸散到呼叫端。
    result = await normalizer.normalize("網傳喝咖啡會骨質疏鬆，是真的嗎？")

    assert result == "網傳喝咖啡會骨質疏鬆，是真的嗎？"
