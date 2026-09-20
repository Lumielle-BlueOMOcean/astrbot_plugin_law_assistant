# Learning Library Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, permission-controlled learning library that preserves raw user evidence and supports archive, search, get, update, and restart recovery through the existing LawAssistantService and AstrBot LLM Tool entrypoints.

**Architecture:** SQLiteStorage remains the sole schema/lifecycle owner and upgrades v5 to v6. A new LibraryRepository uses its live connection, LibraryService owns validation/hash/dedup and DTOs, and LawAssistantService delegates to it; main.py only wires four authorized LLM Tools.

**Tech Stack:** Python 3.12 baseline, stdlib sqlite3/json/hashlib, existing AstrBot `@filter.llm_tool`, pytest/pytest-asyncio, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-20-learning-library-foundation-design.md`

## Global Constraints

- Preserve AstrBot compatibility `>=4.22.0,<5`; verify exact v4.22.0 and v4.25.0 source commits.
- Use the existing SQLite database and migration lifecycle; never create `law_library.sqlite3` or a second schema version.
- Store runtime data only below `StarTools.get_data_dir("astrbot_plugin_law_assistant")`.
- LLM interprets evidence; deterministic code owns identity, hashes, permissions, validation, deduplication, and verification state.
- Archive accepts only `case`, `real_question_candidate`, `mock_question`, and `note`.
- Agent input can never promote content to `official_case` or `verified_real_question`.
- Preserve `created_by`, `session_origin`, exact `raw_text`, and source hash.
- Same operator plus same raw text may reuse a Source, but different structured items must remain separate and must not overwrite one another.
- All library operations require the existing private-chat and Admin/operator authorization.
- Do not add DOCX/PDF/OCR/QQ-file ingestion, official-article splitting, RAG, WebUI, new event sources, or daily-task takeover.
- Existing event, official-case, verified-question, daily-plan, scheduler, and publication behavior must remain green.

## Review Focus

- Same source with different summaries/subjects must produce separate items: `test_same_source_does_not_overwrite_distinct_items`.
- Agent-supplied official/verified claims must be rejected or forced to safe identity: `test_archive_cannot_escalate_verification_identity`.
- Raw source must survive a deliberately incorrect summary: `test_raw_text_is_preserved_separately_from_summary`.
- Unauthorized search/get/update must be denied at the Tool entrypoint: `test_library_tools_require_operator_private_chat`.
- Restart must prove actual Tool flow rather than repository-only persistence: `test_archive_search_get_survives_plugin_restart`.

---

### Task 1: Schema v6, models, and repository

**Files:**
- Create: `library_models.py`
- Create: `library_repository.py`
- Modify: `storage.py` migration constant and v5→v6 migration
- Test: `tests/test_library_repository.py`

**Interfaces:**
- Consumes: `SQLiteStorage.connection` and `SQLiteStorage.schema_version`.
- Produces: `LibraryRepository.archive(...)`, `search(...)`, `get(...)`, `update(...)`, and library DTOs used by Task 2.

- [ ] **Step 1: Write failing repository/schema tests** covering v6 table creation, foreign keys, source reuse, distinct item rows, case/question detail round-trips, search fields, and restricted update columns.
- [ ] **Step 2: Run `./.venv/bin/pytest tests/test_library_repository.py -q` and verify failure because the new modules and v6 migration do not exist.
- [ ] **Step 3: Add library dataclasses, `SQLiteStorage.connection`, v6 migration tables/indexes, and parameterized repository CRUD with one transaction per mutation.
- [ ] **Step 4: Run the repository test file and verify all new storage behaviors pass.
- [ ] **Step 5: Run existing `tests/test_storage.py -q` to verify v5 migration and current storage behavior remain green.

### Task 2: LibraryService validation, identity, search, and update

**Files:**
- Create: `library_service.py`
- Test: `tests/test_library_service.py`

**Interfaces:**
- Consumes: `LibraryRepository` from Task 1 and existing subject/question-type normalization.
- Produces: async-safe service methods returning stable dictionaries: `archive_learning_material`, `search_learning_library`, `get_learning_item`, `update_learning_item`.

- [ ] **Step 1: Write failing service tests** for allowed material mappings, raw-text preservation, candidate/mock separation, invalid JSON/shape errors, identity escalation blocking, same-source distinct items, field search, limits, and restricted updates.
- [ ] **Step 2: Run the focused service tests and verify expected failures for missing `LibraryService`.
- [ ] **Step 3: Implement deterministic SHA-256 source/item hashes, structured JSON validation, subject/type normalization, safe verification defaults, stable error dictionaries, and service-to-repository calls.
- [ ] **Step 4: Run focused service tests and verify pass.
- [ ] **Step 5: Run `./.venv/bin/pytest tests/test_library_repository.py tests/test_library_service.py -q` and verify both layers pass together.

### Task 3: LawAssistantService facade and Tool lifecycle

**Files:**
- Modify: `service.py`, `main.py`
- Modify: `tests/fakes.py`
- Test: `tests/test_library_tools.py`, `tests/test_plugin.py`

**Interfaces:**
- Consumes: `LibraryService` methods from Task 2 and existing `_authorized`, `_session_origin`, `_json_text` helpers.
- Produces: registered Tools `law_archive_learning_material`, `law_search_learning_library`, `law_get_learning_item`, `law_update_learning_item`; all delegate through `LawAssistantService`.

- [ ] **Step 1: Write failing entrypoint tests** for authorization, stable JSON errors, archive return IDs, exact Tool delegation, and archive→terminate→recreate→search/get using the real plugin instance and temporary data directory.
- [ ] **Step 2: Run focused Tool tests and verify failure because methods/tools are not registered.
- [ ] **Step 3: Wire `LibraryService` in plugin initialization and facade methods; add concise Tool docstrings/structured parameters; catch validation errors into stable JSON without leaking tracebacks.
- [ ] **Step 4: Run focused Tool/plugin tests and verify pass, including the restart acceptance flow.
- [ ] **Step 5: Run existing plugin/product tests and verify command, authorization, publication, question import, plans, and scheduler regressions remain green.

### Task 4: Long-term rules, documentation, and complete verification

**Files:**
- Modify: `AGENTS.md`, `README.md`
- Test: all existing tests plus `tests/test_compatibility_smoke.py`

**Interfaces:**
- Consumes: completed library behavior from Tasks 1–3.
- Produces: accurate implemented/deferred documentation and release evidence.

- [ ] **Step 1: Add only the durable evidence/identity/Service-boundary rules to AGENTS.md and document archive/search/get/update, identities, raw preservation, and deferred features in README.md.
- [ ] **Step 2: Run `./.venv/bin/python -m compileall .`, Ruff format/check, pytest, and `git diff --check`; fix only failures caused by this feature.
- [ ] **Step 3: Run real AstrBot v4.22.0 and v4.25.0 compatibility smoke and verify all four new Tools are present.
- [ ] **Step 4: Inspect the final diff for scope creep, secrets, runtime DB files, and forbidden feature work.
- [ ] **Step 5: Commit with `feat: add learning library foundation`, push `main`, wait for green GitHub Actions, and report exact SHA, schema version, tools, acceptance results, and deferred scope.

