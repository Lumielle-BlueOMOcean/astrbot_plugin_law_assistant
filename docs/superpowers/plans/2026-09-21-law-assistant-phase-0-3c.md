# Law Assistant Phase 0.3C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the plugin with a deterministic current-activity radar and a unified daily-learning inventory whose subject and question-type plans are independent, strict, persistent, and scheduler-safe.

**Architecture:** Keep `LawAssistantService` as the only facade for commands, Tools, and Scheduler. Add focused radar and daily-resolution helpers; extend SQLite minimally from schema v8 to v9 for cross-source relations, independent question-type plan fields, and stable daily-content identities. Reuse the existing LibraryService/Repository for persistent mock questions and the existing Publisher confirmation boundary.

**Tech Stack:** Python 3.12+, stdlib `sqlite3`, `zoneinfo`, existing AstrBot decorators, deterministic pytest fakes, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-21-law-assistant-phase-0-3c-design.md` and the approved Phase 0.3C施工单 supplied by the user.

## Global Constraints

- Preserve AstrBot compatibility `>=4.22.0,<5`; do not modify AstrBot Core.
- Keep GitHub `main` as source of truth and do not add Agent/API/MCP/server infrastructure.
- Keep case and question scheduler tasks independent; strict constraint misses are skips, never silent fallback.
- Store runtime state only under the AstrBot plugin data directory; never commit SQLite, secrets, caches, or generated runtime files.
- Confirmed source evidence and deterministic code own truth; LLM dates and generated material cannot acquire official identity.
- Automatic publication remains operator-preauthorized and idempotent; manual publication remains `prepare → preview → confirm → execute`.
- Network code remains async and uses existing HTTP client; no `requests` dependency.
- Run tests before production code for each behavior, then compileall, Ruff, pytest, diff check, and real 4.22/4.25 loader smokes.

## Review Focus

- Historical notices first discovered today must be stored but excluded from default current results and automatic publication; cover with a 2019 fixture at a 2026 clock.
- A date rollover without source-content change must change derived radar visibility without creating an event revision; cover with two clocks against one database row.
- `real + subject + question_type` must never fall back to mock or another type; cover mixed real/candidate/mock inventory.
- Generated mock questions must be validated and persisted before daily sending; cover both valid and malformed LLM output.
- Subject rotation and question-type rotation must calculate independently after restart and after a skip; cover two different anchors over multiple dates.

---

### Task 1: Activity radar resolver and source policy

**Files:**
- Create: `activity_radar.py`
- Modify: `models.py`, `extraction.py`, `config.py`, `_conf_schema.json`, `main.py`, `service.py`
- Test: `tests/test_activity_radar.py`, `tests/test_extraction.py`, `tests/test_config.py`

**Interfaces:**
- Produces `derive_radar_status(event, now, timezone_name, historical_keywords=...) -> str`, `canonical_event_key(event) -> str | None`, and `RadarSourcePolicy`/`radar_policy_for(source_key, config)` helpers.
- `LawAssistantService.list_events(limit, radar_status, event_type, keyword)` accepts `current`, `needs_review`, `historical`, or `all` and passes one clock to the resolver.

- [ ] **Step 1: Write failing tests** for confirmed-future current, expired historical, old 2019 historical, no-evidence needs-review, evidence-mismatched LLM date, configurable keyword extensions, and strict source auto-publish policy.
- [ ] **Step 2: Run** `./.venv/bin/pytest tests/test_activity_radar.py tests/test_extraction.py tests/test_config.py -q`; expected: new resolver/config tests fail because the interface and dynamic filtering do not exist.
- [ ] **Step 3: Implement** the pure resolver and extraction metadata. Use confirmed dates only, store historical/participation signals in metadata, merge configured keywords with built-ins, and keep source revision fields unchanged when only `now` changes.
- [ ] **Step 4: Implement** service query filtering and update `/law events` plus `law_list_events` with `radar_status`, `event_type`, `keyword`, and compact current-event formatting.
- [ ] **Step 5: Run** the focused tests and the existing service tests; expected: all radar cases pass and old event/query behavior remains green.

### Task 2: v8→v9 storage foundation and cross-source relations

**Files:**
- Modify: `storage.py`, `models.py`, `service.py`
- Test: `tests/test_storage.py`, `tests/test_service.py`

**Interfaces:**
- Migration adds `event_relations`, independent question-type fields to `daily_plans`, and `source_kind`, `source_item_key`, `resolved_subject`, `resolved_question_type`, `resolved_origin` to `daily_contents` while retaining old columns.
- Produces `record_event_relation`, `related_events`, and `has_canonical_publication` storage methods.

- [ ] **Step 1: Write failing tests** for schema v9, v8 upgrade preservation, strong/weak event relation behavior, stable daily-content identity, and migration idempotence.
- [ ] **Step 2: Run** `./.venv/bin/pytest tests/test_storage.py tests/test_service.py -q`; expected: new v9 assertions fail against schema v8 and integer-only history.
- [ ] **Step 3: Implement** one transactional `_migrate_8_to_9`; extend `DailyPlan` row serialization and daily-content claim/list helpers without dropping old fields or touching user runtime data outside migration.
- [ ] **Step 4: Integrate** canonical-key recording after event upsert and block duplicate automatic publication when a related event revision has already been published.
- [ ] **Step 5: Run** focused storage/service tests and confirm existing schema migration tests still pass.

### Task 3: Independent DailyPlan subject/type resolver

**Files:**
- Modify: `daily_plans.py`, `config.py`, `_conf_schema.json`, `service.py`
- Create: `daily_resolver.py`
- Test: `tests/test_content_and_plans.py`, `tests/test_review_fixes.py`, `tests/test_daily_resolver.py`

**Interfaces:**
- `DailyPlan` gains `question_type_selection_mode`, `fixed_question_type`, `rotation_question_types`, `question_type_rotation_start_date`, and `question_type_rotation_start_index`.
- Produces `resolve_daily_constraints(plan, current_date, target_id, content_type) -> dict` with `subject`, `subject_mode`, `question_type`, `question_type_mode`, and `origin`.

- [ ] **Step 1: Write failing tests** for fixed/random/rotation type modes, two independent anchors, school preset, arbitrary list lengths, pre-start dates, old `question_type` compatibility, and seven-day preview without generation.
- [ ] **Step 2: Run** the focused plan tests; expected: new type-axis and preview assertions fail.
- [ ] **Step 3: Implement** date-based deterministic selection using local calendar dates and a stable hash for random axes; validate non-empty rotation lists, valid types, dates, and indices.
- [ ] **Step 4: Extend** config parsing and schema with global daily question type fields and persist the new fields for config and target plans. Preserve explicit dates and existing anchor materialization behavior.
- [ ] **Step 5: Make `list_daily_plans`, plan preview, and scheduled execution call the shared resolver; run focused tests and old plan tests.

### Task 4: Unified LearningContentProvider and persistent mock inventory

**Files:**
- Create: `learning_inventory.py`
- Modify: `library_repository.py`, `library_service.py`, `learning_service.py`, `service.py`
- Test: `tests/test_learning_inventory.py`, `tests/test_learning_service.py`, `tests/test_library_service.py`

**Interfaces:**
- `LearningContentProvider` exposes `select_case(subject, date, content_type)`, `select_question(origin, subject, question_type, session_origin)`, and `validate_daily_item(...)` using existing LibraryService/SQLiteStorage.
- `LearningService` delegates daily case/question selection to the provider and returns explicit `origin`, `subject`, `question_type`, and stable source identity.

- [ ] **Step 1: Write failing tests** for active official-case selection, exact real filtering, persistent valid mock selection, excluded candidate/inactive/invalid mock rows, strict no-match skips, random origin reporting, valid generated mock persistence, and invalid LLM output rejection.
- [ ] **Step 2: Run** focused learning tests; expected: persistent mock inventory and provider tests fail because no provider path exists.
- [ ] **Step 3: Add** repository/service queries for active `mock_question` and `official_case` bundles, deterministic eligibility validation for all five question types, and a library-bundle-to-message conversion.
- [ ] **Step 4: Implement** provider selection. For missing mock inventory, validate LLM output, archive it as a `mock_question` with evidence and `created_by=system:daily_question`, then select the stored item for the response. Never alter real-question identity.
- [ ] **Step 5: Run** focused learning/library tests plus existing question/case regression tests.

### Task 5: Scheduler integration, stable daily history, and Radar publication gates

**Files:**
- Modify: `service.py`, `scheduler.py`, `publisher.py`, `main.py`
- Test: `tests/test_scheduler.py`, `tests/test_service.py`, `tests/test_review_fixes.py`, `tests/test_plugin.py`

**Interfaces:**
- `_run_daily_content` resolves constraints once, selects through `LearningContentProvider`, records stable source identity and resolved axes, and independently claims `daily_case`/`daily_question`.
- Automatic event publishing consults radar status, source policy, evidence, canonical relation publication, and existing revision/target idempotency.

- [ ] **Step 1: Write failing tests** for independent subject/type combinations, twelve-day 4×3 rotations, skip-without-advance, persistent mock daily send, stable history fields, case/question isolation, historical/needs-review auto-publish refusal, current trusted auto-publish, and cross-source no-duplicate publish.
- [ ] **Step 2: Run** focused scheduler/service/plugin tests; expected: new cases fail on type constraints, source identity, and radar gates.
- [ ] **Step 3: Implement** the smallest integration using shared resolver/provider methods and existing claim idempotency; do not add a second scheduler loop.
- [ ] **Step 4: Update** command/tool serialization and required compatibility smoke expectations for radar filters and plan fields.
- [ ] **Step 5: Run** focused integration tests and the full current suite.

### Task 6: Documentation, long-term rules, and final verification

**Files:**
- Modify: `README.md`, `AGENTS.md`, `metadata.yaml`
- Test: `tests/test_compatibility_smoke.py`, CI workflow if required by new tests

- [ ] **Step 1: Update** README Implemented/Planned sections with current-only Radar semantics, trusted source policy, persistent mock inventory, independent subject/type axes, strict skip, and schema v9.
- [ ] **Step 2: Merge** only the durable Phase 0.3C rules into AGENTS.md; do not rewrite existing rules or add temporary施工步骤.
- [ ] **Step 3: Run** `./.venv/bin/python -m compileall .`, `./.venv/bin/ruff format --check .`, `./.venv/bin/ruff check .`, `./.venv/bin/pytest`, and `git diff --check`; all must exit 0.
- [ ] **Step 4: Run** real AstrBot loader smoke for exact 4.22.0 commit `81c7b0f7150485beb6124a7ec524a8d8534e7f6e` and 4.25.0 commit `02291a3217c92faa0c577bf9d89076949c40954c`; both must PASS without stubs.
- [ ] **Step 5: Inspect diff/status, commit `feat: upgrade activity radar and daily learning plans`, push `origin main`, wait for GitHub Actions, and verify local/remote exact SHA plus clean worktree.
