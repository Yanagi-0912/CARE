from app.core.user_message import (
    get_request_user_message,
    reset_request_user_message,
    set_request_user_message,
)


def test_default_is_none_outside_agent_request():
    assert get_request_user_message() is None


def test_set_then_reset_restores_previous_value():
    token = set_request_user_message("魚油跟魚肝油有什麼差別？")
    try:
        assert get_request_user_message() == "魚油跟魚肝油有什麼差別？"
    finally:
        reset_request_user_message(token)
    assert get_request_user_message() is None


def test_empty_text_is_stored_as_none():
    token = set_request_user_message("")
    try:
        assert get_request_user_message() is None
    finally:
        reset_request_user_message(token)
