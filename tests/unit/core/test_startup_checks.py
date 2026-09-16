"""啟動設定檢查：拿著會讓認證失效的設定，行程就不該起來。"""

from types import SimpleNamespace

import pytest

from app.core.startup_checks import (
    DEFAULT_JWT_SECRET,
    MIN_JWT_SECRET_LENGTH,
    openapi_visibility,
    validate_runtime_config,
)

GOOD_SECRET = "x" * MIN_JWT_SECRET_LENGTH


def make_settings(**overrides):
    base = dict(
        APP_ENV="production",
        AUTH_JWT_SECRET=GOOD_SECRET,
        AUTH_JWT_ALGORITHM="HS256",
        LINE_CHANNEL_ID="1234567890",
        LINE_CHANNEL_SECRET="channel-secret",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_valid_production_config_passes():
    validate_runtime_config(make_settings())


def test_default_secret_is_rejected_in_production():
    with pytest.raises(RuntimeError, match="AUTH_JWT_SECRET 仍是程式碼裡的預設值"):
        validate_runtime_config(make_settings(AUTH_JWT_SECRET=DEFAULT_JWT_SECRET))


def test_short_secret_only_warns_in_production(caplog):
    """短密鑰是弱點不是漏洞：擋下來會讓滾動更新停在舊版，只警告。"""
    with caplog.at_level("WARNING"):
        validate_runtime_config(make_settings(AUTH_JWT_SECRET="x" * (MIN_JWT_SECRET_LENGTH - 1)))
    assert any("長度" in r.getMessage() for r in caplog.records)


def test_missing_app_env_means_production():
    """缺一個環境變數的後果應該是「起不來」，不是「用開發設定跑正式流量」。"""
    settings = make_settings(AUTH_JWT_SECRET=DEFAULT_JWT_SECRET)
    del settings.APP_ENV
    with pytest.raises(RuntimeError):
        validate_runtime_config(settings)


def test_development_only_warns_about_the_secret(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        validate_runtime_config(
            make_settings(APP_ENV="development", AUTH_JWT_SECRET=DEFAULT_JWT_SECRET)
        )
    assert any("開發環境" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("env", ["Development", " development "])
def test_app_env_comparison_is_case_and_space_insensitive(env):
    validate_runtime_config(make_settings(APP_ENV=env, AUTH_JWT_SECRET=DEFAULT_JWT_SECRET))


@pytest.mark.parametrize("name", ["LINE_CHANNEL_ID", "LINE_CHANNEL_SECRET"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_missing_line_credentials_are_rejected_in_every_env(name, value):
    for env in ("production", "development"):
        with pytest.raises(RuntimeError, match=name):
            validate_runtime_config(make_settings(APP_ENV=env, **{name: value}))


@pytest.mark.parametrize("algorithm", ["none", "RS256", "hs256", ""])
def test_algorithm_outside_the_allowlist_is_rejected(algorithm):
    with pytest.raises(RuntimeError, match="AUTH_JWT_ALGORITHM"):
        validate_runtime_config(make_settings(AUTH_JWT_ALGORITHM=algorithm))


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_hmac_family_is_allowed(algorithm):
    validate_runtime_config(make_settings(AUTH_JWT_ALGORITHM=algorithm))


def test_all_problems_are_reported_at_once():
    """修一個、重啟、再看下一個——每一輪都是一次滾動更新的時間。"""
    with pytest.raises(RuntimeError) as exc:
        validate_runtime_config(
            make_settings(
                AUTH_JWT_SECRET=DEFAULT_JWT_SECRET,
                LINE_CHANNEL_SECRET="",
                AUTH_JWT_ALGORITHM="none",
            )
        )
    message = str(exc.value)
    assert "AUTH_JWT_SECRET" in message
    assert "LINE_CHANNEL_SECRET" in message
    assert "AUTH_JWT_ALGORITHM" in message


# ── /docs 與 /openapi.json 的可見性 ──────────────────────────────────


def test_docs_are_hidden_outside_development():
    for env in ("production", "staging", "", "prod"):
        assert openapi_visibility(env) == {
            "docs_url": None,
            "redoc_url": None,
            "openapi_url": None,
        }


def test_docs_are_visible_in_development():
    assert openapi_visibility("development")["openapi_url"] == "/openapi.json"
    assert openapi_visibility("development")["docs_url"] == "/docs"


def test_running_app_has_no_public_docs():
    """整支 app 是在 APP_ENV 未設（＝production）的測試環境下組出來的。"""
    from app.core.config import settings
    from app.main import app

    if settings.APP_ENV == "development":
        pytest.skip("本機把 APP_ENV 設成 development，這個斷言不適用")
    assert app.openapi_url is None
    assert app.docs_url is None
