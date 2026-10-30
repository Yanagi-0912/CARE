# CARE 本地 Agent／LangSmith 實驗交接

更新：2026-10-16。這份交接對應「在本地執行 CARE，從 LangSmith 看 agent 的實際流程」；與其他 healthnews／RAG 品質實驗分開。

## 接手先看這裡

目的：確認真實 CARE LangGraph 的一次執行，可以完整上傳到 LangSmith CARE 專案，讓下一位開發者看見節點、模型呼叫、工具輸入輸出、耗時與錯誤。現階段是單案例 tracing smoke test，尚未建立完整的 agent 功能或醫療品質評測。

已確認的案例：使用者說「我想找附近的醫院，但還沒提供位置。請先請我分享位置。」本地 agent 呼叫 `request_location_quick_reply`，回覆請使用者分享位置。2026-10-15 的一次本地執行耗時約 3.26 秒，16 個 runs 均無錯誤；已另外從 LangSmith 讀回 root 結束時間、輸出與工具紀錄確認完整上傳。這個時間不包含整個 Python 程序的初始化時間，也不代表效能基準。

成功 trace：[2026-10-15 本地執行](https://apac.smith.langchain.com/o/b5e5674f-b8cc-4024-af08-dfc79cff3a33/projects/p/34724c4d-1670-449a-ade9-73bfecdcafc0/r/6fdaa3ab-8a21-4013-91d9-79f429ae3254?poll=true)。需登入有該 workspace 權限的帳號。

## 檔案清單

| 檔案 | 用途 |
| --- | --- |
| `../../scripts/agent_trace/run.py` | 從本地組合根取得真實 agent，執行指定 JSON 案例並讀回 trace 驗收 |
| `cases.json` | 案例輸入、預期輸出、必須出現的節點／工具名稱 |
| `results/20261016-handoff-verification.json` | 整理版 runner 的實際重跑結果，六項驗收全通過 |
| `results/20261015-local.json` | 對話中已確認的成功本地執行摘要；不是原始 trace 全量匯出 |
| `results/20261014-local.json` | 從原工作區 experiments 搬入的較早本地結果，保留原格式 |
| `results/20261014-deployed-image-local.json` | 用部署映像來源在本地跑的歷史結果；不是 GCP 線上執行 |
| `observed-flow.mmd` | 2026-10-14 留存的 LangGraph 結構圖；含其他分支，不表示這些分支已測過 |
| `generated/` | 新執行結果預設位置，忽略版控；需交接時先審閱再選擇性搬到 results |

早期資料原放在 workspace 的 `experiments/langsmith-agent-first-look-20261014/`，不在 CARE Git repo。當時的 `deployed-source/` 是部署程式快照，本次不複製整份舊 app；維護中的正式程式以本 repo `app/` 為準。原 `/tmp/care_local_trace_once.py` 為一次性探測程式，現整理成上述 runner，移除機器絕對路徑，新增案例／輸出參數及完整上傳驗收。

## 本地重跑

在 CARE 後端 repo 根目錄操作（有 `pyproject.toml` 的那層）。Python 3.12+，依 `uv.lock` 安裝：

```bash
uv sync
cp .env.example .env  # 只有尚未建立 .env 才需要；不要覆蓋自己的設定
```

將既有開發環境需要的設定補入 `.env`，本實驗特別需要：

```dotenv
GEMINI_API_KEY=<自己的 Gemini 金鑰>
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<有 CARE workspace 權限的金鑰>
LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com
LANGSMITH_PROJECT=CARE
```

`LANGSMITH_ENDPOINT` 必須對應帳號的資料區域；已知成功環境為 APAC。SDK 讀環境變數，不需要在 `app/core/config.py` 加欄位。runner 先載入 repo `.env`，再 import 組合根；shell 已設定的同名變數優先。LangSmith 目前由既有 LangChain 相依帶入，版本依 `uv.lock`。

runner 使用完整 `app.dependencies`，因此也會初始化其他服務；請沿用可啟動 CARE 的開發 `.env`，而不是只填上面幾個欄位就假設所有環境都能運行。`models/` 的本地分類模型若缺少會降級，可能改變模型呼叫數與耗時；建立方式參考 `scripts/fetch_text_encoder.py`。本案例不測 RAG、資料庫查詢或 Cohere rerank，缺少 Cohere 的警告不等於此案例失敗。

先驗證 JSON（不需憑證、不連網、不載入 app）：

```bash
python3 scripts/agent_trace/run.py --validate-only
```

再執行實際測試（會呼叫 Gemini、LangSmith 並產生費用）：

```bash
uv run python scripts/agent_trace/run.py
# 或在已裝好的虛擬環境：
.venv/bin/python scripts/agent_trace/run.py
```

預設讀 `evals/agent_trace/cases.json` 的 `location_without_coordinates`，寫到 `evals/agent_trace/generated/latest.json`。可用 `--cases`、`--case`、`--output`、`--trace-timeout` 覆寫。後者只控制 agent 完成後等待 trace 完整可見的時間，不限制初始化或模型呼叫總時長。

## 怎麼實作

1. runner 載入環境設定，從 `app/dependencies.py` 匯入 `_care_agent`。這是專案實際組合的 `Agent`，不是 fake。直接使用私有組合根變數是實驗耦合點，若後續改名，runner 也要調整。
2. 外層用 LangSmith `@traceable` 建立 root run，指定 UUID；`tracing_context` 把 project、local tag 與案例 metadata 傳給內層。
3. 呼叫 `Agent.invoke(user_input=...)`；`app/services/agent/agent.py` 內部呼叫已編譯的 LangGraph。LangChain／LangGraph 自動建立 child runs。
4. 本案例觀察流程為 `guardrail → agent → tools → request_location_quick_reply → agent → END`。`AgentNodes` 在 `app/services/agent/utils/nodes.py`，工具註冊在 `app/tools/registry.py`，位置工具本體在 `app/tools/medical_tools.py`。agent 節點可能使用本地捷徑，不保證每個 agent 節點都有一次 LLM 呼叫。
5. 收集回覆及 `call_request_location`／`emergency`／`answer_kind`，flush tracer；再輪詢 LangSmith，確認 root 有結束時間和輸出、已可見 runs 均結束、預期節點和工具都出現。
6. 比對 JSON 期望與已儲存輸出，寫出含 trace URL、commit、dirty 狀態、各 run 父子關係和驗收結果的摘要。成功 exit 0，驗收未通過 exit 1；認證或執行例外直接非零退出，需查看 stderr，可能不會產生結果檔。

這條路徑直接測 agent，不經 HTTP／LINE webhook、不發送 LINE 訊息，也不驗證對話歷史儲存、實際位置按鈕渲染或位置回傳後的院所搜尋。LangSmith 會收到真實 prompt 和工具資料，僅使用合成問題；金鑰與 `.env` 不進版控。

## 預期成果與驗收

- JSON `status` 為 `PASS`，`checks` 全為 true。
- 回覆包含「位置」，`call_request_location=true`、`emergency=false`。
- 有 `LangGraph`、`guardrail`、`agent`、`tools` 和 `request_location_quick_reply`；已上傳 root 輸出與本地結果一致，無 run error。
- 打開輸出的 `trace_url`，展開 LangGraph，能點選模型／工具步驟查看輸入、輸出、耗時。
- 不要求文字逐字一致、不固定 16 runs 或 3.26 秒；模型版本和本地分類模型會影響結果。輪詢驗收也不是 span 數量的完整性數學保證，而是檢查此案例必要路徑及當時可見 runs。

## 已遇到的問題

- GCP CARE 後端當時沒有 LangSmith API key，傳到預設 US API 收到 401；agent 本身成功，但那一次沒有可用的完整 trace。使用者後續指定改在本地。之後的 GCP 重跑連線被中斷，是否留有部分 trace 未驗證；不要拿它作為成功證據。本次交接不修改 GCP 設定。
- `flush()` 後立刻讀 run 可能已拿到 URL，但 root `end_time` 仍為空、child runs 也未到齊。舊探測程式遇到這個問題；整理版 runner 會等待必要步驟及結束時間再判定。
- 現用 `read_run/list_runs/get_run_url` 在當時 SDK 會顯示棄用警告（預告 2027-01-31 後移除）。先保留已成功使用的呼叫方式；升級 SDK 時需依安裝版 API 遷移並重新驗證，不要只改方法名稱。

## 下一位接手的工作

1. 先離線驗證案例，再本地重跑，確認 `PASS` 和 UI 能開啟；若 workspace 沒權限，向專案管理者取得存取權。
2. 審閱生成的 JSON 後才納入 `results/`，保留 commit／模型版本／測試日期，不覆寫歷史結果。歷史 2026-10-15 未記錄當時 commit 和確切模型設定，無法保證逐字重現。
3. 待本案例穩定後，另行規劃 RAG 成功／無資料、緊急短路、帶位置院所搜尋等案例；目前都未驗證，不要標成通過。含資料寫入或 LINE 推播的工具須先準備隔離測試依賴和合成帳號。
4. 若目標改成品質／效能評測，增加多題、重複次數、版本欄位與量化驗收；這份 smoke test 本身不證明所有 agent 功能正常。

## 交接整理版驗證紀錄

2026-10-16（台北時間）用本 repo 的 `scripts/agent_trace/run.py` 實際重跑，結果存於 `results/20261016-handoff-verification.json`：`PASS`，六項 checks 全為 true，agent 呼叫約 3.002 秒。該 JSON 使用 UTC 時間，故日期顯示 2026-10-15。執行基底 commit 為 `864b6a00a228070e884474a9ff7f642de4333b94`，當時包含未提交的交接檔案，`working_tree_dirty=true`；不是對乾淨 commit 的驗證。

離線案例驗證、Python 語法編譯、所有交接 JSON 解析與 `git diff --check` 已通過。完整專案檢查 `./init.sh`：9,822 passed、9 skipped、1 failed（93.50 秒）。失敗為 `tests/unit/services/line_messaging/test_medication_report_dispatch.py::test_slot_postback_confirms_the_chosen_log_at_the_stated_time`，預期 `(12, 0)`，實際 `(0, 15)`。這些測試及對應產品程式均未在本次修改；原因尚未進一步定位，不宣稱全套測試通過。摘要見 `results/20261016-repository-checks.json`。
