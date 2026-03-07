import json
import os
import sys

import requests

from app.services.line.token_manager import line_token_manager


def main() -> None:
    # 1. 取得 userId（優先使用命令列參數，沒有就看環境變數）
    user_id = sys.argv[1] if len(sys.argv) >= 2 else os.getenv("LINE_TEST_USER_ID")
    if not user_id:
        print("請在命令列帶入 userId，或先設定環境變數 LINE_TEST_USER_ID")
        sys.exit(1)

    # 2. 透過 LineTokenManager 取得 channel access token
    try:
        access_token = line_token_manager.get_token()
    except Exception as e:
        print(f"取得 LINE access token 失敗：{e}")
        sys.exit(1)

    # 3. 呼叫 LINE Profile API
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except requests.RequestException as e:
        print(f"呼叫 LINE Profile API 失敗：{e}")
        sys.exit(1)

    # 4. 在 terminal 印出結果
    print(f"HTTP 狀態碼：{resp.status_code}")
    try:
        data = resp.json()
        print("回傳內容：")
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except ValueError:
        print("回傳不是 JSON，原始內容如下：")
        print(resp.text)


if __name__ == "__main__":
    main()
