> 前置：change 1 `harden-url-whitelist` 與 change 2 `approve-with-content-preview` 皆已完成合併。
> 本 change 直接使用 change 1 提供的 `app/services/rag/whitelist.py` 的 `normalize_url` 與 `assert_allowed_urls`。

## 實作時與本文件的偏離（三處，皆為刻意）

1. **錯誤 detail 形狀改採 change 1 已上線的契約。** design 決策 8 原訂頂層兩個
   code（`url_invalid`／`url_not_allowed`）搭配扁平的 `urls` 陣列，但
   `harden-url-whitelist` 已在 admin approve 端點上線
   `{code, invalid_urls: [{url, reason}], message}`。同一支 API 對同一件事不該
   有兩種形狀；且原訂形狀無法表達「一個 malformed + 一個 not_allowed」的混合
   批次——單一頂層 code 必然把其中一個貼錯標籤，正是決策 8 自己要避免的失敗
   模式。決策 8 的目的（不同失敗給不同補救文案）改由前端依每個 item 的
   `reason` 組文案達成，粒度反而更細。
2. **`user_source_urls` 存的是正規化後的網址**（`assert_allowed_urls` 的回傳
   值），不是使用者貼的原字串。本文件 4.1 未寫明，但存原字串會讓正規化的效果
   在寫入這一步丟失。
3. **7.4 的 `styles.ts` 不存在。** 本頁樣式一律 inline Tailwind（見
   `components.tsx`），因此表單的 class 收在 `ReportFormDialog.tsx` 內的
   `formStyles` 常數，不另建檔案。

## 驗證狀態

- 後端 `pytest`：1574 passed（在既有 `.venv` 下執行；10.1 的 `./init.sh` 未跑，
  該腳本會重建 venv 並重裝依賴，與本次驗證目的無關）
- `CARE-LIFF`：`npm run test` 23 files / 164 tests passed、`npm run build` 通過

## 1. 設定與資料模型

- [x] 1.1 `app/core/config.py` 新增 `KNOWLEDGE_REPORT_MANUAL_DAILY_QUOTA`（預設 `10`）與 `KNOWLEDGE_REPORT_MAX_SOURCE_URLS`（預設 `3`），同步寫入 `.env.example`
- [x] 1.2 `app/models/knowledge_report.py`：`KnowledgeReport` 新增 `source: Optional[Literal["manual", "agent_tool", "web_fallback"]] = None`（舊紀錄為 `None`，視為非手動）
- [x] 1.3 `app/models/knowledge_report.py`：`CreateKnowledgeReportRequest`（現行 :47）收緊——`user_source_urls` 改 `list[str]` 必填、`min_length=1`、`max_length` 取 `KNOWLEDGE_REPORT_MAX_SOURCE_URLS`；`user_note` 改必填 `max_length=500`；`question` 補 `max_length=500`
- [x] 1.4 為 `question`／`user_note`／`user_source_urls` 加 `field_validator`：先 `strip`，空字串視為未填而拒絕（Pydantic 的 `min_length=1` 不會擋掉 `"   "`）；URL 單一長度上限 2048
- [x] 1.5 `tests/unit/routers/test_knowledge_reports.py` 新增請求驗證測試（缺 `user_source_urls`／缺 `user_note`／`user_note` 為空白字串／URL 超過上限數量／URL 過長 → 422），以 `app.dependency_overrides[get_current_user]` 與 `app.dependency_overrides[get_knowledge_report_service]` 注入，不使用 monkey patch

## 2. Repository：配額計數與編號碰撞

- [x] 2.1 `app/repositories/knowledge_report_repository.py` 新增 `count_manual_by_line_user_since(line_user_id, since, collection=None)`，查詢 `{"line_user_id": ..., "source": "manual", "created_at": {"$gte": since}}`
- [x] 2.2 確認不需新增索引：既有 `knowledge_report_line_user_created`（:21）已涵蓋 `line_user_id` + `created_at`；於 `tests/unit/repositories/test_knowledge_report_repository.py` 的 `test_ensure_indexes_covers_admin_queue_query`（:214）旁補一則斷言，確認索引清單未因本 change 增減
- [x] 2.3 `tests/unit/repositories/test_knowledge_report_repository.py` 新增 `count_manual_by_line_user_since` 的 filter 斷言，用 `collection=` 參數傳入 `MagicMock`（沿用同檔 `test_count_by_statuses`（:87）的注入方式），不使用 monkey patch

## 3. Service：`source` 參數與 `report_id` 重試

- [x] 3.1 `KnowledgeReportService.create`（`app/services/knowledge_reports/service.py:40`）新增 `source: str | None = None` 參數並寫入 `KnowledgeReport`；**不得** 在此加入白名單驗證或配額檢查（理由見 design 決策 1）
- [x] 3.2 `create` 改為最多重試 5 次：捕捉 `pymongo.errors.DuplicateKeyError` 後重新 `_generate_report_id` 再 `insert`；5 次皆失敗才讓例外往上。`_generate_report_id` 維持 4 碼
- [x] 3.3 `create_from_web_fallback`（:63）呼叫 `create` 時帶 `source="web_fallback"`；其餘行為（含 `delete_pending_or_reviewing_by_urls`）完全不變
- [x] 3.4 新增 `count_manual_reports_since(line_user_id, since)` 轉呼叫 repository，供 router 做配額檢查（router 不直接碰 repository）
- [x] 3.5 `tests/unit/services/knowledge_reports/test_service.py` 新增：`create` 遇 `DuplicateKeyError` 會換編號重試並最終成功（`mock_repo.insert` 的 `side_effect` 先丟 `DuplicateKeyError` 再成功），以及重試耗盡會往外拋。以建構子注入 `KnowledgeReportService(repository=mock_repo)`，不使用 monkey patch
- [x] 3.6 `tests/unit/services/knowledge_reports/test_service.py` 新增守門測試：`service.create` 帶非白名單 URL 時 **仍會建立成功**（證明驗證不在 service 層，自動路徑不會被白名單收緊誤傷）
- [x] 3.7 `tests/unit/services/knowledge_reports/test_service.py` 既有 `test_create_from_web_fallback`（:541）補上 `assert report.source == "web_fallback"`

## 4. Router：白名單驗證與配額

- [x] 4.1 `app/routers/users/knowledge_reports.py` 的 `create_knowledge_report`（:26）在呼叫 service 前先 `assert_allowed_urls(body.user_source_urls)`；不合格時回 400，detail 為 `{"code": "url_not_allowed" | "url_invalid", "urls": [...]}`，`urls` 一次列出全部不合格者
- [x] 4.2 同端點加入配額檢查：24 小時滾動視窗內 `source="manual"` 的筆數達上限時回 429，detail 為 `{"code": "quota_exceeded", "limit": N}`
- [x] 4.3 上限值以 FastAPI 依賴（例如 `get_manual_report_quota`）提供，讓測試用 `app.dependency_overrides` 覆寫，**不得** 用 monkey patch 改 `Settings`
- [x] 4.4 `create_knowledge_report` 呼叫 `service.create(..., source="manual")`，並把 `body.question` 原樣傳入（前端負責把說明欄同時填入 `question` 與 `user_note`，後端不做隱式複製）
- [x] 4.5 **既有測試會變紅**：`tests/unit/routers/test_knowledge_reports.py::test_create_knowledge_report`（:91）目前送 `{"question": "問題", "reason": "missing"}`，沒有 `user_source_urls` 與 `user_note`，收緊後會得到 422。改寫為送白名單 URL（沿用同檔 `ALLOWED_URL`，:21）與非空 `user_note`，並斷言 `mock_service.create` 收到 `source="manual"`
- [x] 4.6 `tests/unit/routers/test_knowledge_reports.py` 新增：送非白名單 URL → 400 且 detail 的 `code` 為 `url_not_allowed`、`urls` 含全部被拒者（送兩個壞 URL 驗證不是只回第一個）；送含反斜線的 URL → 400 `url_invalid`（迴歸 change 1 修掉的繞過）。以 `app.dependency_overrides` 注入 mock service，斷言 `service.create` 未被呼叫
- [x] 4.7 `tests/unit/routers/test_knowledge_reports.py` 新增配額測試：mock service 的 `count_manual_reports_since` 回傳達上限值 → 429 且 `code` 為 `quota_exceeded`；未達上限 → 200。上限以 `dependency_overrides` 覆寫為小值

## 5. Agent tool：維持 URL 選填

- [x] 5.1 `app/tools/knowledge_report_tools.py` 的 `submit_knowledge_report`（:24）維持 `user_source_urls: list[str] | None = None`，**不改為必填**
- [x] 5.2 tool 對收到的 URL 逐一以 `is_allowed_url` 過濾：不合格者丟棄並記錄 log，**不得** 因此讓工具呼叫回傳失敗訊息。輸入為 `None` 時必須維持 `None`（不可變成 `[]`）
- [x] 5.3 tool 呼叫 `service.create` 時帶 `source="agent_tool"`
- [x] 5.4 **既有測試會變紅**：`tests/unit/tools/test_knowledge_report_tools.py::test_submit_creates_pending_report`（:56）的 `service.create.assert_awaited_once_with(...)`（:75）需補上 `source="agent_tool"`。**`user_source_urls=None` 這個斷言必須原樣保留**——它是「tool 不強制 URL」的迴歸守門
- [x] 5.5 `tests/unit/tools/test_knowledge_report_tools.py` 新增：不帶 `user_source_urls` 呼叫 tool 仍成功建報（守住 design 決策 3）；帶一個白名單外 URL 時回報仍建立、`user_source_urls` 為 `[]` 且工具回傳成功訊息。以 `tools.configure_knowledge_report_tool(service)` 注入 mock service，不使用 monkey patch

## 6. 前端 API 與表單

- [x] 6.1 `CARE-LIFF/src/api/knowledgeReportsApi.ts`：`KnowledgeReportDto` 新增 `source?: 'manual' | 'agent_tool' | 'web_fallback' | null`
- [x] 6.2 同檔新增 `CreateKnowledgeReportBody`（`question` / `reason` / `user_note` / `user_source_urls`）與 `createKnowledgeReport(body)`，`POST` 到 `${BASE_URL}/api/knowledge-reports`
- [x] 6.3 `createKnowledgeReport` 不沿用現行 `parseError`（:51，會把物件 `JSON.stringify`）：改為解析結構化 detail，丟出帶 `code`／`urls`／`limit` 的自訂 Error，供表單對應 i18n 文案
- [x] 6.4 新增 `CARE-LIFF/src/pages/KnowledgeReports/ReportFormDialog.tsx`：URL（必填，`type="url"`）、說明（必填，`<Textarea>`）、`reason` 三選一（必選，重用既有 `knowledgeReports.reason.*` 文案當選項標籤，不新增 3×6 筆字串）
- [x] 6.5 送出時 `question` 與 `user_note` 都填入說明欄的 trim 後內容（design 決策 2），`user_source_urls` 為單一元素陣列
- [x] 6.6 以 `useMutation` + `useQueryClient().invalidateQueries({ queryKey: queryKeys.knowledgeReports })` 在成功後更新列表（沿用 `CARE-LIFF/src/pages/Medications/useMedications.ts:55-57` 的寫法），成功後關閉 Dialog 並以 sonner toast 提示
- [x] 6.7 URL 欄位下方常駐揭露規則的說明文字（講規則不列清單，design 決策 7）
- [x] 6.8 `CARE-LIFF/src/App.tsx` 新增 `/knowledge-reports/new` 路由，渲染同一個 `KnowledgeReportsPage`（掛載時自動開啟 Dialog、關閉時 `navigate('/knowledge-reports', { replace: true })`），並包在既有 `ProtectedRoute` 內（對齊 :101 的既有路由）

## 7. 前端顯示補齊（三處）

- [x] 7.1 `CARE-LIFF/src/pages/KnowledgeReports/index.tsx` 的 `interface KnowledgeReport`（:22）新增 `sourceUrls: string[]` 與 `userNote?: string`
- [x] 7.2 同檔 `mapReportDto`（:55）帶入 `report.user_source_urls` 與 `report.user_note`
- [x] 7.3 同檔詳情 Dialog 的 `<dl>`（:333）新增「我提供的網址」與「我的說明」兩個 `DIALOG_ITEM`；`question === userNote` 時說明只顯示一次（design 決策 2 的已知代價）；網址以 `<a target="_blank" rel="noopener noreferrer">` 呈現
- [x] 7.4 `CARE-LIFF/src/pages/KnowledgeReports/styles.ts` 補上表單與新增 Dialog 項目所需的 class 常數

## 8. i18n（六語言：zh-TW / en / id / vi / th / ja）

- [x] 8.1 `CARE-LIFF/src/i18n/messages.ts` 的 `knowledgeFeatureMessages`（:144）新增表單文案：`knowledgeReports.form.open`／`title`／`urlLabel`／`urlHint`／`noteLabel`／`notePlaceholder`／`reasonLabel`／`submit`／`cancel`／`submitSuccess`
- [x] 8.2 同處新增錯誤文案：`knowledgeReports.form.error.urlNotAllowed`／`urlInvalid`／`quotaExceeded`（帶 `{{limit}}`）／`generic`，文案方向依 design 決策 8（避免「網址無效」「格式錯誤」等會誤導成使用者打錯字的措辭）
- [x] 8.3 同處新增詳情欄位文案：`knowledgeReports.detail.sourceUrls`／`knowledgeReports.detail.userNote`
- [x] 8.4 六種語言全部補齊（zh-TW / en / id / vi / th / ja），漏補會回退到 zh-TW 而不會報錯，因此需人工逐一核對

## 9. 前端測試

- [x] 9.1 **既有測試會變紅**：`CARE-LIFF/src/tests/knowledgeReports.test.tsx:9-11` 的 `vi.mock('../api/knowledgeReportsApi', ...)` 工廠只定義了 `fetchKnowledgeReports`。頁面一旦 import `createKnowledgeReport`，vitest 會丟「No "createKnowledgeReport" export is defined on the mock」，該檔 **全部四則測試** 都會失敗。工廠需補上 `createKnowledgeReport: vi.fn()`
- [x] 9.2 `CARE-LIFF/src/tests/knowledgeReports.test.tsx` 新增：開啟表單 → 填 URL 與說明 → 送出 → 斷言 `createKnowledgeReport` 收到 `question === user_note`、`user_source_urls` 為單一元素陣列，且送出後重新取得列表
- [x] 9.3 同檔新增：`createKnowledgeReport` 以 `code: 'url_not_allowed'` 與兩個 `urls` 失敗時，畫面顯示白名單說明文案並逐一列出兩個被拒網址（不是只顯示第一個）
- [x] 9.4 同檔新增：`code: 'quota_exceeded'` 失敗時顯示帶次數的配額文案
- [x] 9.5 同檔新增：詳情 Dialog 顯示 `user_source_urls` 與 `user_note`（`mockReports` 需有一筆帶值；現行三筆的 `user_source_urls` 皆為 `[]`、`user_note` 皆為 `null`，:28、:41、:54）
- [x] 9.6 `CARE-LIFF/src/tests/i18n.test.ts` 補一則：切到 `vi` 後表單與白名單錯誤文案不含中文（沿用同檔 :39 `not.toContain('高血壓')` 的檢查方式）

## 10. 收尾

- [x] 10.1 `./init.sh` 全綠
- [x] 10.2 `CARE-LIFF` 執行 `npm run test`（vitest）全綠、`npm run build` 通過
- [x] 10.3 全專案搜尋 `delete_pending_or_reviewing_by_urls`，確認呼叫點仍只有 `app/services/knowledge_reports/service.py:74` 一處（手動路徑不得接上，見 proposal 的範圍排除）
- [x] 10.4 勾選本 tasks
