# CARE controlled diagnostics

User-approved scope: retain the original 600 records, diagnose queueing/timeouts with a fixed small cohort and lower concurrency, then run a separate controlled comparison only when stable. No filtering failed cases.

1. Add optional offline timing for embedding, PG connection acquisition, PG SQL, Mongo content fetch/BM25, semaphore wait and event-loop lag. Preserve prompts, models, retrieval budget and existing deadlines.
2. Test transparent wrappers, cancelled queue waits, probe cleanup, and the gate. Run ./init.sh without external integration using dummy credentials. Independent review before paid execution.
3. Use the original eight pilot IDs HN001/HN002/HN005/HN033/HN036/HN047/HN066/HN094, not a selection based on new successes. Run each B/D once at concurrency1, then concurrency4 (32 diagnostic results). Keep every row. This is infrastructure diagnosis, not an unbiased effectiveness sample.
4. Use one new observed live baseline across diagnostics and controlled rerun; pause care-etl, reject drift, retain the original experiment untouched. All model/prompt/budget/deadline settings remain pinned; only concurrency and measurement differ.
5. Gate: all 16 low-concurrency rows present, no runtime errors, no incident/runtime_error statuses, and physical pre/post verification success. KB_EMPTY and MODEL_REFUSE alone do not fail the infrastructure gate. No repeat-until-pass or selective retries.
6. If gated, run all original100 × B/D ×3 at concurrency1 under a new identity/journal, retaining failures. If not gated, stop before full rerun and report diagnostics for investigation.
7. A persistent Kubernetes sequence Job saves each stage and gate. Resume original ETL schedule when the sequence completes; failures remain paused for investigation, per the established recovery policy. No paid duplicate submission on restart.
