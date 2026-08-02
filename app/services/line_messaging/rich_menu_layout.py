CELL_W: int = 400
CELL_H: int = 405
RICH_MENU_WIDTH: int = 1200
RICH_MENU_HEIGHT: int = 810
IMAGE_PATH: str = "resources/rich_menu_zh-TW.png"

RICH_MENU_LANGUAGES: tuple[str, ...] = ("zh-TW", "en", "id", "vi", "th", "ja")
DEFAULT_RICH_MENU_LANGUAGE = "zh-TW"

AREA_LABELS: dict[str, tuple[str, str, str, str, str, str]] = {
    "zh-TW": ("家庭中心", "用藥提醒", "附近醫院", "我的家人", "語音回覆", "設定"),
    "en": ("Family", "Meds", "Hospitals", "Relatives", "Voice", "Settings"),
    "id": ("Keluarga", "Obat", "Rumah Sakit", "Anggota", "Suara", "Pengaturan"),
    "vi": ("Gia đình", "Thuốc", "Bệnh viện", "Người thân", "Giọng nói", "Cài đặt"),
    "th": ("ครอบครัว", "ยา", "โรงพยาบาล", "ญาติ", "เสียง", "ตั้งค่า"),
    "ja": ("ホーム", "服薬", "病院", "家族", "音声", "設定"),
}

CHAT_BAR_TEXT: dict[str, str] = {
    "zh-TW": "開啟功能選單",
    "en": "Open menu",
    "id": "Buka menu",
    "vi": "Mở menu",
    "th": "เปิดเมนู",
    "ja": "メニューを開く",
}

VOICE_DISPLAY_TEXT: dict[str, str] = {
    "zh-TW": "切換語音回覆",
    "en": "Toggle voice reply",
    "id": "Alihkan balasan suara",
    "vi": "Bật/tắt trả lời giọng nói",
    "th": "สลับการตอบด้วยเสียง",
    "ja": "音声返信を切り替え",
}


def normalize_rich_menu_language(language: str | None) -> str:
    if language in RICH_MENU_LANGUAGES:
        return language
    return DEFAULT_RICH_MENU_LANGUAGE


def image_path_for_language(language: str | None) -> str:
    lang = normalize_rich_menu_language(language)
    return f"resources/rich_menu_{lang}.png"


def chat_bar_text_for_language(language: str | None) -> str:
    lang = normalize_rich_menu_language(language)
    return CHAT_BAR_TEXT[lang]


def liff_uri(base: str, path: str) -> str:
    """組合 LIFF deep link：{LIFF_URL}{path}。

    首頁用裸 LIFF URL（不加尾隨 `/`），避免 `…/{liffId}/` 在部分環境異常。
    """
    root = (base or "").rstrip("/")
    if not path or path == "/":
        return root
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{root}{path}"


def build_rich_menu_areas(liff_url: str, language: str | None = "zh-TW") -> list[dict]:
    lang = normalize_rich_menu_language(language)
    home_label, meds_label, hospitals_label, relatives_label, voice_label, settings_label = (
        AREA_LABELS[lang]
    )
    voice_display = VOICE_DISPLAY_TEXT[lang]

    home = liff_uri(liff_url, "/")
    family = liff_uri(liff_url, "/family")
    settings = liff_uri(liff_url, "/settings")

    return [
        {
            "bounds": {"x": 0, "y": 0, "width": CELL_W, "height": CELL_H},
            "action": {"type": "uri", "label": home_label, "uri": home},
        },
        {
            "bounds": {"x": CELL_W, "y": 0, "width": CELL_W, "height": CELL_H},
            "action": {"type": "uri", "label": meds_label, "uri": family},
        },
        {
            "bounds": {"x": CELL_W * 2, "y": 0, "width": CELL_W, "height": CELL_H},
            "action": {"type": "location", "label": hospitals_label},
        },
        {
            "bounds": {"x": 0, "y": CELL_H, "width": CELL_W, "height": CELL_H},
            "action": {"type": "uri", "label": relatives_label, "uri": family},
        },
        {
            "bounds": {"x": CELL_W, "y": CELL_H, "width": CELL_W, "height": CELL_H},
            "action": {
                "type": "postback",
                "label": voice_label,
                "data": "action=toggle_voice_reply",
                "displayText": voice_display,
            },
        },
        {
            "bounds": {"x": CELL_W * 2, "y": CELL_H, "width": CELL_W, "height": CELL_H},
            "action": {"type": "uri", "label": settings_label, "uri": settings},
        },
    ]
