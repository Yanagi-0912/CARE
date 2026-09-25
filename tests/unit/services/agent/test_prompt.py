from app.i18n.messages import t
from app.services.agent.prompt import SYSTEM_PROMPT, build_system_prompt


def test_system_prompt_requires_rag_for_health_and_medical_fraud():
    assert "get_rag_answer" in SYSTEM_PROMPT
    assert "answer_from_uploaded_document" in SYSTEM_PROMPT
    assert "詐騙" in SYSTEM_PROMPT or "識詐" in SYSTEM_PROMPT
    assert "165" in SYSTEM_PROMPT
    assert "必須" in SYSTEM_PROMPT and "get_rag_answer" in SYSTEM_PROMPT
    assert "執法" in SYSTEM_PROMPT
    assert "匯款" in SYSTEM_PROMPT


def test_system_prompt_bans_markdown_and_routes_tools():
    assert "禁用 Markdown" in SYSTEM_PROMPT
    assert "[文字](網址)" in SYSTEM_PROMPT
    assert "request_location_quick_reply" in SYSTEM_PROMPT
    assert "lookup_medical_facility" in SYSTEM_PROMPT
    assert "open_official_site" in SYSTEM_PROMPT
    assert "打開官網" in SYSTEM_PROMPT
    assert "我有孕痛" in SYSTEM_PROMPT
    assert "附近有哪些醫院" in SYSTEM_PROMPT
    assert "禁止呼叫 `get_rag_answer`" in SYSTEM_PROMPT or "禁止 `get_rag_answer`" in SYSTEM_PROMPT
    assert "answer_from_uploaded_document" in SYSTEM_PROMPT
    assert "我剛上傳的報告" in SYSTEM_PROMPT
    assert "[RAG_ERR:" in SYSTEM_PROMPT
    assert "WEB_EMPTY" in SYSTEM_PROMPT


def test_system_prompt_rule_10_asks_user_to_retry_on_timeout():
    """逾時要請使用者稍後再問，不能跟其他代碼一樣說成「暫無相符資料」。"""
    assert "TIMEOUT" in SYSTEM_PROMPT
    assert "稍後再問一次" in SYSTEM_PROMPT


def test_build_system_prompt_en_requires_english_not_traditional_chinese():
    prompt = build_system_prompt("en")
    assert "必須只使用繁體中文" not in prompt
    assert "English" in prompt
    assert t("agent.rag_prefix", "en") in prompt
    assert t("agent.sources_heading", "en") in prompt


def test_build_system_prompt_zh_tw_requires_traditional_chinese():
    prompt = build_system_prompt("zh-TW")
    assert "繁體中文" in prompt
    assert t("agent.rag_prefix", "zh-TW") in prompt
    assert t("agent.sources_heading", "zh-TW") in prompt


def test_system_prompt_rule_8_preserves_sources_when_present():
    assert "參考來源網址" in SYSTEM_PROMPT
    assert "完整保留" in SYSTEM_PROMPT
    assert "不得修改網址" in SYSTEM_PROMPT


def test_system_prompt_rule_8_forbids_fabricated_sources_when_absent():
    assert "嚴禁" in SYSTEM_PROMPT
    assert "不含" in SYSTEM_PROMPT
    assert "自行新增" in SYSTEM_PROMPT or "捏造" in SYSTEM_PROMPT


def test_build_system_prompt_unknown_language_falls_back_to_zh_tw():
    prompt = build_system_prompt("fr")
    assert "繁體中文" in prompt
    assert t("agent.rag_prefix", "zh-TW") in prompt


def test_system_prompt_routes_medication_status_questions_to_the_status_tool():
    assert "get_medication_status" in SYSTEM_PROMPT
    assert "我今天要吃什麼藥" in SYSTEM_PROMPT
    # 藥物本身的衛教照舊走知識庫，不能被新規則吃掉。
    assert "漏吃降血壓藥要補吃嗎" in SYSTEM_PROMPT


def test_system_prompt_routes_family_directory_questions_to_the_directory_tool():
    assert "get_family_directory" in SYSTEM_PROMPT
    assert "我的父母是誰" in SYSTEM_PROMPT
    assert "我有哪些家人" in SYSTEM_PROMPT
    assert "王美玲是我的誰" in SYSTEM_PROMPT
    assert "relationship=parent" in SYSTEM_PROMPT
    assert "禁止把「父母」填進 `person`" in SYSTEM_PROMPT
    assert "我兒子王美玲" in SYSTEM_PROMPT
    assert "姓名與 `relationship` 都要填" in SYSTEM_PROMPT
    assert "禁止送進 `get_rag_answer`" in SYSTEM_PROMPT


def test_system_prompt_structures_the_department_patient_without_guessing():
    assert "relationship=child" in SYSTEM_PROMPT
    assert "relationship=spouse" in SYSTEM_PROMPT
    assert "person=王美玲" in SYSTEM_PROMPT
    assert "age=5" in SYSTEM_PROMPT
    assert "gender" in SYSTEM_PROMPT
    assert "person=王大明" in SYSTEM_PROMPT
    assert "symptom=懷孕了而且肚子痛" in SYSTEM_PROMPT
    assert "requested_department=婦產科" in SYSTEM_PROMPT
    assert "同一句同時明確提供姓名與稱謂時兩者都填" in SYSTEM_PROMPT
    assert "禁止從兒子、女兒、老婆等稱謂推測年齡或性別" in SYSTEM_PROMPT


def test_system_prompt_limits_cross_turn_patient_continuation():
    assert "前文只有一位可能的看診者" in SYSTEM_PROMPT
    assert "依最近出現、性別、年齡或照顧對象猜測" in SYSTEM_PROMPT
    assert "禁止呼叫工具" in SYSTEM_PROMPT
    assert "換我頭痛了" in SYSTEM_PROMPT
    assert "重設為本人" in SYSTEM_PROMPT
    assert "人物延續只依目前收到的近期對話" in SYSTEM_PROMPT
    assert "我老婆肚子痛要看哪科" in SYSTEM_PROMPT
    assert "她還有發燒，要看哪科" in SYSTEM_PROMPT
    assert "前文提過兩位男性家人" in SYSTEM_PROMPT


def test_system_prompt_keeps_multiple_patients_in_separate_cases():
    assert "`cases` 中每位看診者各填一筆" in SYSTEM_PROMPT
    assert "混入另一人的症狀" in SYSTEM_PROMPT
    assert "同句有多位看診者" in SYSTEM_PROMPT
    assert "同一次工具呼叫" in SYSTEM_PROMPT
    assert "禁止合併成一筆" in SYSTEM_PROMPT
    assert "我老婆肚子痛，我也頭痛" in SYSTEM_PROMPT


def test_system_prompt_declines_requests_unrelated_to_health():
    """guardrail 不放行只是不給 RAG 工具，回答照樣會產生。2026-09-14 正式環境
    「推導高等微積分」拿到一整篇數學推導，當時 (a)–(j) 沒有任何一條處理
    「不是健康、也不是寒暄」的請求。"""
    assert "(k)" in SYSTEM_PROMPT
    assert "婉拒" in SYSTEM_PROMPT
    assert "推導高等微積分" in SYSTEM_PROMPT
    # 寒暄照舊簡短回應，不能被新規則一起婉拒。
    assert "(j) 純寒暄" in SYSTEM_PROMPT
    assert "不要婉拒" in SYSTEM_PROMPT
    # 問 CARE 本身怎麼用（例如怎麼邀請家人）不算離題。
    assert "CARE 本身的功能" in SYSTEM_PROMPT


def test_date_context_gives_the_taipei_date_and_weekday():
    """模型要把「昨天」「禮拜一」換成幾天前，得先知道今天是台北的哪一天。"""
    from datetime import datetime, timezone

    from app.services.agent.prompt import build_date_context

    # UTC 9/13 16:30 已經是台北 9/14 00:30（星期一）。
    text = build_date_context(datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc))
    assert "2026-09-14" in text
    assert "星期一" in text


async def test_agent_node_puts_the_date_context_into_the_system_prompt(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import AIMessage, HumanMessage

    from app.services.agent.utils import nodes as nodes_module

    monkeypatch.setattr(nodes_module, "build_date_context", lambda: "\n［日期標記］\n")
    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(return_value=AIMessage(content="好"))
    nodes = nodes_module.AgentNodes(llm=llm, guardrail_service=MagicMock())

    await nodes.agent_node({"messages": [HumanMessage(content="你好")], "allow_rag": False})

    system_msg = llm.bind_tools.return_value.ainvoke.call_args[0][0][0]
    assert "［日期標記］" in system_msg.content


def test_system_prompt_routes_share_requests_to_share_care():
    """想把 CARE 分享給朋友或邀請家人 → share_care。關鍵字秒回攔不到的講法
    （句子較長或換了說法）靠這條規則叫出同一張卡。"""
    assert "share_care" in SYSTEM_PROMPT
    assert "怎麼讓我朋友也用這個" in SYSTEM_PROMPT


def test_system_prompt_rule_9_lists_verify_claim_among_flex_verbatim_tools():
    """次要 finding 3：規則 9 的 Flex 原樣輸出工具清單過去只列了
    find_nearby_hospitals／find_nearby_facilities_by_department／
    lookup_medical_facility／open_official_site，沒有 verify_claim——即使
    agent.py 的 medical_tool_names 機制實際上已經涵蓋 verify_claim（見
    test_agent.py 的回歸測試），系統提示的文字說明本身仍應完整列出，
    避免日後有人以「規則 9 已涵蓋」為由精簡掉那段機制。"""
    assert "verify_claim" in SYSTEM_PROMPT
    assert "Flex Message" in SYSTEM_PROMPT


def test_system_prompt_rule_10_treats_web_search_failures_like_timeout():
    """
    WEB_ERROR／WEB_RATE_LIMITED 是「沒搜成」不是「找不到」：要跟 TIMEOUT 一樣請
    使用者稍後再問，不能叫他換個說法——換十種說法都一樣，因為根本沒搜。
    """
    start = SYSTEM_PROMPT.index("若代碼是 TIMEOUT")
    retry_clause = SYSTEM_PROMPT[start : start + 200]
    assert "WEB_ERROR" in retry_clause
    assert "WEB_RATE_LIMITED" in retry_clause
    assert "稍後再問一次" in retry_clause
    assert "不要叫使用者換個說法" in retry_clause
    assert "[RAG_ERR:" in SYSTEM_PROMPT
