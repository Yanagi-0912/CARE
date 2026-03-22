# CARE: Clinical Assistance &amp; Resource Engine

一個以高齡友善設計、資料準確性保障與 AI 科技整合為核心目標的適地性健康醫療資訊 AI 助手。

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

啟動 Fast Api

```
uvicorn app.main:app --reload --port 8000
```

啟動ngrok

```
ngrok http 8000
```

[LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api)的 webhoook 網址改為 "ngrok url"/line/callback

## 在 Windows 上執行測試

- **啟動虛擬環境**

```powershell
cd C:\你的路徑\CARE
venv\Scripts\activate
```

- **安裝專案依賴（第一次或有變更時）**

```powershell
pip install -r requirements.txt
```

- **執行所有測試**

```powershell
python -m pytest tests/ -v
```

> 必須在啟動過的虛擬環境裡執行，這樣 `pytest-asyncio` 才會載入，非同步的測試（`async def test_xxx`）才會正常運作。

## 在 macOS / Linux（包含你這台）啟動與測試

- **建立並啟動虛擬環境**

```bash
cd /Users/你的帳號/Desktop/computersciencehomework/CARE
python3 -m venv venv
source venv/bin/activate
```

- **安裝依賴**

```bash
pip install -r requirements.txt
```

- **執行測試**

```bash
pytest           # 或 pytest tests/ -v
```

- **啟動後端服務**

```bash
uvicorn app.main:app --port 8000 --reload --reload-exclude venv
```

## n8n workflow 多媒體處理功能

1.首先使用docker啟動n8n，docker預設運行在 ``http://localhost:5678/``上，local asr 與 file parser兩服務分別運行在 port 8200 和 8100 上。

2.將resources\mutimedia process.json import至n8n中、填寫api key並publish

3.向webhook ``http://localhost:5678/webhook/bff1fd27-efc4-45cf-b64a-adb0475aa35c``傳送POST Request ，body中帶有要解析的檔案


### direnv 虛擬環境提示字元（可選）

專案有 `.envrc`，`direnv allow` 後進目錄會自動啟用 `venv`
#### 關閉虛擬環境與停用 direnv

- **關掉目前 shell 裡的虛擬環境（不再用 venv）**
  ```bash
  deactivate
  ```

- **關閉 direnv 自動啟動 venv**


  ```bash
  direnv deny
  ```
