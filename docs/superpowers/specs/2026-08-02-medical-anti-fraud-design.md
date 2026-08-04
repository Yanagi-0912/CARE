# Medical Anti-Fraud（醫療打詐）Design

**Date:** 2026-08-02  
**OpenSpec:** `openspec/changes/medical-anti-fraud/`

## Summary

CARE 維持健康醫療助手，並擴充「醫療場景識詐」：假藥、假醫師、假醫院簡訊、保證療效、以醫療／健保名義要求匯款或點連結。答案走既有 `get_rag_answer`（CRAG＋白名單 web fallback），不新增獨立打詐 tool。

## Approach

1. **Guardrail**：分類提示涵蓋醫療詐騙語意 → `allow_rag=True`。
2. **SYSTEM_PROMPT**：角色＋「健康／識詐且工具已提供時必須先查 RAG」＋非執法／勸阻匯款／提示 165 等官方管道。
3. **Tool docstring**：`get_rag_answer` 明示醫療識詐查證。
4. **種子 URL**：`resources/medical_anti_fraud_seed_urls.txt`，用既有 ingest CLI 補庫。

## Non-Goals

一般打詐 bot、執法 API、強制線上 ingest、LIFF 改版、改白名單（除非種子 URL 需要）。

## Verification

單元測試覆蓋 prompt／guardrail 字串與種子 URL 白名單；`pytest` 全綠。
