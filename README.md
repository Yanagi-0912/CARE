
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

啟動 Fast Api

```
uvicorn app.main:app --reload --port 8000
ngrok 輸入 ngrok http 8000
Line developer 管理頁面的webhoook 網址改為 "ngrok url"/line/callback
```

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

### direnv 虛擬環境提示字元（可選）

專案有 `.envrc`，`direnv allow` 後進目錄會自動啟用 `venv`，但 shell 不會自動顯示 `(venv)`。若要在提示字元顯示虛擬環境名稱，在 `~/.zshrc` 最後加上：

```bash
# 有 VIRTUAL_ENV 時在右側顯示 (venv)
setopt PROMPT_SUBST
RPROMPT='${VIRTUAL_ENV:+($(basename $VIRTUAL_ENV))}'
```

存檔後開新 terminal 或執行 `source ~/.zshrc`，再 `cd` 進 CARE 就會在提示右側看到 `(venv)`。
