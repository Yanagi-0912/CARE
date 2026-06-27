import pytest

from app import dependencies


def test_getters_return_singleton_instances():
    assert dependencies.get_gemini_service() is dependencies._gemini_service
    assert dependencies.get_guardrail_service() is dependencies._guardrail_service
    assert dependencies.get_line_token_manager() is dependencies._line_token_manager
    assert dependencies.get_line_message_service() is dependencies._line_message_service
    assert dependencies.get_line_event_handler() is dependencies._line_event_handler
    assert dependencies.get_medical_service() is dependencies.medical_service
    assert dependencies.get_vector_search_config() is dependencies._vector_search_config
    assert dependencies.get_vector_search_reader() is dependencies._vector_search_reader
    assert dependencies.get_user_profile_service() is dependencies._user_profile_service


def test_dependency_wiring_is_correct():
    line_message_service = dependencies.get_line_message_service()
    handler = dependencies.get_line_event_handler()
    profile_service = dependencies.get_user_profile_service()

    assert line_message_service.token_provider is dependencies.get_line_token_manager()
    assert line_message_service.medical_service is dependencies.get_medical_service()
    assert handler._line_message_service is line_message_service
    assert handler._agent is dependencies._care_agent
    assert handler._chat_history_repository is dependencies.get_chat_history_repository()
    assert profile_service._repo is dependencies._user_profile_repository


def test_get_mongodb_uri_returns_settings_uri(monkeypatch):
    monkeypatch.setattr(dependencies.settings, "MONGODB_URI", "mongodb://from-settings")

    assert dependencies.get_mongodb_uri() == "mongodb://from-settings"


def test_get_mongodb_uri_raises_when_missing(monkeypatch):
    monkeypatch.setattr(dependencies.settings, "MONGODB_URI", "")

    with pytest.raises(ValueError) as exc:
        dependencies.get_mongodb_uri()
    assert "MONGODB_URI" in str(exc.value)
