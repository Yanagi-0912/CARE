# CARE: Clinical Assistance & Resource Engine

一個以高齡友善設計、資料準確性保障與 AI 科技整合為核心目標的適地性健康醫療資訊 AI 助手。

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=Yanagi-0912_CARE&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=Yanagi-0912_CARE) [![SonarQube Cloud](https://sonarcloud.io/images/project_badges/sonarcloud-dark.svg)](https://sonarcloud.io/summary/new_code?id=Yanagi-0912_CARE)

## 聲明

暫無

## 規格與開發流程

架構、行為規則與開發流程集中在 OpenSpec（工具中立，任何編輯器或 AI 皆適用同一套）：

| 內容 | 位置 |
|------|------|
| 已上線規格 | `openspec/specs/` |
| 進行中的變更 | `openspec/changes/` |
| 專案設定與工作流程 | `openspec/config.yaml`、`AGENTS.md` |

主要規格：`agent-architecture`（LangGraph 流程與工具）、`backend-architecture`（分層與 DI）、`line-reply-rules`、`rag-responses`、`location-search`。

本 README 只放「怎麼安裝、啟動與部署」的操作說明。

## 快速開始

```bash
./init.sh          # macOS / Linux：建立 .venv、安裝依賴、跑 pytest
```

```powershell
.\init.ps1         # Windows PowerShell
```

啟動後端：

```bash
uvicorn app.main:app --port 8000 --reload --reload-exclude .venv
```

### 手動安裝（可選）

```bash
python -m venv venv
source venv/bin/activate        # Windows：venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 前端（LIFF App）

```bash
cd frontend/liff-app
npm install    # 首次或 package 有變更時
npm run dev    # 預設 http://localhost:5173
npm run test   # 前端測試
```

## 執行測試

```bash
pytest              # 或 pytest tests/ -v
```

> 必須在啟動過的虛擬環境裡執行，這樣 `pytest-asyncio` 才會載入，非同步的測試（`async def test_xxx`）才會正常運作。

Windows 範例：

```powershell
cd C:\你的路徑\CARE
venv\Scripts\activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

## LINE Webhook（callback 網址）

- Callback URL：`https://care.jamessu2016.com/line/callback`

在 [LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api) 的 webhook URL 請填入上面網址。

## Cloudflare Tunnel（已登入帳號可直接使用）

若你已登入 Cloudflare Tunnel 帳號，可直接執行：

```bash
cloudflared tunnel run care-api
```

這會直接開始監聽你本機啟動中的 Python 後端（預設是 `uvicorn` 跑在 `8000`）。

## ngrok（其他組使用方式，保留）

```bash
ngrok http 8000
```

在 [LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api) 的 webhook URL 改為 `"ngrok url"/line/callback`。

## Kubernetes 與 Kong（Ingress）

後端的 K8s manifest 與 Kong Helm values 已獨立放在 **`CARE-infra`** 專案（與本 repo 同層目錄的 `CARE-infra/`），請到該目錄依 `CARE-infra/README.md` 操作 `kubectl` 與 `helm`。

## 代理與工具流程（摘要）

完整規格見 `openspec/specs/agent-architecture/` 與 `openspec/specs/location-search/`。

目前代理以 **LangGraph** 編排（`app/services/agent/agent.py`）：

```
START → guardrail → agent →（tools → agent）→ END
```

可用工具（`app/tools/registry.py`）：

- `get_rag_answer`：RAG 知識庫問答（guardrail 判定健康相關才啟用）
- `request_location_quick_reply`：引導使用者分享 LINE 位置
- `find_nearby_hospitals`：依座標搜尋鄰近醫療院所

LINE 訊息經 `app/services/line_messaging/` 進入代理，回覆由 `LineMessageService` 送出。分層、DI 與新增工具的慣例見 `openspec/specs/backend-architecture/`。

## n8n workflow 多媒體處理功能

1. 使用 docker 啟動 n8n，預設運行在 `http://localhost:5678/`；local ASR 與 file parser 兩服務分別運行在 port `8200` 和 `8100`。
2. 將 `resources/mutimedia process.json` import 至 n8n，填寫 api key 並 publish。
3. 向 webhook `http://localhost:5678/webhook/bff1fd27-efc4-45cf-b64a-adb0475aa35c` 傳送 POST Request，body 中帶有要解析的檔案。

## direnv 虛擬環境提示字元（可選）

專案有 `.envrc`，`direnv allow` 後進目錄會自動啟用 `venv`。

- 關掉目前 shell 的虛擬環境：`deactivate`
- 關閉 direnv 自動啟動：`direnv deny`
