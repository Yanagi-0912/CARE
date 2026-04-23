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

- **執行前端測試（LIFF App）**

```bash
cd frontend/liff-app
npm run test
```

- **啟動後端服務**

```bash
uvicorn app.main:app --port 8000 --reload --reload-exclude venv
```

## 目前已上線的 Tool 流程

目前只有 **RAG 回答**這條路徑有使用 tool calling；其他功能暫時不以 Tool-First 為主。

- 工具宣告在 `app/tools/rag_tools.py`，並由 `app/tools/registry.py` 組裝成 Gemini 可用的 tools。
- 請求進入後，由 `app/application/orchestration/response_orchestrator.py` 先呼叫 `GuardrailService` 判斷是否允許暴露 RAG tool。
- `app/infrastructure/gemini/services/gemini_service.py` 送出 `generate_response(..., tools=...)` 給模型。
- 如果模型回傳 `functionCall` 且 `function_name == "get_rag_answer"`，`ResponseOrchestrator` 會呼叫 `RagAnswerService` 執行檢索與回答。
- 若 RAG 無命中或執行失敗，會 fallback 成「不含 RAG tool」再次呼叫 Gemini 產生一般文字回覆。
- 回覆結果再交回 LINE 通道層送出。

## 架構與 SRP：`client`／`service`／`shared` 基本寫法

本專案目前採 `app/application` 與 `app/infrastructure` 分層。以下為角色定義。

### `client/`（對外邊界層）

- **職責**：與**外部系統**通訊——HTTP、gRPC、官方 SDK、OAuth／token 端點。
- **原則**：輸入輸出偏技術面，可映射自訂錯誤；不寫業務流程。

### `service`（應用／用例層）

- **職責**：一條完整 **use case**：驗證（若屬此流程）、組資料、呼叫一個或多個 `client`、組結果、決定錯誤要怎麼呈現給上層。
- **原則**：類名常見 `*Service`；**不**在這裡直接實作低階 HTTP（應委派給 `client`）。
- **備註**：同層流程可拆分成多個模組（例如 retrieval 與 answer service）。

### `shared/`（模組內共用）

- **職責**：同一模組內多處共用：型別、錯誤基底、純驗證函式、常數、與業務無關的小工具。
- **原則**：**不**放「只對單一 HTTP 端點說話」的程式（那屬於 `client`）；**不**放整條業務主流程（那屬於 service 或 orchestrator）。

### 組裝與進入點

- **`app/dependencies.py`** 作為 **composition root**：在此建立單例、注入 `client`／`service`，對外只暴露 `get_*()`。業務程式應**優先**由這裡取得依賴，避免在模組載入時隱性建立全域實例。

## 後端新規範（目前版）

以下規範以目前專案現況為準，後續若再調整分層，請同步更新本段。
本專案目前採 **朝 Clean Architecture 演進** 的做法（以 `application` / `infrastructure` 分層、DI 與組裝點分離為核心原則），並依現階段需求做漸進式落地，而非一次到位的教科書重構。

### 1) 分層與放置規範

- `app/application/`：放用例流程、協調邏輯（orchestration、rag 流程、line event 流程）。
- `app/infrastructure/`：放技術實作（SDK/HTTP client、shared validation/errors、vector search）。
- `app/tools/`：放 Gemini tool declarations 與 registry。
- `app/dependencies.py`：唯一組裝點（composition root）。

### 2) 依賴方向規範（重要）

- 允許：`application -> infrastructure`（透過介面或注入使用）。
- 禁止：`infrastructure/db -> app/dependencies` 反向依賴。
- 原則：由 `dependencies.py` 注入設定與實例，不在底層模組主動回頭拿依賴。

### 3) DI 與物件建立規範

- 新增服務時，優先使用 constructor 注入（`Protocol` 或明確介面）。
- 避免在模組 import 階段偷偷 `new` 外部 client（尤其是網路、DB、SDK 物件）。
- 全域單例只能在 `dependencies.py` 組裝，不要分散在各模組自行建立。

### 4) Tool 使用規範（現行）

- 目前只有 RAG 回答流程使用 tool calling。
- 新功能若不需要 tool，先走一般流程即可；不要為了形式硬接 tool。
- 要新增 tool 時，必須同步更新：
  - `app/tools/*.py`（declaration）
  - `app/tools/registry.py`（註冊）
  - `ResponseOrchestrator` 的分派邏輯（若需 function call 處理）

### 單元測試目錄對齊

- `tests/unit/application/` 底下子路徑對應 `app/application/`。
- `tests/unit/infrastructure/` 底下子路徑對應 `app/infrastructure/`。
- 舊的 `tests/unit/services/` 視為歷史目錄，新增測試請優先放在 `application` / `infrastructure` 對應位置。

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
