
# CARE: Clinical Assistance &amp; Resource Engine

一個以高齡友善設計、資料準確性保障與 AI 科技整合為核心目標的適地性健康醫療資訊 AI 助手。#

## 聲明

暫無

## 開發步驟

建立虛擬環境

```
python -m venv venv
```

進入虛擬環境

```
venv\Scripts\activate
```

安裝套件

```
pip install -r requirements.txt
```

設定環境變數

建立 `.env` 檔案並設定必要的 API 金鑰：

```bash
# 複製範例檔案
cp .env.example .env

# 編輯 .env 檔案，填入你的 API 金鑰
# GEMINI_API_KEY=你的_Gemini_API_金鑰
# MODEL_NAME=gemini-2.0-flash-exp
# LINE_CHANNEL_ID=你的_LINE_Channel_ID
# LINE_CHANNEL_SECRET=你的_LINE_Channel_Secret
# LINE_CHANNEL_ACCESS_TOKEN=你的_LINE_Access_Token
```

啟動 Fast Api

```
uvicorn app.main:app --reload --port 8000
ngrok 輸入 ngrok http 8000
Line developer 管理頁面的webhoook 網址改為 "ngrok url"/api/v1/callback
```


''' 
跑測試 
python -m pytest tests/ -v -s
'''

'''
Swagger api 文件
http://127.0.0.1:8000/docs
'''