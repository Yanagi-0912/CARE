"""驗證 Flex Message 字級會跟隨使用者的 font_size 設定。"""

import pytest

from app.core.user_font_size import (
    DEFAULT_USER_FONT_SIZE,
    normalize_user_font_size,
    reset_request_font_size,
    set_request_font_size,
)
from resources.flex_messages import theme


def test_default_font_size_matches_user_settings_default():
    # UserSettings.font_size 預設為 large，兩者必須一致
    assert DEFAULT_USER_FONT_SIZE == "large"


@pytest.mark.parametrize(
    "raw", ["", None, "huge", "LARGE", "medium"]
)
def test_normalize_unknown_font_size_falls_back_to_default(raw):
    assert normalize_user_font_size(raw) == DEFAULT_USER_FONT_SIZE


def test_font_size_scales_every_role_monotonically():
    order = ["xxs", "xs", "sm", "md", "lg", "xl", "xxl", "3xl", "4xl", "5xl"]
    normal = theme.resolve_theme("normal")
    large = theme.resolve_theme("large")
    xlarge = theme.resolve_theme("xlarge")

    for role in ("title", "heading", "body", "caption", "button"):
        n, l, x = (
            order.index(getattr(normal, role)),
            order.index(getattr(large, role)),
            order.index(getattr(xlarge, role)),
        )
        assert n < l < x, f"{role} 未隨字級遞增"


def test_resolve_theme_reads_context_var_when_arg_omitted():
    token = set_request_font_size("xlarge")
    try:
        assert theme.resolve_theme() == theme.resolve_theme("xlarge")
    finally:
        reset_request_font_size(token)


def test_resolve_theme_arg_overrides_context_var():
    token = set_request_font_size("xlarge")
    try:
        assert theme.resolve_theme("normal") == theme.resolve_theme("normal")
        assert theme.resolve_theme("normal").title != theme.resolve_theme("xlarge").title
    finally:
        reset_request_font_size(token)


def test_buttons_use_resolved_button_size():
    ft = theme.resolve_theme("xlarge")
    button = ft.primary_button("測試", {"type": "uri", "label": "測試", "uri": "https://x"})
    assert button["contents"][0]["size"] == ft.button
    assert button["backgroundColor"] == theme.BRAND
