## 1. 先量測，再決定要不要做

**本節完成前不得進入第 2 節。**

- [ ] 1.1 **急症召回率**：整理急症敘述測試集（真實口語形式，例如「我阿公昏迷」「我剛剛被車撞，現在流好多血」「胸口悶還喘不過氣」），以及必須**不**觸發的對照組（「中風前兆有哪些」「食物中毒可以吃什麼」「中風的保險理賠」），量測判斷器的漏放率與誤報率。結果寫入 `openspec/changes/symptom-department-guidance/coverage.md`
- [ ] 1.2 **對照表覆蓋率**：以人工審定後的表為準，從常見口語症狀問句（每條目改寫 2 種說法，含「肚子痛」這類表內不存在的口語詞）量測命中率；同樣記入 `coverage.md`
- [ ] 1.3 **門檻校準**：以 1.2 的正樣本（同條目改寫）與負樣本（相鄰但不同科的症狀）掃 `MIN_MATCH_SCORE`，取誤配率為 0 的最低門檻
- [ ] 1.4 **決策點**：急症漏放率不為 0 → **停止本 change**；覆蓋率低於 40% → 於 proposal.md 補記結論並重新評估優先序

## 2. 資料整備（人工，非程式）

- [V] 2.1 `resources/symsptom_department_table/` 更名為 `resources/symptom_department_table/`（修正拼字）
- [ ] 2.2 人工審定對照表（原稿 `vghtpe_yuli_department_schema.json` 已整併為 `resources/symptom_department_table/symptom_department_reference.json`，repo 中不再有原稿）：逐條確認科別、改寫病症描述（design 決策 11）、移除院所特有分科（傳統醫學科、疼痛科）與非科別值（`15歲以下兒童` 應改為兒科）
- [ V] 2.3 修正來源錯字（「穿恐」→「穿孔」、「坐骨神精痛」→「坐骨神經痛」；「打曀」「火燒急觸電」「嚴重車禍急外傷」經人工確認不是錯字），處理 `vghtpe_yuli_review.json` 的 10 筆待複查條目
- [ ] 2.4 **重建 `emergency` 標註**：不沿用爬蟲的關鍵字初篩結果（design 決策 3），逐條人工定案
- [~] 2.5 ~~審定完成的條目 `confidence` 改為 `verified`；未審定者維持 `unverified` 並**不得**進入線上表~~ —— **改為整表 `status`**：逐條 `confidence` 未實作，審定狀態以表頭 `status` 表示（spec「對照表載入時強制轉為部定專科並驗證來源」，2026-09-11）
- [~] 2.6 ~~補齊症狀口語同義詞~~ —— **取消**：手寫同義詞表已移除（design 決策 12），口語對應改由語意向量處理，不再需要人工列舉

## 3. 急迫度判斷

- [x] 3.1 `app/services/medical/symptom_classification/urgency.py`：語意判斷器，結構化輸出 `happening_now` / `needs_immediate_care` / `display`，兩者皆為真才觸發（design 決策 2）
- [x] 3.2 **fail-open**：例外與逾時一律降級為不緊急並留下紀錄（design 決策 2）
- [x] 3.3 掛在 graph 上 `agent` 之前，判定為緊急時短路整條流程，**不進 agent、不跑 RAG、不產生任何門診科別建議**（design 決策 1）
- [x] 3.4 `tests/unit/services/medical/test_urgency_classifier.py`：兩條件的四種組合、欄位缺漏、例外與逾時降級、display 截斷、語言傳遞
- [x] 3.5 `tests/unit/services/agent/test_urgency_routing.py`：**端到端路由**——有無掛號意圖都攔得到、緊急卡是合法 Flex JSON 且不被後置處理改寫、不緊急時一般流程不受影響
- [ ] 3.6 誤報率觀測：上線後蒐集判定為緊急的實際訊息，人工複核比例（無地板，這是唯一的品質回饋）

## 4. 症狀詞正規化

- [x] 4.1 `app/services/medical/symptom_classification/normalizer.py`：口語症狀詞 → 表內條目。**手寫同義詞表已整層移除**（design 決策 12，理由與實測見 `coverage.md`），改為語意向量比對：高分直接採用、中間帶交 LLM 決選、低於門檻走保底
- [x] 4.2 LLM 輸出以 JSON Schema enum 約束為「表內症狀條目 ∪ {UNKNOWN}」，**schema 中不存在科別欄位**（design 決策 5）。向量召回後 enum 進一步縮小為 top-k，仍是封閉集合
- [x] 4.3 正規化失敗一律降級為 UNKNOWN，走保底路徑，不拋錯。取向量失敗時降級為「全表 enum 交 LLM」而非中斷
- [x] 4.4 `tests/unit/services/medical/test_symptom_normalizer.py`：三段分流（直接採用／LLM 決選／保底）、決選候選已縮小、集合外答案被拒、取向量失敗降級、無索引仍可用、**斷言 LLM 無法輸出科別**
- [x] 4.5 `app/services/medical/symptom_classification/vector_index.py` + `scripts/build_symptom_vectors.py`：向量索引與離線建檔；向量檔綁定對照表 hash，不同步即拒用（design 決策 12）
- [x] 4.6 `tests/unit/services/medical/test_symptom_vector_index.py`：hash 不符／檔案缺席／格式版本不符／檔案損壞一律拒用，維度不符拋錯，落地向量檔與落地對照表同步

## 5. 對照表與建議產生

- [x] 5.1 `app/services/medical/symptom_classification/symptom_table.py`：載入審定後的表，**載入時對每個科別呼叫 `resolve_department()`，任一條無法解析即拋錯**（design 決策 4）
- [~] 5.2 ~~`confidence` 非 `verified` 的條目載入時略過~~ —— 同 2.5，改為整表 `status`：非 `verified` 時照常載入並留下警告
- [x] 5.3 `app/services/medical/symptom_classification/symptom_department_service.py`：正規化 → 比對 → 建議。**本服務不做急迫度判斷**（design 決策 3）
- [x] 5.4 低於 `MIN_MATCH_SCORE`、或候選超過 `MAX_CANDIDATES`（目前 5）個 → 走保底建議（家醫科、內科、不分科 + 無法判斷說明），SHALL NOT 取最接近的一條（design 決策 6、7）
- [x] 5.5 輸出含免責、就醫提示與來源標示；用語 SHALL NOT 為診斷語氣（design 決策 8）
- [x] 5.6 **SHALL NOT 呼叫 `request_location_quick_reply`**（design 決策 9）。由 `tests/unit/services/agent/test_department_intent.py::test_symptom_with_registration_intent_does_not_request_location` 守著（2026-09-12 補）
- [x] 5.7 `tests/unit/services/medical/test_symptom_department_service.py`：命中單一候選、命中多候選、超過 `MAX_CANDIDATES` 個候選走保底、未命中走保底、**斷言輸出的科別皆為部定專科**、**斷言不觸發位置請求**（期望值會隨資料改變的案例已移至 `test_symptom_acceptance.py`，見 9.8）

## 6. Agent tool 與接線

- [x] 6.1 `app/tools/symptom_tools.py` 新增 `suggest_department_for_symptom`；docstring 明確區分與 `get_rag_answer` 的適用問句形態（問掛號 vs 問衛教）
- [x] 6.2 `app/tools/registry.py` 納入，常駐工具集、不隨 `include_rag_tool` 開關（~~`SYMPTOM_DEPARTMENT_ENABLED` 為 false 時不提供~~：旗標已取消，design 決策 10）
- [x] 6.3 `app/services/agent/prompt.py`：於工具優先順序新增分流規則與反例；**明確保留「純症狀敘述無掛號意圖 → 仍走 (g) `get_rag_answer`」**
- [x] 6.4 `app/dependencies.py` 組裝 `SymptomDepartmentService`
- [x] 6.5 ~~`app/core/config.py` 與 `.env.example` 新增 `SYMPTOM_DEPARTMENT_ENABLED`（default false）~~——旗標已取消（design 決策 10）。**`MIN_MATCH_SCORE` 不進 env**——門檻與維度是對「特定模型 × 特定版本對照表」校準的，跨環境調整沒有意義且危險，改為模組常數與向量檔一同版控（design 決策 12）
- [x] 6.6 `tests/unit/tools/test_symptom_tools.py`：工具常駐（`include_rag_tool` 開與關皆納入）、輸出與降級

## 7. 迴歸（確認既有行為未變）

- [x] 7.1 `tests/unit/services/medical/test_department_matcher.py`：確認別名表未新增任何症狀詞
- [x] 7.2 `tests/unit/services/medical/test_llm_term_resolver.py`：確認「肚子痛」等症狀輸入仍回 `UNKNOWN`（既有 case，不得因本 change 放寬）。LLM 在單元測試中是替身，實際守的是 prompt 仍禁止症狀分診（`test_prompt_forbids_symptom_triage`），回傳值本身只能線上驗證
- [x] 7.3 `tests/unit/services/agent/`：「附近有腸胃科嗎」「我牙齒痛」「附近有醫院嗎」三條既有路徑行為不變
- [~] 7.4 ~~旗標關閉時，全部既有測試與本 change 之前完全相同~~——旗標已取消（design 決策 10）

## 8. 收尾

- [ ] 8.1 `coverage.md` 補上最終數字（急症漏放率／誤報率、覆蓋率、門檻）
- [ ] 8.2 `./init.sh` 全綠（Windows：`.\init.ps1`）——2026-09-12：`pytest tests/unit` 4040 項中唯一失敗為本機既有的 magick 測試；`init.ps1` 本身尚未執行
- [x] 8.3 `department_matcher.py` 與 `llm_term_resolver.py` 的模組註解補一行：症狀邏輯已移至 `symptom_classification`，此兩處紅線不變
- [ ] 8.4 建立 git commit / PR（繁體中文描述），合併後 `openspec archive symptom-department-guidance`

## 9. 撤回人工補列與人工排序、修正卡片標註（2026-09-11，design 決策 14、15）

驗收標準見 `acceptance.md`。本節一律**修改既有函式本體**，不新增平行函式、旗標、模組或檔案；被取代的欄位、分支、註解與測試在同一次改動中刪除。

**資料**

- [x] 9.1 `resources/symptom_department_table/symptom_department_reference.json`：刪除 16 筆 `origin: project` 候選、刪除 11 個 `rank` 欄位（其中 7 個隨補列條目一併刪除）；`usage_rules` 的 origin 條文改寫為「只收來源所載、欄位白名單」，「3 家皆列」條文改為不寫死家數；`version` 升為 4
- [x] 9.2 重跑 `scripts/build_symptom_vectors.py`（刪了 4 個 term，表內容 hash 已變）

**載入與排序**

- [x] 9.3 `app/services/medical/symptom_classification/symptom_table.py`：`DepartmentCandidate` 刪除 `rank`、`note`；刪除 `_UNRANKED`；`_candidate_sort_key` 只留（來源家數、院所數）；`load_symptom_table` 遇到空 `sources`、未登記的來源代碼、帶 `rank`／`origin` 的候選時拋 `SymptomTableError`；改寫模組註解中關於 rank 的段落，以及已過期的 status 註解（「目前的表就是 unverified」）
- [x] 9.4 `app/services/medical/symptom_classification/symptom_department_service.py`：`term_sources` 的註解改為「標註分母 N 的來源」；模組註解的「上限 3」改為對齊 `MAX_CANDIDATES`

**卡片**

- [x] 9.5 `resources/flex_messages/medical_messages/symptom_department_flex_message.py` 的 `_reason_for`：刪除 note 拼接；標註改為 design 決策 15 的三種句型，分母取 `result.term_sources`
- [x] 9.6 同檔：刪除 `_cites_other_department`、`_SOURCE_LABEL_OTHER_DEPARTMENT`、`_source_label` 的分支，以及 `_cited_references` 退到 `term_sources` 的分支；修正模組註解的「至多 3 個候選」

**測試**

- [x] 9.7 刪除 legacy 測試（2026-09-11 完成）。只刪「專測撤回行為」的測試與斷言，刪完後現行程式仍全綠
  - `tests/unit/services/medical/test_symptom_department_service.py`：刪除 `test_manual_rank_outranks_cross_source_agreement`、`test_ranked_candidates_come_before_unranked_ones`、`test_asthma_offers_family_medicine_to_adults`；`test_common_cold_points_at_where_people_actually_go` 只留「不帶次專科標籤」的斷言，改名為 `test_common_cold_carries_no_subgroup_label`；`test_hyperlipidemia_offers_the_cardiology_route` 拿掉家醫科斷言；`test_reviewed_candidate_order` 刪除感冒一列
  - `tests/unit/services/medical/test_symptom_normalizer.py`：刪除 `test_nosebleed_goes_to_ent`、`test_phlegm_maps_to_both_airway_departments`、`test_project_added_entry_claims_no_source_consensus`，以及只有它們在用的 `table` fixture
  - `tests/unit/services/medical/test_symptom_department_flex.py`：刪除 `test_project_added_entry_shows_no_source_section`、`test_term_level_sources_are_cited_when_the_candidate_has_none`、`test_term_level_citation_never_overrides_a_direct_one`、`test_entry_with_no_source_anywhere_still_shows_nothing` 與輔助函式 `_project_added`；`test_direct_citation_keeps_the_plain_label` 刪除（標題由 T28 驗證）。第二輪刪除已被完整取代的測試：交替配色 2 個 → 同檔 `test_candidate_boxes_match_template`；LINE SDK 驗證 3 個 → T27；直接測內部函式 `_cited_references` 的 3 個 → T28（D1、D2）；參考來源標題、保底卡無來源 → T28、T26
- [x] 9.8 期望值會隨資料改變的舊測試，改由 `tests/unit/services/medical/test_symptom_acceptance.py` 驗證，舊測試刪除（2026-09-11）：`test_candidates_sorted_by_cross_source_agreement` → T11；`test_vomiting_now_has_an_adult_department` → T15；`test_term_sources_survive_the_pediatric_filter` → T28（D1）；`test_too_many_candidates_falls_back_instead_of_guessing` → T19；`test_reviewed_candidate_order` 的坐骨神經痛、性病兩列 → T12
- [x] 9.9 新增測試集中在 `test_symptom_acceptance.py`（acceptance H 節），不分散到既有檔案
- [x] 9.10 acceptance H 節全數通過；D15 為線上抽測（K4）
- [x] 9.11 `venv` 下 pytest 全綠（acceptance F 節），回報動到的既有函式與刪除清單（acceptance E 節）

**2026-09-11 追加**

- [x] 9.12 `symptom_table.py` 載入檢查：欄位白名單、`term` 不可為空、`sources` 不可重複、「症狀＋科別」全表唯一、`db_facility_count` 為正整數（spec「對照表載入時強制轉為部定專科並驗證來源」）；白名單常數逐欄寫上意義註解
- [x] 9.13 刪除 `SymptomEntry.kind` 與載入時的 `kinds` 收集；JSON 保留 `kind` 供維護者分類
- [x] 9.14 `FALLBACK_DEPARTMENTS` 改為（家醫科、內科、不分科），見 design 決策 6
- [x] 9.15 實作前行為快照：全表 392 個條目 × {40 歲, 8 歲} 的輸出存於 `baseline_snapshot.json`，實作後供 acceptance K2 比對（2026-09-11；K2 通過後已刪除）
- [x] 9.16 `tests/unit/services/medical/test_symptom_acceptance.py`：acceptance H 節的檢核清單。實作前 52 紅、18 綠，紅綠分布與失敗原因皆符合 H 節；實作後 70 個全數通過，K3 突變驗證五項皆轉紅（2026-09-12）

## 10. 看診者解析與個人資料套用（依相依順序逐步實作）

本節依相依順序逐項實作。每一項 SHALL 是可獨立驗證、可獨立 commit 的變更；前一項測試未
通過前不得開始下一項。commit 訊息一律使用繁體中文。除明列的整合 task 外，不得順手改變
其他模組行為。

### 10.A 共用人物核心

- [x] 10.1 **鎖定既有行為**：補齊用藥 `resolve_person` 的特徵測試，涵蓋本人別名、姓名唯一命中、姓名加關係縮小範圍、關係唯一命中、同關係多人、找不到與空白姓名；本 task 只加測試，不搬程式、不改行為（2026-09-21；人物解析 39 項、相關用藥範圍 584 項全綠）
- [x] 10.2 **無行為重構**：將 `PersonResolution`、關係別名與 `resolve_person` 搬到共用人物解析模組，用藥服務改為匯入共用實作；10.1 與既有用藥測試結果 SHALL 完全不變（2026-09-21；相關用藥範圍 584 項全綠）
- [x] 10.3 **補齊解析契約**：新增 `display_label`／資料來源，實作姓名與關係衝突時回報不一致、外部 id 不構成命中、未連結他人與多人歧義的明確結果；不得在此 task 接入科別或緊急流程（2026-09-21；共用解析、用藥服務與六語文案 613 項全綠）
- [x] 10.4 **建立資料模型**：定義不可變 `PatientContext` 與值來源 enum，包含 operator、patient kind/id、display label、relationship、age、gender；以純單元測試驗證訊息值高於 profile、profile 高於 unknown（2026-09-21；相關範圍 628 項全綠）
- [x] 10.5 **授權式 context builder**：唯一家庭成員只有在 `FamilyAuthorizationService` 通過 `SENSITIVE READ` 後才可把 profile 寫入 context；未授權時只保留本輪明示資料，`is_care_recipient` 與外部 id 不得放行（2026-09-21；相關範圍 639 項全綠）
- [x] 10.5a **稱謂契約收斂**：稱謂是登入者視角下的單向個人標籤；任何已登入使用者皆可設定自己家庭名單中的成員，不自動覆寫反向關係、不接受本人或非成員為目標，且稱謂不得參與健康資料授權；支援以 `null` 清除稱謂（2026-09-21；家庭、路由、repository 與相關用藥範圍 907 項全綠）
- [x] 10.5b **家庭名單查詢工具**：提供只讀登入者自己家庭名單的 Agent 工具，支援列出全部、依稱謂列出成員及依姓名查稱謂；固定六語回覆直接送出，只使用姓名與稱謂，不呈現或判斷角色、權限、外部 id 或健康資料（2026-09-21；家庭 service、Agent、工具、registry 與六語範圍 1,329 項全綠）

### 10.B 科別推薦接入

- [ ] 10.6 **單一看診者工具參數**：擴充 `suggest_department_for_symptom`，保留症狀原文並加入結構化 `person`、`relationship` 與本輪明示的年齡／性別線索；伺服器端建立 `PatientContext`
- [ ] 10.7 **解析失敗的可見回覆**：`ambiguous` 與姓名／關係衝突時反問且不查症狀表；`not_found` 仍可依本輪資料給一般建議，但不得讀任一家人 profile
- [ ] 10.8 **兒科改讀病人資料**：兒科判斷改讀 `PatientContext.age` 與「寶寶」提示；移除症狀路徑對發話者年齡 ContextVar 的依賴；子女稱謂不得推導年齡
- [ ] 10.9 **性別適用性**：男性本人一般腹痛抑制婦產科、男性替老婆詢問保留婦產科、性別未知不猜測；懷孕／生產／月經／生殖語意及明確指定科別高於 profile
- [ ] 10.10 **跨輪人物延續**：只在同一就醫問題明確延續時解析「他／她」，新的第一人稱問題重設為本人；缺少唯一前文時反問，不永久保存推測人物
- [ ] 10.11 **同句多位看診者**：人物與各自症狀綁定；單一看診者工具須反問先處理哪一位或分別輸出，禁止共用一個全域 `current_patient`
- [ ] 10.12 **科別整合驗收**：加入本人、配偶腹痛／生產、兒子年齡未知、同句多個年齡、姓名／關係衝突、未連結他人、無權限、跨輪代名詞與同句多人端到端測試

### 10.C 緊急流程接入

- [ ] 10.13 **急迫度的人物輸出**：急迫度結果攜帶一個或多個受影響人物、各自事件與 reporter 歸屬；既有 emergency／none 判定與紅卡觸發 SHALL 不變
- [ ] 10.14 **行動先到**：紅卡先送、不等待家庭查詢或人物反問；本人、已解析家人、歧義人物與未連結第三人使用正確或中性稱謂
- [ ] 10.15 **通知正確病人的照顧者**：自動通知改以解析後的 patient 為主體；有效家庭連結中的任一角色可回報，收件人仍依 patient 的 `emergency_detected` 政策選出，且回報不得授予 profile 讀權
- [ ] 10.16 **正確標示回報者**：第三人原文標成 reporter 回報，不得顯示成 patient 本人發言；本人發話才可使用「剛才說」
- [ ] 10.17 **通知結果文案**：依人物種類與 `sent／no_recipient／disabled／failed` 選固定多語文案；任何未送達結果不得宣稱家人已收到
- [ ] 10.18 **防止重複與濫用**：跨人物緊急回報須寫稽核紀錄，並對同一 reporter／patient／事件實作去重與頻率限制
- [ ] 10.19 **緊急整合驗收**：加入「我阿公跌倒」、兩位阿公歧義、路人昏倒、朋友自傷、過去已處理的跌倒、本人與阿公同句急症，以及各種通知結果的端到端測試

### 10.D 完成門檻

- [ ] 10.20 全部人物案例對六種語言的可見文案完成；用藥、科別、急迫度、家人通知與 LINE handler 測試全綠；確認不存在發話者 age／gender 被誤套到其他人的平行路徑
