# Lumielle Law Assistant v0.6.0 Implementation Plan

> **For agentic workers:** Execute directly in the approved isolated worktree. Use test-first slices; do not wait for another design approval. Track each task in this plan and the per-plan ledger.

**Goal:** Deliver the integrated v0.6.0 scheduler, study-content lifecycle, administration, pagination and Plugin Page cycle on schema v13, then push a fast-forward update to `main` only if it still has the approved base.

**Architecture:** `command / LLM Tool / Scheduler / Plugin Page -> LawAssistantService -> focused service/repository -> SQLite / Publisher / source adapters`. The 12→13 migration is sequential and transactional. Independent scan and local due-work loops use persistent occurrence/reveal rows. Safe DTO and operator DTO paths stay separate.

**Tech Stack:** Python 3.12, SQLite stdlib, pytest/pytest-asyncio, Ruff, vanilla JavaScript/Node test runner, AstrBot 4.22.0 and 4.25.0 exact-source smoke.

**Spec:** `docs/superpowers/specs/2026-09-29-law-assistant-v0-6-design.md` and the approved Phase 0.6.0施工单.

## Global Constraints

- Minimum AstrBot version stays `>=4.22.0,<5`; no Core edits or deprecated APIs.
- Schema version becomes 13 through one transactional 12→13 migration; no production DB or real corpus changes.
- Commands, Tools, Scheduler and Web API delegate business decisions to `LawAssistantService`.
- Candidate identity, source evidence, answers, official case identity, and send/idempotency truth are deterministic; LLM cannot promote or invent them.
- Normal DTOs remain answer-safe; operator management requires existing Admin/operator authorization.
- All group sends and persistent-plan changes retain preview/confirmation boundaries; exact IDs/bodies are locked before confirm.
- Scheduler scan interval never drives daily due timing; only today's local date may catch up.
- No secrets, user DB, real PDF/JSON, cache, tests, or local runtime files in the package.
- No tag, GitHub Release, force push, history rewrite, global Git/proxy/credential changes, real QQ send, or live LLM dependency.
- Every behavior change has deterministic tests; final checks include Python, Ruff, Node, AstrBot 4.22/4.25, package smokes and diff validation.
- Any remote `main` movement from `1c0718251e63f0c1841e32e8e6eb85793b1beb9c` is a stop-and-report condition.

## Review Focus

- A crash during a multi-page answer send must not resend a page with known success or silently claim uncertain delivery.
- A batch token must not apply to a changed row set, another operator, another action, or data changed after preview.
- Clearing one data scope must preserve all unrelated rows and leave schema v13 reopenable with valid foreign keys.
- A missing/unverified answer must remain `None`/unverified and must never be inferred; explicit operator identity promotion is separate, while identity collision or stale state remains a per-item failure.
- A local-time task crossing midnight, DST gap/fold, restart, or plan edit must not run for the wrong date/target or duplicate a completed occurrence.

---

### Task 1: Schema v13 transactional foundation

**Files:** `storage.py`, `daily_plans.py`, `tests/test_storage.py`, `tests/test_content_and_plans.py`, `service.py`, version/schema assertions.

**Interfaces:** Consumes existing `SQLiteStorage._initialize_schema()` and schema 12. Produces `SCHEMA_VERSION=13`, migration 12→13, durable reveal/radar/lifecycle columns and updated overview/version contracts.

- [x] Add `test_fresh_database_initializes_schema_13_reveal_and_review_defaults`, v12 fixture preservation/reopen idempotence, future-version refusal at 13, and injected-failure DDL/version rollback regressions.
- [x] Run the new migration tests; expected/current baseline schema returned 12 and had no 12→13 migration.
- [x] Implement exact columns/tables from the design document with conservative defaults, indexes, uniqueness, status constraints and FK actions.
- [x] Update runtime schema assertions and verify old data remains unchanged after migration.
- [x] Run `pytest tests/test_storage.py tests/test_content_and_plans.py tests/test_library_repository.py tests/test_web_api.py -q`; expected: all pass and every DB reports v13.

### Task 2: Reveal-job repository and DailyPlan contract

**Files:** `daily_plans.py`, `storage.py`, `question_session_repository.py`, new `scheduled_reveals.py`, `tests/test_storage.py`, `tests/test_daily_resolver.py`.

**Interfaces:** `DailyPlan` adds question-only `question_reveal_mode`, answer delay and explanation delay; `ScheduledRevealRepository.create_for_session(...)`, `claim_due(...)`, `mark_page_sent(...)`, `finish(...)`, `skip(...)`, `list_pending(...)` are idempotent per prompt and reveal kind, validate transitions and preserve snapshot/target identity.

- [x] Test defaults, invalid reveal settings, unique `(session_id,reveal_kind)`, exact hash/UMO, state transitions, page progress across reopen and manual skip behavior.
- [x] Run focused tests; expected: repository module was absent, and duplicate claim initially re-returned an in-flight job.
- [x] Implement the typed model and focused repository; no answer text is copied into reveal rows.
- [x] Run `pytest tests/test_scheduled_reveals.py tests/test_daily_resolver.py tests/test_storage.py tests/test_content_and_plans.py -q`; expected: all 48 pass.

### Task 3: Independent wakeable Scheduler

**Files:** `scheduler.py`, `service.py`, `main.py`, `tests/test_scheduler.py`, `tests/test_plugin.py`.

**Interfaces:** `LawAssistantScheduler` owns scan and due tasks separately; `wake()` starts/recomputes due work; `LawAssistantService.next_due_at(now)` and `process_due_work(now)` are deterministic service entrypoints. `run_scheduled_jobs()` remains a compatibility facade for scan/reminder behavior only.

- [x] Add fake-clock tests for immediate due evaluation, nearest due wait, wake after plan commit, same-day catch-up, prior-day exclusion, repeated start, cancel/await both loops, independent scan interval, and exception survival.
- [x] Verify tests fail on existing one-loop interval scheduler.
- [x] Implement separate task lifecycle and time resolver with configured `ZoneInfo`; DST gap advances to first valid minute and fold uses first occurrence.
- [x] Run service/scheduler resolver regressions; latest combined session/scheduler/service/storage run → 95 passed.

### Task 4: Daily content send and scheduled staged reveal

**Files:** `service.py`, `question_session.py`, `scheduled_reveals.py`, `publisher.py` (only if needed), `tests/test_scheduler.py`, `tests/test_question_session.py`, `tests/test_content_and_plans.py`.

**Interfaces:** Daily delivery uses existing shared selection/snapshot renderer; after confirmed prompt send it creates the target-scoped session then due jobs from actual `sent_at + configured delay`. `process_due_reveals(now)` consumes only the stored snapshot and exact target UMO.

- [x] Test prompt failure creates neither active session nor reveal job; delayed answer/explanation are independent; manual reveal and closed session skip; duplicate day is idempotent.
- [x] Test multi-page delivery persists each successful page, known failure is visible, ambiguous in-flight is `needs_review`, and no prior-day job is sent.
- [x] Implement service/scheduler operations; isolate one target/job failure and retain per-target history diagnostics.
- [x] Run broader focused suite and add a safe scheduled-reveal history diagnostics regression before closing this task.

### Task 5: Truth-safe inventory promotion, soft delete and mock selection

**Files:** `library_models.py`, `library_repository.py`, `library_service.py`, `learning_service.py`, `storage.py`, `service.py`, `tests/test_library_service.py`, `tests/test_learning_inventory.py`, `tests/test_library_repository.py`.

**Interfaces:** Operator-only `get_learning_item_for_management()` returns editable fields; `soft_delete_items()/restore_items()` are reversible; `promote_candidate()` validates stored structured payload, reconciles via `RealQuestion.from_mapping`, persists exact binding and returns explicit per-item result. Mock `origin` uses injected `rng.choice` and exact session origin for no-repeat.

- [x] Add tests for unauthorized/safe DTO separation, soft delete/restore preserving evidence/history, explicit candidate promotion, absent answers remain absent, identity collision, partial per-item failure, idempotent reconciliation, strict origin/subject/type and random mock choice/no-repeat.
- [x] Verify the promotion boundary is explicit and deterministic: candidate identity changes only after authorized confirmation; missing answers remain missing and imported/LLM content does not self-verify.
- [x] Implement narrow repository/service methods; imports and LLM output never set verified identity.
- [x] Run full library/inventory/product regression coverage; safe reads remain unchanged and lifecycle tests pass.

### Task 6: Radar review and operator status overlay

**Files:** `date_parser.py`, `extraction.py`, `activity_radar.py`, `storage.py`, `service.py`, `web_api.py`, `tests/test_date_parser.py`, `tests/test_activity_radar.py`, `tests/test_service.py`, `tests/test_web_api.py`.

**Interfaces:** Date parsing returns evidence-bound candidate plus review status; `set_event_status_override(event_id,status,actor,reason)` overlays display status only; `prepare_event_date_review(...)` / `confirm_event_date_review(token,actor)` preserve source evidence and audit decisions.

- [x] Test trusted publication-year precedence, arbitrary body year not used, month/day with no trusted year stays review-only, unconfirmed deadline never drives reminder/publication, and manual override never confirms evidence.
- [x] Test actor/reason audit, stale/foreign confirmation rejection, and evidence/hash preservation after review.
- [x] Implement deterministic extraction/status/review persistence and API service delegation.
- [x] Run radar/date/service/API regression coverage; conservative evidence status and existing fixtures pass.

### Task 7: Question Session renderer and Chinese command alias

**Files:** `question_session.py`, `main.py`, `tests/test_question_session.py`, `tests/test_plugin.py`, `README.md`.

**Interfaces:** Add command root `法务` that enters the exact same parser/authorization/service handlers as `/law`; update snapshot rendering only where tests demonstrate defects.

- [x] Add synthetic long shared-stem regression matching Q309 shape, answer-safe first page, lossless block reconstruction, single stem display before subquestions, per-subquestion requirements, message budget, and restart/scope isolation.
- [x] Add parity tests proving Chinese root and `/law` invoke identical command/service paths and authorization.
- [x] Remove slash-prefixed ordinary user instruction examples while retaining slash command compatibility.
- [x] Implement minimum behavior changes; Question Session and plugin command regressions pass in the full suite.

### Task 8: Server-side pagination contracts

**Files:** `storage.py`, `library_repository.py`, `library_service.py`, `service.py`, `web_api.py`, `tests/test_library_repository.py`, `tests/test_library_service.py`, `tests/test_web_api.py`.

**Interfaces:** All large list routes accept `page` and bounded `page_size` (20/50/100); return `{items,page,page_size,total,page_count}`. Query and count use identical filters.

- [x] Add bounded page helper tests and endpoint regressions for Library, cases, review, Radar, targets and histories; query/count filters share their repository predicates.
- [x] Verify paged routes return the stable `{items,page,page_size,total,page_count}` envelope while legacy bounded list callers remain supported where retained.
- [x] Implement count/offset queries or a streaming Radar page scan; list pages do not materialize the full Radar table.
- [x] Run API/repository/service tests; existing callers remain compatible or are explicitly adapted.

### Task 9: Batch mutation prepare/confirm and admin Web API

**Files:** `service.py`, `web_api.py`, `tests/test_web_api.py`, `tests/test_library_service.py`, `tests/test_service.py`.

**Interfaces:** `prepare_batch(actor_id,action,item_ids,changes)` returns exact preview/token; `confirm_batch(actor_id,token)` rechecks one-shot ownership/action/expiry/exact IDs/current row fingerprints. Homogeneous edits and soft delete/restore are all-or-nothing in `BEGIN IMMEDIATE`; candidate promotion uses per-item savepoints. Management routes require existing Admin/operator gate.

- [x] Add tests for unauthorized access, stale selection, changed row, wrong actor/action, expiration/replay, all-or-nothing edit/delete, and mixed promotion item outcomes.
- [x] Verify prepare/confirm tokens are actor-bound, one-shot, stale-safe, and accept only the explicit management allowlist.
- [x] Implement allowlisted operations: title/subjects/note/practice notes/explanation/review state/delete/restore/promotion; never expose arbitrary SQL or raw hidden fields to ordinary tools.
- [x] Run management, Web API and safe DTO regressions; safe normal queries still exclude answer/explanation/raw evidence.

### Task 10: Fixed-scope data clear transactions

**Files:** `storage.py`, `service.py`, `web_api.py`, `tests/test_storage.py`, `tests/test_service.py`, `tests/test_web_api.py`.

**Interfaces:** `prepare_data_clear(actor_id,scope)` returns counts/retentions/token; `confirm_data_clear(actor_id,token,typed_confirmation=None)` performs fixed allowlisted FK-safe clearing. Full runtime reset requires exact `清空全部数据`.

- [x] Parameterize tests across scopes, seed related and unrelated entities, assert exact deletions/retentions, token binding/expiry/staleness, rollback, `PRAGMA foreign_key_check`, close/reopen schema 13.
- [x] Verify the clear API accepts only fixed scope names and its rollback/snapshot behavior is covered by deterministic tests.
- [x] Implement fixed scope-to-table registry, exact PK snapshot, `BEGIN IMMEDIATE`, revalidation, all-or-nothing deletion order and post-commit scheduler wake.
- [x] Run management/storage/API clear regressions; no arbitrary table input or schema/config deletion path is exposed.

### Task 11: Plugin Page pagination, batch management, plan editor and data management

**Files:** `pages/law-assistant/app.js`, `pages/law-assistant/style.css`, `.astrbot-plugin/i18n/*`, `tests/page_lifecycle.test.mjs`, `tests/test_page_assets.py`, `tests/page_discovery_smoke.py`.

**Interfaces:** Shared page state `{page,pageSize,total,selectedIds}`; a server page response drives controls; all destructive/multi-row actions call prepare then explicit confirm. Existing safe DTO routes remain safe.

- [x] Add Node tests for page metadata, page-local select/current-page-all, selection reset on filters, detail replace-in-place, cancel-without-persist, batch preview exact IDs, full-reset typed confirmation and reveal controls only for question plans.
- [x] Implement pager and shared selection/action UI for large tables, dedicated operator detail, dependency-driven direction/type/origin/reveal controls, Data Management scope preview and Chinese/English i18n/fallbacks.
- [x] Run Node lifecycle, Page asset, 4.25 discovery and API route smoke; no Bridge double-unwrapping regression is present.

### Task 12: Version/docs, build and complete verification

**Files:** `metadata.yaml`, `README.md`, `_conf_schema.json` only if required, `scripts/build_release.py`, `.github/workflows/ci.yml`, version/package tests.

**Interfaces:** Version `0.6.0`, schema 13; artifact name `astrbot_plugin_law_assistant-0.6.0.zip`; build excludes tests/scripts/docs/cache/SQLite/secrets while including runtime modules/page/i18n.

- [x] Add/adjust tests for version facts, changelog policy, package name and exclusion list.
- [x] Update README Changelog with `0.6.0 — Release Candidate` (no invented date), usage/configuration, migration backup, scheduler/reveal history, admin/batch, soft-delete/restore, promotion and clear semantics; distinguish implemented from externally blocked.
- [x] Bump metadata/runtime/Page version and schema references consistently; no release tag or Release is created.
- [x] Run Python 3.12 full quality checks, complete pytest, Node lifecycle, exact AstrBot 4.22/4.25 loader and extracted-package smokes, 4.25 Page/API smoke, ZIP inspection and SHA-256; required local checks pass.
- [x] Review the diff, verify the original checkout is untouched, build the package from staged tracked files, and verify the live remote before attempting any fast-forward push. Remote movement or unreadability remains a hard stop.

## Final local verification record

- Python 3.12.14: `compileall` PASS; Ruff format/check PASS; `pytest -q` 393 passed; `git diff --check` and cached diff check PASS.
- Node lifecycle: 20 passed.
- Exact AstrBot source loader smoke: 4.22.0 (`81c7b0f7150485beb6124a7ec524a8d8534e7f6e`) and 4.25.0 (`02291a3217c92faa0c577bf9d89076949c40954c`) PASS.
- Extracted package: compileall and both exact-version loader smokes PASS; AstrBot 4.25 Plugin Page discovery PASS; 45 Web API routes and required route contracts PASS.
- Package: `astrbot_plugin_law_assistant-0.6.0.zip`, 236,073 bytes, SHA-256 `1f2bc15859777824b75098a89a73892ae493ca6f4b95e1fea5ebbf5004ca2bef`; ZIP integrity and required/excluded path checks PASS.
- Real QQ, external LLM, live websites, and Windows-host installation were not exercised.
