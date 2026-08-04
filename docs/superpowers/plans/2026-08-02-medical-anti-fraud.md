# Medical Anti-Fraud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CARE Agent 支援醫療場景識詐：Guardrail／Prompt 強制健康與醫療詐騙問題走 RAG，並提供官方種子 URL 清單。

**Architecture:** 不新增 tool／API。擴充 `GuardrailService` 分類語意、`SYSTEM_PROMPT` 硬規則、`get_rag_answer` docstring；種子檔供既有 `ingest_url.py` 使用。

**Tech Stack:** Python 3.12, pytest, LangGraph Agent（既有）

**Work dir:** `/Users/jamessu/Desktop/computersciencehomework/CARE`  
**Branch:** 自 `main` 開 `medical-anti-fraud`（或沿用既有 feature branch）

## Global Constraints

- 繁體中文使用者文件；程式註解維持專案既有風格
- 禁止 monkey patch 改全域；測試用 DI（AsyncMock 注入 Guardrail）
- 不改 whitelist（種子必須通過 `is_allowed_url`）
- 不強制線上 ingest；不做 LIFF／新 API
- DO NOT commit（controller 統一 commit）
- OpenSpec tasks 對應：`openspec/changes/medical-anti-fraud/tasks.md`

---

### Task 1: Guardrail 分類範圍

**Files:**
- Modify: `app/services/guardrail/service.py`
- Modify: `tests/unit/services/guardrail/test_service.py`

**OpenSpec:** 1.1, 1.2

- [ ] **Step 1: 擴充測試** — 在 `test_service.py` 新增：

```python
from app.services.guardrail import service as guardrail_module


def test_classification_prompt_covers_medical_fraud():
    prompt = guardrail_module._CLASSIFICATION_PROMPT
    assert "假藥" in prompt or "詐騙" in prompt
    assert "醫療" in prompt
```

並可選加一個行為測試：mock 回 True，輸入「收到藥局簡訊要我先轉帳才能領藥」，確認 `allow_rag_tool` 為 True 且 invoker 被呼叫（分類器實際判斷由 mock 決定，重點是會進分類而非被短路）。

- [ ] **Step 2:** `pytest tests/unit/services/guardrail/test_service.py -q` — 新測試應 FAIL

- [ ] **Step 3: 更新 `_CLASSIFICATION_PROMPT`**，在相關語意中加入醫療詐騙／假藥／假醫師／假醫院簡訊／以醫療或健保名義要求匯款或點連結等。保持既有健康類別與 fail-open／位置短路行為不變。

建議文案方向（可微調，但測試關鍵字必須命中）：

```python
_CLASSIFICATION_PROMPT = (
    "你是一個訊息分類器。請判斷以下使用者訊息是否與下列主題相關：\n"
    "健康、醫療、身體狀況、疾病、藥物、營養、運動健身、心理健康；\n"
    "或醫療場景詐騙／識詐（例如假藥、假醫師、假醫院或健保相關簡訊、"
    "保證療效的可疑保健話術、因醫療／檢驗／健保／保險理賠名義要求匯款或點擊不明連結）。\n\n"
    "使用者訊息：\n"
)
```

- [ ] **Step 4:** 同檔 pytest 全綠

- [ ] **Step 5:** 不 commit；回報 DONE

---

### Task 2: SYSTEM_PROMPT 與 get_rag_answer docstring

**Files:**
- Modify: `app/services/agent/prompt.py`
- Modify: `app/tools/rag_tools.py`
- Create: `tests/unit/services/agent/test_prompt.py`
- Create: `tests/unit/tools/test_rag_tools_docstring.py`（或併入既有 tools 測試）

**OpenSpec:** 2.1–2.3

- [ ] **Step 1: 寫失敗測試** `tests/unit/services/agent/test_prompt.py`：

```python
from app.services.agent.prompt import SYSTEM_PROMPT


def test_system_prompt_requires_rag_for_health_and_medical_fraud():
    assert "get_rag_answer" in SYSTEM_PROMPT
    assert "詐騙" in SYSTEM_PROMPT or "識詐" in SYSTEM_PROMPT
    assert "165" in SYSTEM_PROMPT
    # 必須查庫（硬規則訊號）
    assert "必須" in SYSTEM_PROMPT and "get_rag_answer" in SYSTEM_PROMPT
```

`tests/unit/tools/test_rag_tools_docstring.py`：

```python
from app.tools.rag_tools import get_rag_answer


def test_get_rag_answer_docstring_mentions_medical_fraud():
    doc = get_rag_answer.description or get_rag_answer.__doc__ or ""
    assert "詐騙" in doc or "假藥" in doc
```

（若 LangChain `@tool` 把說明放在 `.description`，以實際屬性為準。）

- [ ] **Step 2:** pytest 上述兩檔 — FAIL

- [ ] **Step 3: 更新 `SYSTEM_PROMPT`**

在既有規則 1–9 **之後**追加規則 10（並在開頭角色句加入醫療識詐）：

角色句改為類似：

```text
你是 CARE（Clinical Assistance & Resource Engine），
一個專業的健康醫療資訊 AI 助手，並可協助使用者辨識醫療場景相關的詐騙與假藥風險。
你不是執法人員，不代替報案或做法律判定。
```

新增規則 10 大意（須含必須呼叫 get_rag_answer、165、勸阻匯款）：

```text
10. 知識查詢與醫療識詐（非常重要）：
   - 當問題涉及症狀、疾病、用藥、保健、衛教，或疑似醫療詐騙／假藥／假醫師／假醫院簡訊／
     以醫療或健保名義要求匯款或點連結，且本輪已提供 get_rag_answer 工具時，
     你必須先呼叫 get_rag_answer，再依工具結果回答；不得只靠自己知識直接給衛教或識詐結論。
   - 若工具表示無法提供資訊，可簡短說明知識庫暫無，提醒必要時就醫或向官方管道查證。
   - 純寒暄且與健康／醫療識詐無關時，可不呼叫工具。
   - 若使用者正要依可疑醫療訊息匯款或點不明連結，必須強烈勸阻，並提示可向 165 反詐騙諮詢專線等官方管道查證。
```

保留規則 5–9（位置、RAG 前綴、來源、院所）不變。

- [ ] **Step 4: 更新 docstring**

```python
async def get_rag_answer(query: str) -> str:
    """當問題需要引用醫療知識庫（必要時會補充允許網域的公開網路資料）時呼叫。
    例如疾病照護、症狀處置、慢病管理，以及醫療詐騙／假藥／可疑醫療訊息查證。
    """
```

- [ ] **Step 5:** pytest 相關測試全綠；不 commit；回報 DONE

---

### Task 3: 種子 URL 清單與驗證測試

**Files:**
- Create: `resources/medical_anti_fraud_seed_urls.txt`
- Create: `tests/unit/resources/test_medical_anti_fraud_seed_urls.py`

**OpenSpec:** 3.1, 3.2

- [ ] **Step 1: 種子檔**（至少 3 筆，皆須 `is_allowed_url` 通過）。建議：

```text
# 醫療打詐／假藥相關官方頁（以 scripts/ingest_url.py 逐筆 ingest）
# 用法範例：
#   .venv/bin/python scripts/ingest_url.py --url "<URL>" --source-name "<名稱>"
https://165.npa.gov.tw/
https://www.fda.gov.tw/
https://www.mohw.gov.tw/
https://www.hpa.gov.tw/
```

若實測某 URL 非 http(s) 或非白名單，改成其他 `*.gov.tw` 穩定頁。

- [ ] **Step 2: 測試**

```python
from pathlib import Path
from app.services.rag.whitelist import is_allowed_url

SEED = Path(__file__).resolve().parents[3] / "resources" / "medical_anti_fraud_seed_urls.txt"


def test_seed_urls_exist_and_allowed():
    assert SEED.is_file()
    urls = [
        line.strip()
        for line in SEED.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(urls) >= 3
    for url in urls:
        assert url.startswith("http://") or url.startswith("https://")
        assert is_allowed_url(url), url
```

（依 repo 實際 `tests/unit/...` 深度調整 `parents[N]`，或改用專案根：`Path(__file__).resolve().parents[...]` 對到含 `resources/` 的 CARE root。）

- [ ] **Step 3:** pytest 該檔全綠；不 commit；回報 DONE

---

### Task 4: 全量驗證

**OpenSpec:** 4.1（4.2 由 controller commit）

- [ ] 跑：`.venv/bin/python -m pytest -c pytest.ini tests/unit/services/guardrail tests/unit/services/agent/test_prompt.py tests/unit/tools/test_rag_tools_docstring.py tests/unit/resources/test_medical_anti_fraud_seed_urls.py -q --tb=short`
- [ ] 若環境允許再跑更廣的 `tests/unit -q`
- [ ] 勾選 `openspec/changes/medical-anti-fraud/tasks.md` 已完成項
- [ ] 回報 DONE；不 commit
