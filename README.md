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

## LINE Webhook（蘇奕勳 callback 網址）

- Callback URL：`https://care.jamessu2016.com/line/callback`

在 [LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api) 的 webhook URL 請填入上面網址。

## Cloudflare Tunnel（已登入帳號可直接使用）

若你已登入 Cloudflare Tunnel 帳號，可直接執行：

```bash
cloudflared tunnel run care-api
```

這會直接開始監聽你本機啟動中的 Python 後端（預設是 `uvicorn` 跑在 `8000`）。

## ngrok（其他組使用方式，保留）

啟動 ngrok：

```bash
ngrok http 8000
```

在 [LINE Developers 管理頁面](https://developers.line.biz/console/channel/2008834990/messaging-api) 的 webhook URL 改為 `"ngrok url"/line/callback`。

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

## Tool-First 開發規範（重要）

為了避免流程分散與重複邏輯，本專案統一採用 **Tool-First** 寫法。新增 AI 能力時請遵守以下原則：

- **先宣告工具，不直接在 service 硬寫分流**
  - 在 `app/tools/` 新增或擴充 tool declaration（名稱、描述、參數 schema）。
  - 例如：`app/tools/rag_tools.py`、`app/tools/medical_tools.py`。

- **由 registry 統一管理工具清單**
  - 在 `app/tools/registry.py` 的 `get_all_gemini_tools(...)` 統一組裝。
  - 需要 guardrail 時，用參數控制是否暴露特定工具（例如 `include_rag_tool`）。

- **Gemini client 只做通訊與解析，不做業務決策**
  - `app/services/gemini/client/service.py` 只負責：
    - 呼叫 Gemini API
    - 解析 `functionCall` / text
    - 錯誤處理與資料驗證
  - 不在這層做 RAG 分流、商業規則判斷。

- **Orchestrator 負責接住 functionCall 並分派執行**
  - `app/orchestration/response_orchestrator.py` 是唯一工具調度入口。
  - 根據 `function_name` 呼叫對應 service（如 `RagAnswerService`）。

- **Guardrail 獨立成 service**
  - `app/services/guardrail/service.py` 負責「工具前置判斷」。
  - 先做 guardrail，再決定哪些 tools 可提供給模型。

- **Line / Router 層不處理 AI 分流細節**
  - `message_service`、`event_handler` 只做通道協調與 I/O，不寫模型決策邏輯。

### 標準流程

1. 使用者訊息進入 `LineMessageService`
2. 交給 `ResponseOrchestrator`
3. `GuardrailService` 判斷是否允許 RAG tool
4. `GeminiService.generate_response(..., tools=...)`
5. 若回傳 `functionCall`，由 `ResponseOrchestrator` 分派到對應 service
6. 回傳最終文字給 LINE

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
