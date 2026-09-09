## 1. 先量測，再決定要不要做

**本節完成前不得進入第 2 節。**

- [ ] 1.1 **急症召回率**：整理急症敘述測試集（真實口語形式，例如「我阿公昏迷」「我剛剛被車撞，現在流好多血」「胸口悶還喘不過氣」），以及必須**不**觸發的對照組（「中風前兆有哪些」「食物中毒可以吃什麼」「中風的保險理賠」），量測判斷器的漏放率與誤報率。結果寫入 `openspec/changes/symptom-department-guidance/coverage.md`
- [ ] 1.2 **對照表覆蓋率**：以人工審定後的表為準，從常見口語症狀問句（每條目改寫 2 種說法，含「肚子痛」這類表內不存在的口語詞）量測命中率；同樣記入 `coverage.md`
- [ ] 1.3 **門檻校準**：以 1.2 的正樣本（同條目改寫）與負樣本（相鄰但不同科的症狀）掃 `SYMPTOM_MATCH_MIN_SCORE`，取誤配率為 0 的最低門檻
- [ ] 1.4 **決策點**：急症漏放率不為 0 → **停止本 change**；覆蓋率低於 40% → 於 proposal.md 補記結論並重新評估優先序

## 2. 資料整備（人工，非程式）

- [ ] 2.1 `resources/symsptom_department_table/` 更名為 `resources/symptom_department_table/`（修正拼字）
- [ ] 2.2 人工審定 `vghtpe_yuli_department_schema.json`：逐條確認科別、改寫病症描述（design 決策 11）、移除院所特有分科（傳統醫學科、疼痛科）與非科別值（`15歲以下兒童` 應改為兒科）
- [ ] 2.3 修正來源錯字（「打曀」「穿恐」），處理 `vghtpe_yuli_review.json` 的 10 筆待複查條目
- [ ] 2.4 **重建 `emergency` 標註**：不沿用爬蟲的關鍵字初篩結果（design 決策 3），逐條人工定案
- [ ] 2.5 審定完成的條目 `confidence` 改為 `verified`；未審定者維持 `unverified` 並**不得**進入線上表
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

- [ ] 5.1 `app/services/medical/symptom_classification/symptom_table.py`：載入審定後的表，**載入時對每個科別呼叫 `resolve_department()`，任一條無法解析即拋錯**（design 決策 4）
- [ ] 5.2 `confidence` 非 `verified` 的條目載入時略過
- [x] 5.3 `app/services/medical/symptom_classification/symptom_department_service.py`：正規化 → 比對 → 建議。**本服務不做急迫度判斷**（design 決策 3）
- [ ] 5.4 低於 `SYMPTOM_MATCH_MIN_SCORE`、或候選超過 3 個 → 走保底建議（家醫科／一般內科 + 無法判斷說明），SHALL NOT 取最接近的一條（design 決策 6、7）
- [ ] 5.5 輸出含免責、就醫提示與來源標示；用語 SHALL NOT 為診斷語氣（design 決策 8）
- [ ] 5.6 **SHALL NOT 呼叫 `request_location_quick_reply`**（design 決策 9）
- [ ] 5.7 `tests/unit/services/medical/test_symptom_department_service.py`：命中單一候選、命中多候選、超過 3 個候選走保底、未命中走保底、**斷言輸出的科別皆為部定專科**、**斷言不觸發位置請求**

## 6. Agent tool 與接線

- [ ] 6.1 `app/tools/medical_tools.py` 新增 `suggest_department_for_symptom`；docstring 明確區分與 `get_rag_answer` 的適用問句形態（問掛號 vs 問衛教）
- [ ] 6.2 `app/tools/registry.py` 納入；`SYMPTOM_DEPARTMENT_ENABLED` 為 false 時不提供
- [ ] 6.3 `app/services/agent/prompt.py`：於工具優先順序新增分流規則與反例；**明確保留「純症狀敘述無掛號意圖 → 仍走 (g) `get_rag_answer`」**
- [ ] 6.4 `app/dependencies.py` 組裝 `SymptomDepartmentService`
- [ ] 6.5 `app/core/config.py` 與 `.env.example` 新增 `SYMPTOM_DEPARTMENT_ENABLED`（default false）。**`SYMPTOM_MATCH_MIN_SCORE` 不進 env**——門檻與維度是對「特定模型 × 特定版本對照表」校準的，跨環境調整沒有意義且危險，改為模組常數與向量檔一同版控（design 決策 12）
- [ ] 6.6 `tests/unit/tools/test_medical_tools.py` 擴充、`tests/unit/tools/test_registry.py` 更新（含旗標關閉時不納入）

## 7. 迴歸（確認既有行為未變）

- [ ] 7.1 `tests/unit/services/medical/test_department_matcher.py`：確認別名表未新增任何症狀詞
- [ ] 7.2 `tests/unit/services/medical/test_llm_term_resolver.py`：確認「肚子痛」等症狀輸入仍回 `UNKNOWN`（既有 case，不得因本 change 放寬）
- [ ] 7.3 `tests/unit/services/agent/`：「附近有腸胃科嗎」「我牙齒痛」「附近有醫院嗎」三條既有路徑行為不變
- [ ] 7.4 旗標關閉時，全部既有測試與本 change 之前完全相同

## 8. 收尾

- [ ] 8.1 `coverage.md` 補上最終數字（急症漏放率／誤報率、覆蓋率、門檻）
- [ ] 8.2 `./init.sh` 全綠（Windows：`.\init.ps1`）
- [ ] 8.3 `department_matcher.py` 與 `llm_term_resolver.py` 的模組註解補一行：症狀邏輯已移至 `symptom_classification`，此兩處紅線不變
- [ ] 8.4 建立 git commit / PR（繁體中文描述），合併後 `openspec archive symptom-department-guidance`
