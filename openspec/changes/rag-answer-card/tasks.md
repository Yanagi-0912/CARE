## 1. 大小防線（先做，判定卡的線上風險靠它擋）

- [ ] 1.1 `resources/flex_messages/size_guard.py`：`wire_bytes(bubble: dict) -> int` 以 `json.dumps(bubble).encode()` 計算（預設 `ensure_ascii=True`，與 `linebot/v3/messaging/rest.py:155` 一致）；`fits(bubble, limit=SAFE_BUBBLE_BYTES) -> bool`；`SAFE_BUBBLE_BYTES = 9 * 1024`（LINE 上限 10 KB，保留約 10% 餘裕）
- [ ] 1.2 `app/tools/claim_tools.py::_to_flex_message_text`：組完卡片後先過 `fits()`，不合格即走既有的 `_format_verdict_reply` 純文字路徑；與現有 `except Exception` 的 fallback 併為同一個出口
- [ ] 1.3 測試 `tests/unit/services/line_messaging/flex/test_size_guard.py`
      - `test_wire_bytes_counts_escaped_non_ascii`：中文字算 6 bytes 而非 3，鎖住「不可用未轉義 UTF-8 計算」
      - `test_fits_rejects_at_threshold`：門檻邊界
- [ ] 1.4 測試 `tests/unit/tools/test_claim_tools.py`
      - `test_oversized_verdict_card_falls_back_to_text`：`related_info` 灌到超過門檻 → 回傳純文字判定而非 Flex JSON
      - `test_normal_verdict_card_stays_flex`：正常大小仍是 Flex，確認防線沒有誤殺

## 2. 結構化來源

- [ ] 2.1 `app/core/rag_sources.py`：`SourceRef(index, label, url)` dataclass 與 `get_request_rag_sources` / `set_request_rag_sources` / `reset_request_rag_sources`，形狀比照 `app/core/user_font_size.py`
- [ ] 2.2 `app/services/rag/answer_service.py::_append_sources`：組 `source_lines` 的同一個迴圈裡一併收集 `SourceRef`，收完存進 ContextVar；`source_lines` 為空時存空 list（不得沿用上一輪殘留）
- [ ] 2.3 `app/services/line_messaging/handler/message_handler.py`：在既有的 `set_request_font_size` 旁一併重設 RAG 來源 ContextVar，`finally` 一併 reset
- [ ] 2.4 測試 `tests/unit/services/rag/test_answer_service.py`
      - `test_structured_sources_match_text_numbering`：結構化來源的 index 與文字清單的 `[n]` 逐筆對應
      - `test_structured_sources_empty_when_no_citation`：模型未輸出引用編號 → 結構化來源為空
      - `test_structured_sources_keep_url_verbatim`：url 未經改寫

## 3. 卡片 builder

- [ ] 3.1 `app/services/line_messaging/flex/rag_answer_flex.py`：`build_rag_answer_flex(question, body, sources, ft)`——header「衛教資訊」、問句塊、答案本文、separator、section title、footer 來源按鈕（`FlexTheme.secondary_button` + URI action）；所有 size 取自傳入的 `FlexTheme`，不得寫死
- [ ] 3.2 同檔 `build_document_answer_flex(question, body, ft)`：無來源區段與 footer，header 文案區隔為「文件內容問答」
- [ ] 3.3 前綴剝除 helper：比照 `app/i18n/messages.py::all_sources_headings()` 的做法列出所有語言的 RAG 前綴，剝除首行
- [ ] 3.4 測試 `tests/unit/services/line_messaging/flex/test_rag_answer_flex.py`
      - `test_font_size_scales_all_text_nodes`：`normal`／`large`／`xlarge` 三種字級各產一張，斷言每個 text 節點的 size 與 `_SIZE_SCALE` 對應欄位一致——**本次功能的核心斷言**
      - `test_source_buttons_use_uri_action_with_verbatim_url`
      - `test_no_sources_means_no_footer`
      - `test_document_card_has_no_source_section`
      - `test_rag_prefix_stripped_for_every_language`

## 4. 接線與降級

- [ ] 4.1 `app/services/agent/agent.py::invoke`：回傳新增 `answer_kind`（`"rag"` / `"document"` / `None`），依本輪 ToolMessage 判定；`is_rag_fail()` 為真時 SHALL 為 `None`
- [ ] 4.2 `app/services/line_messaging/handler/message_handler.py`：把 `answer_kind` 傳給 `replier.reply()`；`save_turn(ai_reply=...)` 仍存純文字（確認不受影響）
- [ ] 4.3 `app/services/line_messaging/reply/reply.py`：`reply()` 新增 `answer_kind` 參數；非 `None` 時嘗試組卡，`fits()` 不過或 builder 拋例外即退回純文字；卡片分支一併呼叫 `_append_tts_audio_message`，合成文字取組卡前的純文字
- [ ] 4.4 測試 `tests/unit/services/agent/test_agent.py`
      - `test_invoke_reports_answer_kind_rag`
      - `test_invoke_reports_none_answer_kind_for_rag_failure`：`[RAG_ERR:...]` → `None`
- [ ] 4.5 測試 `tests/unit/services/line_messaging/test_reply.py`（依 config 規則以 DI 傳入 mock replier／tts，不使用 monkey patch）
      - `test_rag_answer_kind_sends_flex`
      - `test_oversized_rag_card_falls_back_to_text`
      - `test_builder_exception_falls_back_to_text`
      - `test_flex_branch_appends_audio_when_voice_enabled`
      - `test_flex_branch_skips_audio_when_voice_disabled`
      - `test_quick_reply_still_on_last_message`：位置 Quick Reply 行為未變

## 5. 答案長度上限

- [ ] 5.1 `app/services/rag/answer_prompts.py`：知識庫與 web fallback 兩條生成路徑都加入字數上限指示（400–500 字）；不以截斷實作
- [ ] 5.2 測試 `tests/unit/services/rag/test_answer_prompts.py`
      - `test_answer_prompt_contains_length_limit`：兩條路徑的 prompt 都含上限指示
- [ ] 5.3 跑 `evals/rag` 的 golden set 觀察答案長度與品質是否因長度上限而退步；若 recall／引用正確率下降則回頭調整上限值，並把觀察到的長度分布補進 design.md（該資料目前缺席，見 design.md「已知的證據缺口」）

## 6. 收尾

- [ ] 6.1 `./init.sh` 全綠（所有 pytest 通過）
- [ ] 6.2 真機確認三種字級的卡片外觀，以及來源按鈕可正常開啟瀏覽器
- [ ] 6.3 `openspec archive rag-answer-card`
