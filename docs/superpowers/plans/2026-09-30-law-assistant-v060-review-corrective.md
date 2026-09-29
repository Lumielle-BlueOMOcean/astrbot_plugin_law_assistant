# Lumielle Law Assistant v0.6.0 Review Corrective Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Resolve the confirmed exact-SHA v0.6.0 review defects while retaining version 0.6.0 and schema 13.

**Architecture:** Keep LawAssistantService as the shared application boundary. Put truth and destructive-scope invariants in the SQLite-backed repositories/services; keep UI controls as typed requests through existing Web API routes. Add deterministic regressions before each behavior change.

**Tech Stack:** Python 3.12, stdlib SQLite, pytest, Ruff, AstrBot 4.22/4.25 loader smoke, Node lifecycle tests.

**Spec:** User-approved corrective request in `/Users/blueomocean/.codex/attachments/d2dde96e-6681-488d-9439-a5f2f95213d9/已粘贴的文本.txt`.

## Global Constraints

- Base commit is `3c3c70077fc0901883b0f156d7f743ab85ec89d1`; stop if remote `main` diverges before push.
- Keep plugin version `0.6.0` and schema version `13`; do not tag or create a Release.
- Preserve AstrBot 4.22.0 and 4.25.0 compatibility.
- No real legal corpus, production DB, real QQ, or real LLM writes/tests.
- Keep truth-bearing inventory synchronized, immutable Question Session snapshots intact, and all scoped clears transactional and domain-limited.
- Use `prepare → preview → explicit confirm → execute` for management side effects.
- Run full local checks and release package smoke before normal push to `main`.

## Review Focus

- Linked verified-question edits, revocation, deletion, restore, and identity collision must never leave stale or duplicate selectable truth; pin with a repository/service integration test and immutable-session assertion.
- Clearing one domain must not erase another domain's source baseline, and each cleared domain must remain rebuildable; pin with source-document/run fixtures, same-payload reimport, rollback, DB reopen, and foreign-key checks.
- Ignored Radar state, unconfirmed date evidence, and delayed reveal ordering must remain independent; pin each resolver/UI boundary with service and lifecycle tests.
- Every batch UI enum must be constrained to supported actions and preserve operator authorization; pin via route-contract and frontend lifecycle behavior.
- No code-help hint should expose slash-prefixed commands; pin the rendered help output.

---

### Task 1: Radar ignored state and status UX

**Files:** `service.py`, `storage.py`, `pages/law-assistant/app.js`, `tests/test_activity_radar.py`, `tests/page_lifecycle.test.mjs`.

**Interfaces:** Service derives Radar status; storage persists only explicit display overrides; page submits fixed statuses.

- [x] Add a service regression for event → ignored override → overview without exception, current count exclusion, ignored filter result, and clear override returning to derived status.
- [x] Run that test and verify it fails on the current ignored-count lookup/filter behavior.
- [x] Implement ignored as a first-class status in overview and event filters; offer it in the single-event status control and preserve clear-to-derived behavior.
- [x] Run focused Python and Node tests.

### Task 2: Target binding and due-scheduler wake

**Files:** `service.py`, `scheduler.py`, `tests/test_service.py`, `tests/test_scheduler.py`.

**Interfaces:** `bind_target()` / `unbind_target()` remain service operations; scheduler exposes one wake/recompute callback.

- [x] Add a regression with an enabled global daily-question plan, zero targets at startup, then bind a target and assert scheduler wake plus exactly one delivery at the configured due time.
- [x] Run the focused test and verify it fails because binding does not wake the scheduler.
- [x] Wake/recompute after successful bind/re-enable and unbind, without changing rename behavior or creating duplicate scheduler loops.
- [x] Run the focused service regression; existing scheduler tests are retained for the final focused/full verification.

### Task 3: Linked verified-question truth management

**Files:** `library_repository.py`, `library_service.py`, `learning_inventory.py`, `service.py`, `web_api.py`, `pages/law-assistant/app.js`, `tests/test_library_service.py`, `tests/test_web_api.py`, `tests/page_lifecycle.test.mjs`.

**Interfaces:** `structured_item_bindings.verified_real_question_id` links projection to `real_questions`; `origin=real` reads the verified inventory; session snapshots are immutable.

- [x] Add integration regressions for verified edits being visible to `origin=real`, revocation/delete removing selection, restore/reverify restoring selection, identity collision rollback, and pre-existing session snapshot stability.
- [x] Add coverage for independently imported real questions being individually viewable, editable/revalidated, disabled/removed, and restored through an explicit management path.
- [x] Run focused tests and verify the former divergence: edit left `origin=real` stale and an identity collision did not roll back.
- [x] Make linked truth edits atomic with deterministic validation and collision checks; provide explicit revoke/demote semantics and individual independent-inventory management. Do not rewrite old session snapshots.
- [x] Run focused service, Web API, and frontend tests.

### Task 4: Verified inventory clear returns candidates to a coherent state

**Files:** `storage.py`, `library_repository.py`, `tests/test_storage.py`, `tests/test_library_service.py`.

- [x] Add promote → clear verified inventory → candidate restored → re-promote → foreign-key-clean regression, including proof unrelated manual library entries are not demoted.
- [x] Verify RED against the current clear behavior.
- [x] In the same transaction as inventory deletion, clear promoted links/metadata and restore reviewable candidate identity/state only for linked candidates.
- [x] Run focused service integration, reopen DB, and assert schema 13 plus `foreign_key_check`.

### Task 5: Case clear remains rebuildable

**Files:** `storage.py`, `library_repository.py`, `library_service.py`, `service.py`, `tests/test_storage.py`, `tests/test_library_service.py`, `tests/test_service.py`.

- [x] Add regressions for unchanged official-source rescan after case clear; identical case-only structured import after clear; and mixed question/case import where questions remain singletons while cases reconstruct.
- [x] Verify each regression fails on current source/import dedup behavior.
- [x] Narrowly invalidate CASE-domain source processing state and make structured duplicate confirmation reconstruct missing case records without duplicating retained questions.
- [x] Verify transactional clearing, duplicate-import atomicity, DB reopen, and foreign keys.

### Task 6: Domain-scoped Radar clear and cross-domain matrix

**Files:** `storage.py`, `service.py`, `web_api.py`, `pages/law-assistant/app.js`, `README.md`, `tests/test_storage.py`, `tests/test_web_api.py`, `tests/page_lifecycle.test.mjs`.

- [x] Seed Radar, official-case, and law-update documents/runs plus a law update; test Radar clear deletes only Radar/event rows and source state.
- [x] Assert Radar clear preserves case/law source documents, case/law runs, and law updates; verify rollback, repeatability, reopen, FK checks, and schema 13.
- [x] Run the new regression against current behavior and observe broad source deletion in the initial count (`3` source documents instead of `1`).
- [x] Add fixed source-domain predicates (never caller-provided SQL identifiers); keep law updates outside the Radar clear scope, so no separate UI scope is introduced.
- [x] Run focused storage tests and existing clear-scope checks.

### Task 7: Explicit human confirmation for Radar dates

**Files:** `storage.py`, `service.py`, `web_api.py`, `pages/law-assistant/app.js`, `tests/test_activity_radar.py`, `tests/test_web_api.py`, `tests/page_lifecycle.test.mjs`.

- [x] Add a regression showing an inferred unconfirmed deadline remains excluded after status override, becomes eligible only after accepted-and-confirmed date review, and remains excluded after rejected/unconfirmed review.
- [x] Add UI/route contract assertions for current/proposed confirmation state, evidence, and reason.
- [x] Verify RED on the current behavior: service rejected the explicit confirmation argument, API preview omitted it, and Plugin Page had no control.
- [x] Persist explicit date confirmation independently from display status and audit the actor/evidence/reason in the existing review payload and audit record.
- [x] Run focused Python and Node tests.

### Task 8: Delayed answer/explanation ordering

**Files:** `daily_plans.py`, `pages/law-assistant/app.js`, `tests/test_daily_resolver.py`, `tests/page_lifecycle.test.mjs`.

- [x] Add backend regression: delayed requires answer delay > 0 and explanation delay >= answer delay; manual mode safely ignores/reset delays.
- [x] Add frontend regression: switching manual→delayed starts with useful valid defaults and renders readable validation errors.
- [x] Verify both regressions fail on current behavior.
- [x] Implement the narrow validation/default changes without altering manual reveal behavior.
- [x] Run focused backend/frontend tests.

### Task 9: Batch UX and case verification states

**Files:** `library_models.py`, `library_repository.py`, `library_service.py`, `service.py`, `web_api.py`, `pages/law-assistant/app.js`, `tests/test_library_service.py`, `tests/test_web_api.py`, `tests/page_lifecycle.test.mjs`.

- [x] Add regressions for user-case pending/unverified → user_verified via controlled operator action, batch operation, select-only supported values, and rejection of manual official_case assignment.
- [x] Add lifecycle coverage showing Radar batch removal is explicitly recoverable, not physical deletion, and case review controls are present and constrained.
- [x] Run focused tests and observe the unsupported/missing management behavior.
- [x] Implement bounded authorized batch review/status controls; keep official-case identity exclusive to trusted source adapters.
- [x] Run focused service, API, and frontend tests.

### Task 10: Preset help without slash-prefixed hints

**Files:** `main.py`, `tests/test_plugin.py`.

- [x] Add an assertion that `_help_text()` contains no `/law` while retaining parser compatibility tests.
- [x] Verify RED.
- [x] Change only the displayed help examples to non-slash forms and run focused plugin tests.

### Task 11: Consolidated destructive-scope regression matrix

**Files:** `tests/test_storage.py`, `tests/test_library_service.py`, `tests/test_service.py`.

- [x] Expand clear-scope tests to seed multiple source domains, law updates, promoted links, and structured duplicate/reimport state.
- [x] For every scope assert intended deletion, unrelated retention, rebuildability, `foreign_key_check`, DB reopen, and schema 13.
- [x] Run the full clear matrix and narrow delivery-history clearing so source run state remains intact.

### Task 12: Release-candidate documentation and complete verification

**Files:** `README.md`, `.github/workflows/ci.yml` only if package smoke needs a corrected artifact filename; tests remain in their owning tasks.

- [x] Update the 0.6.0 release-candidate changelog/manual for scoped clear, verified-question edit/revoke/delete/restore, human date confirmation, and delayed reveal constraints. Do not add a released-version entry.
- [x] Verify metadata version 0.6.0 and schema 13 remain unchanged; ensure no real legal corpus was added.
- [x] Run `python -m compileall .`, `ruff format --check .`, `ruff check .`, `pytest`, `node --test tests/page_lifecycle.test.mjs`, and `git diff --check`.
- [x] Run exact AstrBot 4.22.0 and 4.25.0 compatibility smokes; run both release-package smokes and inspect `astrbot_plugin_law_assistant-0.6.0.zip`.
- [ ] Perform final whole-branch review, commit focused corrections, recheck remote `main` still equals the required base before normal push, then verify exact remote SHA and its CI. No tag or Release.
