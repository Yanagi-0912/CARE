# CLAUDE.md - Claude Code Instructions

This file serves as the system rules and operational manual for Claude. Read this file at the start of every session before making any changes.

## 🧭 Order of Operations (Start of Session)
Before writing any code or making modifications, you MUST execute these steps in order:
1. **Check Environment Health**: Run `./init.sh` (or `.\init.ps1` on Windows PowerShell) to ensure the project installs cleanly and the baseline checks/tests pass. If the initialization script fails, STOP and fix the environment before proceeding with new features.
2. **Read Session Progress**: Read `claude-progress.md` to understand what was done in previous sessions, the current verified state, and the immediate next steps.
3. **Select Your Task**: Read `feature_list.json` to identify the highest priority feature that is NOT completed (e.g., status is `not_started` or `in_progress`).
   - You may work on **ONLY ONE** feature at a time.
   - If a feature is already `in_progress`, continue working on it.
   - If no feature is `in_progress`, select the next `not_started` feature (ordered by priority, lowest integer first) and change its status to `in_progress` in `feature_list.json` before starting work.

## 🛠️ Work Discipline & Guidelines
During active coding:
- **Incremental Changes**: Make small, incremental modifications. Do not write massive chunks of untested code.
- **Focus**: Stay strictly within the scope of the single feature you are working on. Do not fix unrelated bugs or add unrequested helper code unless it is directly required.
- **Test-Driven Verification**: Every feature must have a concrete, reproducible verification path. Write automated tests if applicable, or define clear manual validation steps.

## 🏁 Definition of Done (Do Not Modify This Section)
<!-- CRITICAL: DO NOT MODIFY THE FOLLOWING DEFINITION OF DONE PROTOCOL -->
A feature is only considered "Done" and can be set to `passing` in `feature_list.json` when the following conditions are met:
1. **Verification Action**: You have executed the specific `verification` steps outlined for the feature.
2. **Recorded Evidence**: You must record the raw, successful terminal output, test results, or screenshots/logs inside the `evidence` field in `feature_list.json`. Verbal claims like "I have tested it and it works" are NOT acceptable.
3. **Green Build**: `./init.sh` (or `.\init.ps1`) runs successfully, and all existing and new tests pass.
4. **Clean Commit**: Create a clean git commit with a descriptive message outlining the change.
<!-- END OF DEFINITION OF DONE PROTOCOL -->

## 🧹 Session Wrap-up & Clean State Checklist
Before concluding your session, you must:
1. Ensure the repository is in a clean state (no untracked draft files, no broken builds).
2. Update `claude-progress.md` with a session log entry under "會話記錄", summarizing:
   - What you planned to do (本輪目標)
   - What you actually completed (已完成)
   - The verification performed and evidence recorded (執行過的驗證 & 已記錄證據)
   - Any git commit hashes generated (提交記錄)
   - Known risks or unresolved issues (已知風險或未解決問題)
   - The best next action for the next session (下一步最佳動作)
3. Set the feature status in `feature_list.json` to `passing` if completed and verified, or `blocked`/`not_started` if you are pausing work. Ensure no more than one feature remains `in_progress`.
