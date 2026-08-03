import json
import os
import sys

import requests
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.services.line_messaging.rich_menu_layout import (  # noqa: E402
    DEFAULT_RICH_MENU_LANGUAGE,
    RICH_MENU_HEIGHT,
    RICH_MENU_LANGUAGES,
    RICH_MENU_WIDTH,
    build_rich_menu_areas,
    chat_bar_text_for_language,
    image_path_for_language,
)
from app.services.line_messaging.token_manager import LineTokenManager  # noqa: E402

RICH_MENU_IDS_PATH = os.path.join(PROJECT_ROOT, "resources", "rich_menu_ids.json")


def validate_images() -> dict[str, str]:
    """Ensure every language image exists before calling LINE API."""
    paths: dict[str, str] = {}
    missing: list[str] = []
    for lang in RICH_MENU_LANGUAGES:
        rel = image_path_for_language(lang)
        abs_path = os.path.join(PROJECT_ROOT, rel)
        if not os.path.exists(abs_path):
            missing.append(f"  {lang}: {rel}")
        else:
            paths[lang] = abs_path
    if missing:
        print("❌ 缺少 Rich Menu 圖片，中止執行：")
        print("\n".join(missing))
        print("\n請準備 1200x810 的 PNG，命名為 rich_menu_{language}.png 放在 resources/。")
        sys.exit(1)
    return paths


def create_rich_menu(headers: dict, liff_url: str, language: str) -> str:
    rich_menu_data = {
        "size": {"width": RICH_MENU_WIDTH, "height": RICH_MENU_HEIGHT},
        "selected": True,
        "name": f"CARE six-grid {language}",
        "chatBarText": chat_bar_text_for_language(language),
        "areas": build_rich_menu_areas(liff_url, language),
    }
    response = requests.post(
        "https://api.line.me/v2/bot/richmenu",
        headers=headers,
        data=json.dumps(rich_menu_data),
    )
    if response.status_code != 200:
        print(f"FAILED: 建立 Rich Menu ({language}) 失敗: {response.text}")
        sys.exit(1)
    rich_menu_id = response.json().get("richMenuId")
    if not rich_menu_id:
        print(f"FAILED: 建立 Rich Menu ({language}) 未回傳 richMenuId")
        sys.exit(1)
    print(f"SUCCESS: {language} Rich Menu ID: {rich_menu_id}")
    return rich_menu_id


def upload_image(access_token: str, rich_menu_id: str, image_path: str, language: str) -> None:
    with open(image_path, "rb") as f:
        image_data = f.read()
    image_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "image/png",
    }
    upload_response = requests.post(
        f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
        headers=image_headers,
        data=image_data,
    )
    if upload_response.status_code != 200:
        print(f"FAILED: 上傳圖片 ({language}) 失敗: {upload_response.text}")
        sys.exit(1)
    print(f"SUCCESS: {language} 圖片上傳完成")


def read_existing_menu_ids() -> dict[str, str]:
    """讀取上一次寫入的 Rich Menu ID，供建立完成後清理舊選單使用。"""
    if not os.path.exists(RICH_MENU_IDS_PATH):
        return {}
    try:
        with open(RICH_MENU_IDS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARNING: 無法讀取 {RICH_MENU_IDS_PATH}（{exc}），略過舊選單清理")
        return {}
    if not isinstance(data, dict):
        print(f"WARNING: {RICH_MENU_IDS_PATH} 內容不是物件，略過舊選單清理")
        return {}
    return {str(lang): str(menu_id) for lang, menu_id in data.items() if menu_id}


def stale_menu_ids(old_ids: dict[str, str], new_ids: dict[str, str]) -> list[str]:
    """算出該刪除的舊 Rich Menu ID（去重、排除本次新建的、排序以求輸出穩定）。"""
    new_id_set = set(new_ids.values())
    return sorted({menu_id for menu_id in old_ids.values() if menu_id not in new_id_set})


def delete_rich_menu(access_token: str, rich_menu_id: str) -> bool:
    """刪除單一 Rich Menu。回傳 True 表示 LINE 上已無此選單（含原本就不存在）。"""
    response = requests.delete(
        f"https://api.line.me/v2/bot/richmenu/{rich_menu_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code == 200:
        print(f"  已刪除 {rich_menu_id}")
        return True
    if response.status_code == 404:
        print(f"  {rich_menu_id} 已不存在，略過")
        return True
    print(f"  FAILED: {rich_menu_id} 刪除失敗 (HTTP {response.status_code}) {response.text}")
    return False


def cleanup_old_rich_menus(
    access_token: str, old_ids: dict[str, str], new_ids: dict[str, str]
) -> None:
    """
    刪除上一輪留下的 Rich Menu。

    刻意安排在新選單建立並設為預設之後才執行：若前面任何一步失敗，
    腳本已經 exit，舊選單仍完好地服務中，不會出現沒有選單可用的空窗。
    清理失敗只警示不中斷 —— 此時新選單已經正常上線。
    """
    stale = stale_menu_ids(old_ids, new_ids)
    if not stale:
        print("沒有需要清理的舊 Rich Menu")
        return

    leftovers = [
        menu_id for menu_id in stale if not delete_rich_menu(access_token, menu_id)
    ]
    if leftovers:
        print("WARNING: 下列舊 Rich Menu 未能刪除，請至 LINE Developers 手動確認：")
        for menu_id in leftovers:
            print(f"  {menu_id}")
    else:
        print(f"SUCCESS: 已清理 {len(stale)} 個舊 Rich Menu")


def write_menu_ids(menu_ids: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(RICH_MENU_IDS_PATH), exist_ok=True)
    with open(RICH_MENU_IDS_PATH, "w", encoding="utf-8") as f:
        json.dump(menu_ids, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\nSUCCESS: 已寫入 {RICH_MENU_IDS_PATH}")


def set_default_rich_menu(access_token: str, rich_menu_id: str) -> None:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.post(
        f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
        headers=headers,
    )
    if response.status_code != 200:
        print(f"FAILED: 設定預設選單失敗: {response.text}")
        sys.exit(1)
    print(f"SUCCESS: 已將 {DEFAULT_RICH_MENU_LANGUAGE} 設為預設 Rich Menu")


def main() -> None:
    load_dotenv()

    channel_id = os.getenv("LINE_CHANNEL_ID")
    channel_secret = os.getenv("LINE_CHANNEL_SECRET")

    if not channel_id or not channel_secret:
        print("❌ 請在 .env 中設定 LINE_CHANNEL_ID 與 LINE_CHANNEL_SECRET")
        sys.exit(1)

    image_paths = validate_images()

    # 必須在 write_menu_ids 覆寫檔案之前讀取
    old_menu_ids = read_existing_menu_ids()

    print("正在取得 LINE Access Token...")
    token_manager = LineTokenManager(channel_id, channel_secret)
    access_token = token_manager.get_token()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    liff_url = os.getenv("LIFF_URL", "https://liff.line.me/your_liff_id")

    menu_ids: dict[str, str] = {}
    total = len(RICH_MENU_LANGUAGES)

    for i, lang in enumerate(RICH_MENU_LANGUAGES, start=1):
        print(f"\n[{i}/{total}] 處理語系 {lang}...")
        rich_menu_id = create_rich_menu(headers, liff_url, lang)
        upload_image(access_token, rich_menu_id, image_paths[lang], lang)
        menu_ids[lang] = rich_menu_id

    write_menu_ids(menu_ids)

    default_id = menu_ids[DEFAULT_RICH_MENU_LANGUAGE]
    print(f"\n[{total + 1}/{total + 2}] 設定預設 Rich Menu ({DEFAULT_RICH_MENU_LANGUAGE})...")
    set_default_rich_menu(access_token, default_id)

    print(f"\n[{total + 2}/{total + 2}] 清理舊 Rich Menu...")
    cleanup_old_rich_menus(access_token, old_menu_ids, menu_ids)

    ids_json = json.dumps(menu_ids, ensure_ascii=False, indent=2)
    print("\n--- rich_menu_ids.json ---")
    print(ids_json)
    print(
        "\n提示：可將上述 JSON 設為環境變數 RICH_MENU_IDS_JSON（單行），"
        "或直接使用 resources/rich_menu_ids.json。"
    )
    print("\nSUCCESS: 六語 Rich Menu 已全部建立並啟用！")


if __name__ == "__main__":
    main()
