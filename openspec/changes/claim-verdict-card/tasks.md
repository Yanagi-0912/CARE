## 1. 先量測，再決定要不要做

- [ ] 1.1 **覆蓋率**：從 TFC 已入庫文章的 `claim` 反向出題（每篇改寫成 2 種口語問法），量測「能檢索回原文」的比例。結果寫入 `openspec/changes/claim-verdict-card/coverage.md`
- [ ] 1.2 **門檻校準**：以 1.1 的正樣本（同篇改寫）與負樣本（同主題不同主張）掃 `CLAIM_MATCH_MIN_SCORE`，取誤配率為 0 的最低門檻；同樣記入 `coverage.md`
- [ ] 1.3 **決策點**：覆蓋率低於 30% 則停止本 change，於 proposal.md 補記結論並重新評估優先序

## 2. 索引與設定

- [ ] 2.1 ~~建立 claim 向量索引~~ **不需要**（design 決策 2）：沿用既有 `MONGODB_VECTOR_INDEX`，在結果中挑 `verdict` 非空者
- [ ] 2.2 `app/core/config.py` 與 `.env.example` 新增 `CLAIM_MATCH_MIN_SCORE`、`CLAIM_VERIFICATION_ENABLED`
- [ ] 2.3 檢索失敗時降級為「證據不足」路徑，不拋錯（對齊 `RAG_HYBRID_ENABLED` 的 fail-open）

## 3. 主張正規化

- [ ] 3.1 `app/services/rag/claim_verification/normalizer.py`：Gemini structured output，輸入使用者問句、輸出可查核主張
- [ ] 3.2 正規化失敗時以原問句續行，不中斷流程
- [ ] 3.3 `tests/unit/services/rag/test_claim_normalizer.py`：包裝詞剝除、多主張只取主要者、失敗降級

## 4. 已查核主張比對

- [ ] 4.1 `app/services/rag/claim_verification/matcher.py`：以正規化主張向量檢索 `claim` 欄位，先以 `url` 去重再取最高分
- [ ] 4.2 分數低於 `CLAIM_MATCH_MIN_SCORE` 一律視為未命中
- [ ] 4.3 `tests/unit/services/rag/test_claim_matcher.py`：命中、低於門檻、同篇多 chunk 去重、索引缺失降級

## 5. 判定產生

- [ ] 5.1 `app/services/rag/claim_verification/service.py`：命中則回傳 TFC 的 `verdict`；未命中則固定為「證據不足」
- [ ] 5.2 理由由 LLM 依查核報告內容改寫；**斷言 LLM 輸出不含判定值**，判定僅由 `verdict` 欄位帶出
- [ ] 5.3 未命中時執行一般證據檢索，結果放入獨立的「相關衛教資訊」欄位，不進入判定推導
- [ ] 5.4 `tests/unit/services/rag/test_claim_verification.py`：五種 verdict 各一、未命中、LLM 失敗降級、**LLM 回傳的判定值一律被忽略**

## 6. Agent tool 與接線

- [ ] 6.1 `app/tools/claim_tools.py` 的 `verify_claim`；docstring 明確區分與 `get_rag_answer` 的適用問句形態
- [ ] 6.2 `app/tools/registry.py` 納入；`CLAIM_VERIFICATION_ENABLED` 為 false 時不提供
- [ ] 6.3 `app/dependencies.py` 組裝 `ClaimVerificationService`
- [ ] 6.4 `tests/unit/tools/test_claim_tools.py`、更新 `test_registry`

## 7. Flex 判定卡

- [ ] 7.1 `app/services/line_messaging/flex/verdict_card.py`：判定、**使用者原問句**、理由、來源連結（design 決策 8：不顯示知識庫的 claim）
- [ ] 7.2 配色：錯誤／部分錯誤／證據不足／正確用語意色，事實釐清用中性色（design 決策 6）
- [ ] 7.3 標示判定出自台灣事實查核中心並附原文連結（design 決策 5）
- [ ] 7.4 `tests/unit/services/line_messaging/flex/test_verdict_card.py`：五種 verdict 的渲染、缺少 url 時的降級、純文字 fallback

## 8. Eval

- [ ] 8.1 `app/services/rag/eval_scoring.py`：`EvalCase` 新增可選 `expected_verdict`
- [ ] 8.2 新增判定正確率與誤配率兩個指標；誤配定義為「回了判定但主張不對應」
- [ ] 8.3 `evals/rag/golden.jsonl` 補查核型題目，含各 verdict 與應未命中的題目
- [ ] 8.4 `tests/unit/eval/test_rag_eval_scoring.py` 對應測試

## 10. 主張同一性驗證（design 決策 9）

- [ ] 10.1 `app/services/rag/claim_verification/identity.py`：LLM 判斷兩則主張是否同一件事
- [ ] 10.2 **fail-closed**：例外、逾時、無法解析一律視為不同主張
- [ ] 10.3 `ClaimVerificationService` 在命中後呼叫，判定為不同主張時走未命中路徑
- [ ] 10.4 `CLAIM_MATCH_MIN_SCORE` 回到 0.86（決策 9：召回交給門檻，精確交給驗證）
- [ ] 10.5 測試：同一主張、不同主張、驗證失敗降級、**驗證器不得回傳判定值**
- [ ] 10.6 以負樣本重測誤配率，結果記入 `coverage.md`

## 9. 收尾

- [ ] 9.1 `./init.sh` 全綠
- [ ] 9.2 更新 `openspec/specs` delta（`claim-verification` 新增、`agent-architecture` 與 `rag-eval` 修改）
- [ ] 9.3 清楚的 git commit 與 PR
