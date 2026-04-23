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
    assert dependencies.get_user_profile_service() is dependencies._profile_service


def test_dependency_wiring_is_correct():
    line_message_service = dependencies.get_line_message_service()
    handler = dependencies.get_line_event_handler()
    profile_service = dependencies.get_user_profile_service()

    assert line_message_service.token_provider is dependencies.get_line_token_manager()
    assert line_message_service.medical_service is dependencies.get_medical_service()
    assert handler._line_message_service is line_message_service
    assert handler._medical_service is dependencies.get_medical_service()
    assert handler._response_orchestrator is dependencies._response_orchestrator
    assert profile_service._repo is dependencies._profile_repository


def test_get_mongodb_url_prefers_env_var(monkeypatch):
    monkeypatch.setattr(dependencies, "_mongodb_url", "mongodb://from-env")
    monkeypatch.setattr(dependencies.settings, "MONGODB_URI", "mongodb://from-settings")

    assert dependencies.get_mongodb_url() == "mongodb://from-env"


def test_get_mongodb_url_falls_back_to_settings(monkeypatch):
    monkeypatch.setattr(dependencies, "_mongodb_url", None)
    monkeypatch.setattr(dependencies.settings, "MONGODB_URI", "mongodb://from-settings")

    assert dependencies.get_mongodb_url() == "mongodb://from-settings"


def test_get_mongodb_url_raises_when_missing(monkeypatch):
    monkeypatch.setattr(dependencies, "_mongodb_url", None)
    monkeypatch.setattr(dependencies.settings, "MONGODB_URI", "")

    with pytest.raises(ValueError) as exc:
        dependencies.get_mongodb_url()
    assert "MONGODB_URL" in str(exc.value)
