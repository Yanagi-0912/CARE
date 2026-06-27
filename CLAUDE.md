# CLAUDE.md - AI Instructions Manual

This file serves as the system rules and operational manual for AI assistants. Read this file at the start of every session before making any changes.

## 🧭 Order of Operations (Start of Session)
Before writing any code or making modifications, AI assistants MUST execute these steps in order:
1. **Check Environment Health**: Run `./init.sh` (or `.\init.ps1` on Windows PowerShell) to ensure the project installs cleanly and the baseline checks/tests pass.
2. **Read Session Progress**: Read `claude-progress.md` to understand the current verified state and immediate next steps.
3. **Select Your Task**: Read `feature_list.json` to identify the highest priority feature that is NOT completed (e.g., status is `not_started` or `in_progress`).
   - Work on **ONLY ONE** feature at a time.
   - If no feature is `in_progress`, select the next `not_started` feature and change its status to `in_progress` before starting.

## 🛠️ Work Discipline & Guidelines
During active coding:
- **Incremental Changes**: Make small, incremental modifications. Do not write massive chunks of untested code.
- **Focus**: Stay strictly within the scope of the single feature you are working on.
- **Test-Driven Verification**: Every feature must have a concrete, reproducible verification path. Run the automated tests to verify.
- **Testing Discipline**: 寫測試時禁止使用 monkey patch (如 `unittest.mock.patch` 修改全域/別處導入之實例)，請使用依賴注入 (Dependency Injection) 的寫法傳入 mock 實例。

## 🏁 Definition of Done
A feature is only considered "Done" and can be set to `passing` in `feature_list.json` when:
1. **Verification Action**: You have executed the specific `verification` steps outlined for the feature.
2. **Recorded Evidence**: Record successful terminal output, test results, or logs inside the `evidence` field in `feature_list.json`.
3. **Green Build**: `./init.sh` (or `.\init.ps1`) runs successfully, and all existing and new tests pass.
4. **Clean Commit**: Create a clean git commit with a descriptive message outlining the change.

## 💻 Standard Commands
- **Initialize & Verify Environment**: `./init.sh` (Linux/Mac) or `.\init.ps1` (Windows PowerShell)
- **Run All Tests**: `python -m pytest tests/`
- **Run Single Test File**: `python -m pytest tests/unit/services/rag/services/test_rag_answer_service.py`
- **Run Backend Local Server**: `uvicorn app.main:app --port 8000 --reload --reload-exclude .venv`
