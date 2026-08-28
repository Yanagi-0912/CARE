"""
急迫度判斷器：處置正確性與失效行為。

判斷品質本身（模型會不會把「我阿公昏迷」判成緊急）不在這裡測——那需要打真的
API，屬於召回率量測的範疇。這裡測的是**拿到判斷之後系統怎麼處置**，也就是
純 LLM 方案裡唯一還能決定性斷言的部分。
"""

import asyncio

import pytest

from app.services.medical.symptom_classification.urgency import (
    NOT_URGENT,
    URGENCY_EMERGENCY,
    URGENCY_NONE,
    UrgencyClassifier,
    UrgencyVerdict,
)


def _classifier(payload=None, *, exc=None, delay=0.0, timeout=4.0):
    async def invoke(_prompt):
        if delay:
            await asyncio.sleep(delay)
        if exc is not None:
            raise exc
        return payload

    return UrgencyClassifier(invoke=invoke, timeout_seconds=timeout)


def _emergency_payload(display="你提到有人失去意識、叫不醒"):
    return {
        "happening_now": True,
        "needs_immediate_care": True,
        "display": display,
    }


# --- 兩個條件都必須成立 ------------------------------------------------------


@pytest.mark.asyncio
async def test_emergency_requires_both_conditions():
    verdict = await _classifier(_emergency_payload()).classify("我阿公昏迷")
    assert verdict.level == URGENCY_EMERGENCY
    assert verdict.is_emergency is True


@pytest.mark.asyncio
async def test_knowledge_question_about_serious_condition_is_not_emergency():
    """
    「中風要怎麼急救」會讓 needs_immediate_care 為真，但它是知識性問句。
    只看 needs_immediate_care 就會把衛教問句整批吃掉——那正是關鍵字版的病灶。
    """
    payload = {
        "happening_now": False,
        "needs_immediate_care": True,
        "display": "",
    }
    verdict = await _classifier(payload).classify("中風要怎麼急救")
    assert verdict.level == URGENCY_NONE


@pytest.mark.asyncio
async def test_ongoing_but_not_severe_is_not_emergency():
    payload = {
        "happening_now": True,
        "needs_immediate_care": False,
        "display": "",
    }
    verdict = await _classifier(payload).classify("我肚子痛要掛哪一科")
    assert verdict.level == URGENCY_NONE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"happening_now": None, "needs_immediate_care": True, "display": "x"},
        {"needs_immediate_care": True, "display": "x"},
        {"happening_now": True, "display": "x"},
        {},
    ],
)
async def test_missing_or_null_fields_are_not_emergency(payload):
    """欄位缺漏代表判斷沒有真的完成，不該當成緊急——那會變成隨機觸發。"""
    assert (await _classifier(payload).classify("測試")).level == URGENCY_NONE


# --- 失效行為（fail-open）---------------------------------------------------


@pytest.mark.asyncio
async def test_llm_error_fails_open():
    """
    純 LLM 方案沒有地板，中斷時只能 fail-open。fail-closed 會讓每次 API 中斷
    都變成「所有使用者都被叫去打 119」——那是把卡片變成雜訊。
    """
    verdict = await _classifier(exc=RuntimeError("boom")).classify("我阿公昏迷")
    assert verdict is NOT_URGENT


@pytest.mark.asyncio
async def test_timeout_fails_open():
    """這個判斷擋在所有回覆前面，不能讓它把整個 bot 拖住。"""
    classifier = _classifier(_emergency_payload(), delay=0.2, timeout=0.01)
    assert (await classifier.classify("我阿公昏迷")) is NOT_URGENT


@pytest.mark.asyncio
async def test_non_dict_payload_fails_open():
    assert (await _classifier("emergency").classify("我阿公昏迷")).level == URGENCY_NONE


@pytest.mark.asyncio
async def test_blank_input_skips_the_call():
    called = False

    async def invoke(_prompt):
        nonlocal called
        called = True
        return _emergency_payload()

    classifier = UrgencyClassifier(invoke=invoke)
    assert (await classifier.classify("   ")) is NOT_URGENT
    assert called is False


# --- display ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_display_is_truncated():
    """過長的說明會把卡片標題區撐開。"""
    verdict = await _classifier(_emergency_payload("很長" * 100)).classify("x")
    assert len(verdict.display) <= 40


@pytest.mark.asyncio
async def test_display_may_be_blank_and_card_still_builds():
    verdict = await _classifier(_emergency_payload("")).classify("x")
    assert verdict.is_emergency is True
    assert verdict.display == ""


# --- 多語言 ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_language_is_passed_into_the_prompt():
    """
    判斷器本身語言無關（模型讀得懂就行），但 display 會直接印在卡片上，
    必須用使用者的語言。這是換掉 zh-TW regex 之後才成立的性質。
    """
    seen = {}

    async def invoke(prompt):
        seen["prompt"] = prompt
        return _emergency_payload()

    await UrgencyClassifier(invoke=invoke).classify("x", language="English")
    assert "English" in seen["prompt"]


@pytest.mark.asyncio
async def test_user_text_is_passed_verbatim():
    """不得先做正規化或改寫——判準是語意，改寫會改掉時態這個關鍵訊號。"""
    seen = {}

    async def invoke(prompt):
        seen["prompt"] = prompt
        return _emergency_payload()

    await UrgencyClassifier(invoke=invoke).classify("我剛剛被車撞，現在流好多血")
    assert "我剛剛被車撞，現在流好多血" in seen["prompt"]


# --- verdict 的衍生性質 ------------------------------------------------------


def test_hotlines_only_exposed_for_emergency():
    assert UrgencyVerdict(level=URGENCY_EMERGENCY).hotlines
    assert UrgencyVerdict(level=URGENCY_NONE).hotlines == ()


# --- 範圍：自殺／自傷意念不由本判斷器處理 -------------------------------------
#
# 這是範圍決定，不是判斷能力問題。本模組唯一的緊急出口是為生理急症設計的
# （紅底、「請立即就醫」、119／110），對「我想死」判對了送錯卡比不判更糟。
# 代價是這類訊息的回覆不受控——已記錄於模組註解與 openspec 決策 3b。


def test_prompt_excludes_suicidal_ideation_from_scope():
    """
    純 LLM 方案裡，範圍限制只能寫在 prompt。這條測試守的是「那段字還在」——
    它被誰不小心刪掉時，行為會無聲地變回紅色 119 卡。
    """
    from app.services.medical.symptom_classification import urgency

    prompt = urgency._PROMPT_TEMPLATE
    assert "不屬於本判斷的範圍" in prompt
    for phrasing in ("燒炭自殺", "我想跳樓", "不想活了"):
        assert phrasing in prompt


def test_prompt_keeps_completed_self_harm_in_scope():
    """
    排除的是「意念」，不是「已造成的生理傷害」。吞藥與割腕出血是進行中的
    中毒與出血，119 對那個情境是正確的——不能被範圍限制一起掃掉。
    """
    from app.services.medical.symptom_classification import urgency

    prompt = urgency._PROMPT_TEMPLATE
    assert "吞了一整罐" in prompt
    assert "割腕血流不止" in prompt
    assert "happening_now=true, needs_immediate_care=true" in prompt


@pytest.mark.asyncio
async def test_out_of_scope_verdict_produces_no_emergency_card():
    """判斷器回不緊急時，不得殘留任何緊急處置的痕跡。"""
    payload = {
        "happening_now": False,
        "needs_immediate_care": False,
        "display": "",
    }
    verdict = await _classifier(payload).classify("我要燒炭自殺")
    assert verdict.level == URGENCY_NONE
    assert verdict.hotlines == ()
    assert verdict.display == ""
