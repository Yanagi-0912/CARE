CELL_W: int = 400
CELL_H: int = 405
RICH_MENU_WIDTH: int = 1200
RICH_MENU_HEIGHT: int = 810
IMAGE_PATH: str = "resources/rich_menu_zh-TW.png"


def liff_uri(base: str, path: str) -> str:
    """組合 LIFF deep link：{LIFF_URL}{path}。"""
    root = (base or "").rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{root}{path}"


def build_rich_menu_areas(liff_url: str) -> list[dict]:
    home = liff_uri(liff_url, "/")
    family = liff_uri(liff_url, "/family")
    settings = liff_uri(liff_url, "/settings")

    return [
        {
            "bounds": {"x": 0, "y": 0, "width": CELL_W, "height": CELL_H},
            "action": {"type": "uri", "label": "家庭中心", "uri": home},
        },
        {
            "bounds": {"x": CELL_W, "y": 0, "width": CELL_W, "height": CELL_H},
            "action": {"type": "uri", "label": "用藥提醒", "uri": family},
        },
        {
            "bounds": {"x": CELL_W * 2, "y": 0, "width": CELL_W, "height": CELL_H},
            "action": {"type": "location", "label": "附近醫院"},
        },
        {
            "bounds": {"x": 0, "y": CELL_H, "width": CELL_W, "height": CELL_H},
            "action": {"type": "uri", "label": "我的家人", "uri": family},
        },
        {
            "bounds": {"x": CELL_W, "y": CELL_H, "width": CELL_W, "height": CELL_H},
            "action": {
                "type": "postback",
                "label": "語音回覆",
                "data": "action=toggle_voice_reply",
                "displayText": "切換語音回覆",
            },
        },
        {
            "bounds": {"x": CELL_W * 2, "y": CELL_H, "width": CELL_W, "height": CELL_H},
            "action": {"type": "uri", "label": "設定", "uri": settings},
        },
    ]
