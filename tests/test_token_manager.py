"""
測試 LINE Token Manager 是否正常工作
"""
from app.services.line import line_token_manager
import json

print("\n" + "="*70)
print("🔐 LINE Token Manager 測試工具")
print("="*70 + "\n")

# 1. 獲取 token 狀態資訊
print("📋 步驟 1: 檢查 Token Manager 配置")
print("-" * 70)
info = line_token_manager.get_token_info()
print(json.dumps(info, indent=2, ensure_ascii=False))

# 2. 嘗試獲取 token
print("\n📡 步驟 2: 嘗試獲取 Access Token")
print("-" * 70)

try:
    token = line_token_manager.get_token()
    print(f"✅ 成功獲取 token！")
    print(f"   Token 長度: {len(token)} 字元")
    print(f"   Token 前 30 字元: {token[:30]}...")
    
    # 3. 驗證 token 是否有效
    print("\n🔍 步驟 3: 驗證 Token 有效性")
    print("-" * 70)
    
    import requests
    url = "https://api.line.me/v2/bot/info"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers, timeout=10)
    
    if response.status_code == 200:
        data = response.json()
        print("✅ Token 有效！Bot 資訊：")
        print(f"   Bot 用戶 ID: {data.get('userId', 'N/A')}")
        print(f"   Bot 顯示名稱: {data.get('displayName', 'N/A')}")
        print(f"   頭像 URL: {data.get('pictureUrl', 'N/A')}")
    else:
        print(f"❌ Token 驗證失敗！狀態碼: {response.status_code}")
        print(f"   響應: {response.text}")
        
except ValueError as e:
    print(f"❌ 無法獲取 token: {e}")
    print("\n💡 解決方法：")
    if "LINE_CHANNEL_ID" in str(e) or "LINE_CHANNEL_SECRET" in str(e):
        print("   1. 請確保 .env 檔案包含以下設定：")
        print("      LINE_CHANNEL_ID=您的Channel_ID")
        print("      LINE_CHANNEL_SECRET=您的Channel_Secret")
        print("\n   2. 取得這些資訊的步驟：")
        print("      ① 前往 https://developers.line.biz/console/")
        print("      ② 選擇您的 Provider 和 Messaging API Channel")
        print("      ③ 在「Basic settings」標籤找到 Channel ID")
        print("      ④ 在「Basic settings」標籤找到 Channel secret")
        print("      ⑤ 複製這些值到 .env 檔案")
    else:
        print(f"   {e}")
        
except Exception as e:
    print(f"❌ 發生錯誤: {e}")

print("\n" + "="*70)
print("📝 配置說明")
print("="*70)
print("""
方案 1: 使用動態 Token（推薦）✅
在 .env 檔案中設定：

LINE_CHANNEL_ID=您的Channel_ID（數字）
LINE_CHANNEL_SECRET=您的32字元Channel_Secret

優點：
- Token 自動刷新，有效期 30 天
- 更安全，不需要手動管理 token
- Token 過期時自動重新獲取

---

方案 2: 使用靜態 Long-lived Token
在 .env 檔案中設定：

LINE_CHANNEL_SECRET=您的Channel_Secret
LINE_CHANNEL_ACCESS_TOKEN=您的Long_lived_token

缺點：
- 需要手動在控制台生成
- 如果 token 失效需要手動更新

---

如何取得 Channel ID 和 Channel Secret：
1. 前往 https://developers.line.biz/console/
2. 選擇您的 Provider 和 Channel
3. 點擊「Basic settings」標籤
4. Channel ID 在頁面頂部
5. Channel secret 在「Channel secret」區塊
6. 複製這些值到 .env 檔案

完成後重新運行此測試：
python test_token_manager.py
""")
print("="*70 + "\n")
