# Objective Question Group Delivery Implementation Plan

> **For agentic workers:** Execute inline in the existing isolated worktree. Each task is test-first; the user requested one focused final commit and a normal push to `main`.

**Goal:** Validate, persist, select, publish, and study schema 1.1 objective-question groups as one ordered delivery unit without changing real-question verification identity or SQLite schema.

**Architecture:** Extend the pure structured-material validator for explicit schema 1.1 groups, retain the hash-bound import payload already stored in `structured_imports`, and add a narrow resolver over existing item bindings. Reuse LearningContentProvider, Question Session snapshots, pending publication confirmations, and daily-content idempotency; group delivery sends answer-free pages sequentially and opens a session only after complete success.

**Tech Stack:** Python 3.12, stdlib SQLite, pytest/pytest-asyncio, Ruff 0.16.10, AstrBot 4.22.0 and 4.25.0 exact-source smoke.

**Spec:** User-provided work order in this conversation attachment; it is not copied into runtime data or the objective JSON is not committed.

## Global Constraints

- Keep AstrBot 4.22.0 / 4.25.0 compatibility.
- Do not change AstrBot Core, add a service, add a database table, or bump schema v13.
- Structured Material 1.0 keeps its current semantics; 1.1 explicitly validates `objective_question_groups`.
- Only single-choice, multiple-choice, indefinite-choice, and true/false questions may join groups.
- Candidate imports remain `real_question_candidate` / `pending_review`; only fully promoted groups enter verified-real selection.
- A group is one target-scoped daily/history unit; group messages are ordered and answer-free, with no automatic resend after failed/unknown delivery.
- Real PDF/JSON and real question text stay outside Git and tracked tests; use synthetic fixtures.
- Preserve subject/type/origin strict filters, exact target confirmation, and existing subjective question behavior.
- No schema/data migration, tag, or Release.

## Review Focus

- Schema 1.1 group references must fail closed on dangling, overlapping, duplicate, non-objective, or inconsistent-material membership.
- Candidate and verified-real identities must never be mixed or split to satisfy a filter.
- Source-number collisions across imports must be explicit ambiguity, never first-match selection.
- Page splitting must preserve every material/stem/option character while never exposing answers or explanations.
- Partial/unknown multi-message delivery must neither open a session nor be retried as a whole group.

---

### Task 1: Validate schema 1.1 objective groups

**Files:** `structured_material.py`, `tests/test_structured_material.py`

**Interfaces:** `ValidationResult.objective_question_groups: tuple[dict[str, Any], ...]`; preview exposes group summaries and count. Preserve the current 1.0 behavior.

- [x] Add synthetic valid three-question group plus malformed-reference, overlap, duplicate-member, non-objective, inconsistent-material and invalid-mode tests; run them to observe expected failures.
- [x] Implement explicit 1.1 version dispatch and group validation using document-wide IDs; reject a missing 1.1 group field and unsupported versions.
- [x] Verify 1.0 fixtures remain valid and preview tests pass.

### Task 2: Resolve persisted delivery units without schema changes

**Files:** `objective_question_groups.py` (new), `library_repository.py`, `tests/test_structured_ingestion.py`, new focused resolver tests as needed.

**Interfaces:** New pure functions `resolve_objective_question_group(payload, external_id) -> dict[str, Any] | None` and `normalize_source_number(value) -> str`. Repository methods `get_structured_delivery_unit(item_id)`, `get_structured_delivery_unit_for_real_question(real_question_id)`, and `list_structured_source_number_matches(source_number)` return the import scope, ordered member bindings, payloads, and linked `LibraryItemBundle`s. A group-free referenced-material question resolves as a one-question unit. Resolver keys include import scope plus group ID.

- [x] Add synthetic prepare/confirm regression proving the full 1.1 payload remains available and any member source number resolves the same ordered unit.
- [x] Add resolver behavior for bindings, relations, source numbers, import ambiguity and referenced material; do not infer groups from text.
- [x] Verify 1.0 imports and duplicate import idempotency remain unchanged.

### Task 3: Build complete answer-safe group snapshots and navigation

**Files:** `question_session.py`, `service.py`, `question_session_repository.py` only if needed, `tests/test_question_session.py`, `tests/test_service.py`.

**Interfaces:** `build_question_session_snapshot()` accepts `objective_questions: list[dict]` plus shared materials and returns one prompt per member. `format_session_prompt_pages(snapshot) -> list[str]` returns all answer-free material/member pages in order. `question_session_action(..., question_number: str | None = None)` may select a prompt by exact normalized source number; existing unqualified actions keep current-prompt behavior.

- [x] Test shared material once, complete top-level question and options for each member, no answer leakage, bounded page length, and independent per-prompt answer reveal.
- [x] Implement the objective-group snapshot and ordered answer-free page helper; existing per-prompt reveal events key each member independently.
- [x] Verify existing single/subquestion session tests; exact source-number cursor selection will be exercised with the command/service integration in Task 5.

### Task 4: Select grouped verified-real units and keep candidates separate

**Files:** `learning_inventory.py`, `library_repository.py`, `objective_question_groups.py`, `tests/test_learning_inventory.py`.

**Interfaces:** `LearningContentProvider.select_question()` returns the existing result mapping plus `objective_questions`, `shared_materials`, `objective_group_id`, and import-scoped `source_kind`/`source_item_key`; strict filters apply to every member; successful-use filtering operates on the group key. Candidates remain outside provider real inventory.

- [x] Add tests for member-to-group selection, all-members-promoted requirement, individual promotion not splitting a group, whole-group filters, daily deduplication, and referenced-material singleton delivery.
- [x] Implement unit candidate enumeration and answer/evidence assembly from persisted real rows plus original structured bindings.
- [x] Verify strict `origin=real` miss and mock selection regressions.

### Task 5: Integrate source-number lookup, confirmed multi-page publishing, and daily delivery

**Files:** `service.py`, `main.py`, `tests/test_service.py`, `tests/test_plugin.py`, relevant daily tests.

**Interfaces:** `LawAssistantService.start_question_by_number(source_number, source_selector, ...)` resolves a unique import scope and opens one session; `prepare_publish_question(..., source_number=None, source_selector=None)` locks a `bodies: tuple[str, ...]` preview. `_publish_question_to_targets()` sends those bodies in order and opens the target session only after all pages report sent. Daily objective questions use the same page builder and group history key.

- [x] Add tests for manual command/Tool lookup, ambiguous source number handling, frozen page preview/target/token, sent-only session activation, failed/unknown stop/no resend, and daily group idempotency.
- [x] Implement command/Tool parameter routing through LawAssistantService and group-page publishing; keep existing prepare → preview → confirm and authorization.
- [x] Verify independent targets, no partial-success session, and existing event/case/question publication behavior.

### Task 6: Documentation and complete verification

**Files:** `README.md`, this plan, tests if verification exposes a covered defect.

- [x] Document 1.1 group shape, commands/natural language, objective-only scope, candidate status, page and failure behavior; add the change under `0.6.1 — Unreleased`.
- [x] Run Python compileall, Ruff format/check at 0.16.10, full pytest, Node tests, diff check, exact AstrBot 4.22.0/4.25.0 loader smokes; archive install smokes run against the committed exact tree below.
- [x] Run optional read-only validator against the supplied JSON and report counts only; do not import it into a database or commit it.
- [x] Review the complete diff for scope/data safety; make one focused commit and ordinary push to `main`; inspect exact-SHA CI.
