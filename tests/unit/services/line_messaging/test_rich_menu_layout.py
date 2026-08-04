from app.services.line_messaging.rich_menu_layout import (
    CELL_H,
    CELL_W,
    IMAGE_PATH,
    RICH_MENU_HEIGHT,
    RICH_MENU_WIDTH,
    build_rich_menu_areas,
    image_path_for_language,
    liff_uri,
)


def test_liff_uri_strips_trailing_slash_and_joins_path():
    assert liff_uri("https://liff.line.me/abc/", "/family") == (
        "https://liff.line.me/abc/family"
    )
    assert liff_uri("https://liff.line.me/abc/", "/") == "https://liff.line.me/abc"


def test_liff_uri_adds_leading_slash_when_missing():
    assert liff_uri("https://liff.line.me/abc", "settings") == (
        "https://liff.line.me/abc/settings"
    )


def test_canvas_constants():
    assert RICH_MENU_WIDTH == 1200
    assert RICH_MENU_HEIGHT == 810
    assert CELL_W == 400
    assert CELL_H == 405
    assert IMAGE_PATH == "resources/rich_menu_zh-TW.png"


def test_build_rich_menu_areas_six_cells_and_actions():
    areas = build_rich_menu_areas("https://liff.line.me/abc")
    assert len(areas) == 6
    bounds = [(a["bounds"]["x"], a["bounds"]["y"]) for a in areas]
    assert bounds == [(0, 0), (400, 0), (800, 0), (0, 405), (400, 405), (800, 405)]
    for a in areas:
        assert a["bounds"]["width"] == 400
        assert a["bounds"]["height"] == 405

    assert areas[0]["action"] == {
        "type": "uri",
        "label": "家庭中心",
        "uri": "https://liff.line.me/abc",
    }
    assert areas[1]["action"]["uri"] == "https://liff.line.me/abc/medications"
    assert areas[2]["action"]["type"] == "location"
    assert areas[3]["action"]["uri"] == "https://liff.line.me/abc/family"
    assert areas[4]["action"] == {
        "type": "postback",
        "label": "語音回覆",
        "data": "action=toggle_voice_reply",
        "displayText": "切換語音回覆",
    }
    assert "enabled" not in areas[4]["action"]["data"]
    assert areas[5]["action"]["uri"] == "https://liff.line.me/abc/settings"


def test_build_rich_menu_areas_english_labels():
    areas = build_rich_menu_areas("https://liff.line.me/abc", language="en")
    labels = [a["action"]["label"] for a in areas]
    assert labels == ["Family", "Meds", "Hospitals", "Relatives", "Voice", "Settings"]


def test_build_rich_menu_areas_unknown_language_falls_back_to_zh_tw():
    areas = build_rich_menu_areas("https://liff.line.me/abc", language="xx")
    labels = [a["action"]["label"] for a in areas]
    assert labels == ["家庭中心", "用藥提醒", "附近醫院", "我的家人", "語音回覆", "設定"]


def test_image_path_for_language():
    assert image_path_for_language("en") == "resources/rich_menu_en.png"
