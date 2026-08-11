import pytest

from app.services.medical.department_matcher import CANONICAL_DEPARTMENTS
from app.services.medical.facility_type_matcher import FACILITY_TYPE_CATEGORIES
from app.services.medical.llm_term_resolver import (
    UNKNOWN,
    GeminiEnumTermResolver,
    build_department_resolver,
    build_facility_type_resolver,
)


class FakeLLM:
    """記錄收到的 prompt，並依序回傳預先安排好的回應。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def call_count(self) -> int:
        return len(self.prompts)


def _resolver(responses, **kwargs) -> tuple[GeminiEnumTermResolver, FakeLLM]:
    llm = FakeLLM(responses)
    resolver = build_department_resolver(invoke=llm)
    for key, value in kwargs.items():
        setattr(resolver, f"_{key}", value)
    return resolver, llm


@pytest.mark.asyncio
async def test_resolves_to_canonical_value():
    resolver, llm = _resolver([{"value": "外科"}])

    assert await resolver.resolve("大腸科") == "外科"
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_unknown_becomes_none():
    """模型說判不出來時要維持「解析失敗」，不可退化成隨便挑一科。"""
    resolver, _ = _resolver([{"value": UNKNOWN}])

    assert await resolver.resolve("隨便什麼字") is None


@pytest.mark.asyncio
async def test_value_outside_enum_is_rejected():
    """
    enum 之外的值必須擋掉。「大腸直腸外科」真實存在，但不是資料庫的部定專科值，
    放行會讓查詢靜默回 0 筆——比誠實說看不懂更糟。
    """
    resolver, _ = _resolver([{"value": "大腸直腸外科"}])

    assert await resolver.resolve("大腸科") is None


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_none():
    """兜底層掛掉不該讓找院所整條流程壞掉，只降級成解析失敗。"""
    resolver, _ = _resolver([RuntimeError("gemini down")])

    assert await resolver.resolve("大腸科") is None


@pytest.mark.asyncio
async def test_caches_hits_and_misses():
    """同一個說法只該問一次模型；判不出來的結果也要快取，否則長尾每次都付錢。"""
    resolver, llm = _resolver([{"value": "外科"}, {"value": UNKNOWN}])

    assert await resolver.resolve("大腸科") == "外科"
    assert await resolver.resolve("大腸科") == "外科"
    assert llm.call_count == 1

    assert await resolver.resolve("火星科") is None
    assert await resolver.resolve("火星科") is None
    assert llm.call_count == 2


@pytest.mark.asyncio
async def test_cache_evicts_oldest_when_full():
    resolver, llm = _resolver(
        [{"value": "外科"}, {"value": "內科"}, {"value": "外科"}],
        cache_size=1,
    )

    await resolver.resolve("甲科")
    await resolver.resolve("乙科")
    await resolver.resolve("甲科")  # 已被淘汰，要重新問

    assert llm.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "看" * 41])
async def test_skips_llm_for_empty_or_overlong_text(text):
    """空字串與整句話都不值得付一次呼叫。"""
    resolver, llm = _resolver([{"value": "外科"}])

    assert await resolver.resolve(text) is None
    assert llm.call_count == 0


@pytest.mark.asyncio
async def test_prompt_forbids_symptom_triage():
    """
    症狀分診是本專案刻意不做的醫療判斷（見 department_matcher 模組註解）。
    換成 LLM 之後這條線只剩 prompt 在守，因此把它釘成測試。
    """
    resolver, llm = _resolver([{"value": UNKNOWN}])
    await resolver.resolve("肚子痛")

    prompt = llm.prompts[0]
    assert "症狀" in prompt
    assert UNKNOWN in prompt


@pytest.mark.asyncio
async def test_prompt_lists_only_database_values():
    resolver, llm = _resolver([{"value": "外科"}])
    await resolver.resolve("大腸科")

    prompt = llm.prompts[0]
    assert "外科" in prompt
    # 純檢驗科別掛不了號，不可出現在候選清單裡
    assert "解剖病理科" not in prompt
    assert "臨床病理科" not in prompt


def test_department_candidates_are_all_real_database_values():
    resolver = build_department_resolver(invoke=FakeLLM([]))
    assert set(resolver._allowed) <= CANONICAL_DEPARTMENTS


def test_facility_type_candidates_are_the_three_categories():
    resolver = build_facility_type_resolver(invoke=FakeLLM([]))
    assert set(resolver._allowed) == set(FACILITY_TYPE_CATEGORIES)
