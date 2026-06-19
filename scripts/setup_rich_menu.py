import os
import json
import requests
from dotenv import load_dotenv

# 為了取得專案內的模組，將根目錄加入 sys.path
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.line_messaging.client.token_manager import LineTokenManager

def main():
    load_dotenv()
    
    channel_id = os.getenv("LINE_CHANNEL_ID")
    channel_secret = os.getenv("LINE_CHANNEL_SECRET")
    
    if not channel_id or not channel_secret:
        print("❌ 請在 .env 中設定 LINE_CHANNEL_ID 與 LINE_CHANNEL_SECRET")
        return
        
    print("正在取得 LINE Access Token...")
    token_manager = LineTokenManager(channel_id, channel_secret)
    access_token = token_manager.get_token()
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    liff_url = os.getenv("LIFF_URL", "https://liff.line.me/your_liff_id")
    
    # 1. 建立 Rich Menu
    print("\n[1/3] 正在建立 Rich Menu 物件...")
    rich_menu_data = {
        "size": {
            "width": 1200,
            "height": 810
        },
        "selected": True,
        "name": "Multi-function Menu",
        "chatBarText": "開啟功能選單",
        "areas": [
            {
                "bounds": {"x": 0, "y": 0, "width": 800, "height": 810},
                "action": {"type": "uri", "label": "家庭功能", "uri": liff_url}
            },
            {
                "bounds": {"x": 800, "y": 0, "width": 400, "height": 270},
                "action": {"type": "location", "label": "搜尋附近醫院"}
            },
            {
                "bounds": {"x": 800, "y": 270, "width": 400, "height": 270},
                "action": {"type": "postback", "label": "啟用語音回覆", "data": "action=toggle_voice_reply&enabled=true"}
            },
            {
                "bounds": {"x": 800, "y": 540, "width": 400, "height": 270},
                "action": {"type": "postback", "label": "關閉語音回覆", "data": "action=toggle_voice_reply&enabled=false"}
            }
        ]
    }
    
    response = requests.post(
        "https://api.line.me/v2/bot/richmenu",
        headers=headers,
        data=json.dumps(rich_menu_data)
    )
    
    if response.status_code != 200:
        print(f"FAILED: 建立 Rich Menu 失敗: {response.text}")
        return
        
    rich_menu_id = response.json().get("richMenuId")
    print(f"SUCCESS: 成功建立 Rich Menu! ID: {rich_menu_id}")
    
    # 2. 上傳圖片
    print("\n[2/3] 準備上傳圖片...")
    image_path = "resources/rich_menu.jpg"
    
    if not os.path.exists(image_path):
        print(f"⚠️ 找不到圖片檔案: {image_path}")
        print("請準備一張長寬精準為 1200x405 的圖片 (jpg/png) 並命名為 rich_menu.jpg 放在 resources 目錄下。")
        print("由於 LINE API 對尺寸要求極度嚴格，沒有圖片無法繼續綁定。")
        print(f"請在放好圖片後，手動執行以下 CURL 指令上傳圖片：")
        print(f"""
curl -v -X POST https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content \\
-H "Authorization: Bearer {access_token}" \\
-H "Content-Type: image/jpeg" \\
-T {image_path}
        """)
        return
        
    print(f"正在上傳圖片 ({image_path})...")
    with open(image_path, "rb") as f:
        image_data = f.read()
        
    image_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "image/jpeg"
    }
    if image_path.endswith('.png'):
        image_headers["Content-Type"] = "image/png"
        
    upload_response = requests.post(
        f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
        headers=image_headers,
        data=image_data
    )
    
    if upload_response.status_code != 200:
        print(f"FAILED: 圖片上傳失敗: {upload_response.text}")
        return
        
    print("SUCCESS: 成功上傳圖片!")
    
    # 3. 設定為所有用戶的預設選單
    print("\n[3/3] 正在將該選單設定為預設選單...")
    set_default_headers = {
        "Authorization": f"Bearer {access_token}"
    }
    set_default_response = requests.post(
        f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
        headers=set_default_headers
    )
    
    if set_default_response.status_code != 200:
        print(f"FAILED: 設定預設選單失敗: {set_default_response.text}")
        return
        
    print("\nSUCCESS: Rich Menu 已成功建立並啟用！")
    print("您現在可以打開 LINE 測試看看！")

if __name__ == "__main__":
    main()
