# Backend Architecture Spec

## Purpose

定義 CARE 後端（FastAPI）的分層、單一職責（SRP）與依賴注入慣例，作為新增或調整程式碼時的放置與依賴方向依據。本規格以現況為準：後端採 `app/services/*` 領域分層，並以 `app/dependencies.py` 作為唯一組裝點。

## Requirements

### Requirement: 分層與放置

系統程式碼 SHALL 依下列頂層目錄分層放置：

- `app/routers/`：HTTP 進入點（`line`、`liff`、`users`、`system` 等），只做路由與 I/O，不寫業務流程。
- `app/services/<domain>/`：各領域用例（如 `agent`、`guardrail`、`rag`、`medical`、`gemini`、`line_messaging`、`media`、`family`、`vector_search`）。
- `app/tools/`：Gemini／LangChain 工具宣告與 `registry.py` 組裝。
- `app/repositories/`、`app/db/`、`app/models/`、`app/schemas.py`：資料存取與資料模型。
- `app/core/`：設定與跨切面（`config.py`、`cors.py`）。
- `app/dependencies.py`：composition root（唯一組裝點）。
- `app/main.py`：應用進入點。

#### Scenario: 新增領域用例

- **WHEN** 需要新增一條業務用例
- **THEN** 於 `app/services/<domain>/` 建立對應服務，進入點放在 `app/routers/`，不在 router 內實作業務流程

### Requirement: 領域內 SRP 子結構

在 `app/services/<domain>/` 內，若需區分職責 SHALL 採用一致的子結構：`client/`（對外系統通訊：HTTP／SDK／token 端點，不寫業務流程）、`services/` 或領域主檔（一條完整 use case 的協調）、`shared/`（該領域內共用型別、錯誤基底、純函式、常數）。低階 HTTP／SDK 呼叫 SHALL NOT 寫在 use case 層，應委派給 `client/`。

#### Scenario: 領域需要對外通訊

- **WHEN** 某領域需要呼叫外部 API 或 SDK
- **THEN** 對外通訊放在該領域的 `client/`，由該領域的服務層協調呼叫，`shared/` 只放共用且與業務無關的小工具

### Requirement: 依賴注入與組裝點

系統 SHALL 以 `app/dependencies.py` 作為唯一 composition root：在此建立單例、注入依賴，對外只暴露取得函式；工具依賴 SHALL 透過 `configure_*`（如 `configure_rag_tool`、`configure_medical_tools`）於此注入。程式 SHALL NOT 在模組 import 階段建立外部 client（網路／DB／SDK 物件），底層模組 SHALL NOT 反向依賴 `app/dependencies`。

#### Scenario: 新增需要外部資源的服務

- **WHEN** 新增一個依賴外部 client／DB／SDK 的服務
- **THEN** 以 constructor 注入（`Protocol` 或明確介面），並在 `app/dependencies.py` 組裝單例，不在模組載入時隱性建立全域實例

### Requirement: 新增工具的同步更新

當新增一個 Gemini／LangChain 工具時，系統 SHALL 同步更新工具宣告（`app/tools/*.py`）、`app/tools/registry.py` 的註冊，以及（若需要處理該工具的分派或注入）`app/dependencies.py` 的 `configure_*` 與代理流程。不需要工具的功能 SHALL 先走一般流程，不為形式硬接工具。

#### Scenario: 新增一個工具

- **WHEN** 要為代理新增一個可呼叫的工具
- **THEN** 於 `app/tools/*.py` 宣告、於 `registry.py` 納入 `get_all_tools`，並在 `app/dependencies.py` 完成依賴注入

### Requirement: 測試目錄對齊

單元測試 SHALL 放在 `tests/unit/` 下，子路徑對應 `app/` 的分層（如 `tests/unit/services/<domain>/` 對應 `app/services/<domain>/`，`tests/unit/routers/`、`tests/unit/tools/`、`tests/unit/repositories/`）。整合測試 SHALL 放在 `tests/integration/`。

#### Scenario: 為新服務新增測試

- **WHEN** 為 `app/services/<domain>/` 的新服務撰寫單元測試
- **THEN** 測試放在對應的 `tests/unit/services/<domain>/`
