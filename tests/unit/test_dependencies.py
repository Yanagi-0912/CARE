import pytest

from app import dependencies
from app.core.config import settings


def test_getters_return_singleton_instances():
    assert dependencies.get_gemini_service() is dependencies._gemini_service
    assert dependencies.get_guardrail_service() is dependencies._guardrail_service
    assert dependencies.get_line_token_manager() is dependencies._line_token_manager
    assert dependencies.get_line_event_handler() is dependencies._line_event_handler
    assert dependencies.get_medical_service() is dependencies.medical_service
    assert dependencies.get_query_embeddings() is dependencies._query_embeddings
    assert dependencies.get_rag_retriever() is dependencies._rag_retriever
    assert dependencies.get_user_profile_service() is dependencies._user_profile_service
    assert dependencies.get_tts_service() is dependencies._tts_service


def test_dependency_wiring_is_correct():
    handler = dependencies.get_line_event_handler()
    profile_service = dependencies.get_user_profile_service()
    token_manager = dependencies.get_line_token_manager()

    assert token_manager is dependencies._line_token_manager
    assert handler._message_handler._agent is dependencies._care_agent
    assert handler._media_handler._agent is dependencies._care_agent
    assert handler._location_handler._agent is dependencies._care_agent
    assert (
        handler._message_handler._history_service is dependencies._line_history_service
    )
    assert (
        handler._message_handler._history_service._repo
        is dependencies.get_chat_history_repository()
    )
    assert handler._message_handler._user_profile_service is profile_service
    assert dependencies._consultation_service._user_profile_service is profile_service
    assert handler._replier._token_manager is token_manager
    assert handler._replier._tts_service is dependencies._tts_service
    assert (
        handler._message_handler._loading_animation_service
        is dependencies._line_loading_animation_service
    )
    assert profile_service._repo is dependencies._user_profile_repository
    assert dependencies._web_search_service is not None
    assert dependencies._rag_answer_service is not None


def test_get_mongodb_uri_returns_settings_uri(monkeypatch):
    monkeypatch.setattr(dependencies.settings, "MONGODB_URI", "mongodb://from-settings")

    assert dependencies.get_mongodb_uri() == "mongodb://from-settings"


def test_get_mongodb_uri_raises_when_missing(monkeypatch):
    monkeypatch.setattr(dependencies.settings, "MONGODB_URI", "")

    with pytest.raises(ValueError) as exc:
        dependencies.get_mongodb_uri()
    assert "MONGODB_URI" in str(exc.value)


def test_safety_alert_service_reuses_the_loaded_drug_catalog():
    """藥證庫十一萬多筆、啟動時載入一次；再載入一份是純粹的記憶體浪費。"""
    service = dependencies._safety_alert_service

    assert service._catalog_service is dependencies._drug_catalog_service


def test_safety_alert_service_is_wired_with_its_own_dependencies():
    service = dependencies._safety_alert_service

    assert service._extractor is dependencies._drug_mention_extractor
    assert service._replier is dependencies._line_replier
    assert service._user_profile_service is dependencies._user_profile_service
    assert service._dedupe_hours == settings.SAFETY_ALERT_DEDUPE_HOURS


def test_handlers_only_get_the_safety_service_when_the_flag_is_on():
    """開關是唯一的閘門：關閉時 handler 拿到 None，整條路徑一步都不執行。"""
    handler = dependencies.get_line_event_handler()
    expected = (
        dependencies._safety_alert_service if settings.SAFETY_ALERT_ENABLED else None
    )

    assert handler._message_handler._safety_alert_service is expected
    assert handler._media_handler._safety_alert_service is expected


def test_claim_verification_service_is_wired_with_identity_verifier():
    """Task 10 教訓比照 Task 3 review 記錄的 gemini_service 疏漏：
    identity_verifier 是可選參數，忘記在這裡注入不會拋任何例外，只會讓
    同一性驗證整條防線悄悄消失、向量誤配原樣回到線上（design.md 決策 9）。
    這支測試就是那道「沒有其他測試會攔到」的防線本身。"""
    if not settings.CLAIM_VERIFICATION_ENABLED:
        pytest.skip("CLAIM_VERIFICATION_ENABLED is false")

    service = dependencies._claim_verification_service
    assert service is not None
    assert service._identity_verifier is dependencies._claim_identity_verifier
    assert service._identity_verifier is not None


def test_claim_matcher_is_wired_with_content_field_from_settings():
    """C2 finding：matcher 建構子的 content_field 預設值是硬寫的
    "chunk_content"，與 settings.MONGODB_TEXT_FIELD 的預設值 "text" 不同。
    這裡曾經完全沒有明確傳入，正確與否繫於「.env 裡的值剛好等於這個硬寫
    常數」的巧合；一旦照 .env.example 部署，match.content 就會是空字串，
    導致理由改寫的 prompt 沒有查核報告內容可用。這支測試釘住接線本身，
    而不是只靠「目前這份 .env 剛好對」的僥倖。"""
    if not settings.CLAIM_VERIFICATION_ENABLED:
        pytest.skip("CLAIM_VERIFICATION_ENABLED is false")

    matcher = dependencies._claim_matcher
    assert matcher is not None
    assert matcher.content_field == settings.MONGODB_TEXT_FIELD
