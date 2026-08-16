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
