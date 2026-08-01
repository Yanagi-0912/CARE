import pytest

from app import dependencies


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
    assert handler._message_handler._history_service is dependencies._line_history_service
    assert (
        handler._message_handler._history_service._repo
        is dependencies.get_chat_history_repository()
    )
    assert handler._message_handler._user_profile_service is profile_service
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
