# Lumielle Law Assistant v0.6.0 — Design

## Status and scope

Approved implementation design for the integrated v0.6.0 cycle. This is not a release authorization: the cycle may update `main` after verification, but must not create a tag or GitHub Release. The minimum supported AstrBot version remains 4.22.0. Runtime work remains inside `LawAssistantService` and the existing plugin data directory.

The implementation is bounded to the requested scheduler, scheduled reveals, management lifecycle, pagination, review/promotion, radar review, mock-selection, command wording, and session regressions. It does not add an Agent/bridge/server, change AstrBot Core, import real acceptance data, or turn candidate material into verified content without an explicit operator action.

## Existing boundaries retained

`command / LLM Tool / Scheduler / Plugin Page -> LawAssistantService -> domain services/repositories -> SQLite / source adapters / Publisher` remains the only business path. `Web API` validates transport input and delegates to `LawAssistantService`; it does not issue SQL. The existing `SQLiteStorage` owns the SQLite connection and sequential transactional migrations. The normal Library DTO remains answer-safe; a separate operator management DTO is used only after the existing Admin/operator authorization check.

## Schema v13

`SCHEMA_VERSION` advances once from 12 to 13 with one registered `12 -> 13` migration. Migration executes inside the existing initialization transaction; a failure rolls back all DDL/data changes. No production database is touched during development.

The migration adds the following concrete structures:

1. `daily_plans.question_reveal_mode TEXT NOT NULL DEFAULT 'manual'`, `answer_reveal_delay_minutes INTEGER NOT NULL DEFAULT 0`, and `explanation_reveal_delay_minutes INTEGER NOT NULL DEFAULT 0`. Domain validation accepts only `manual` or `delayed`; delays are bounded non-negative minutes. These settings are meaningful only for `daily_question`; a case plan serializes no reveal fields. Manual mode or a zero delay creates no automatic job for that reveal kind.
2. `scheduled_reveal_jobs`: `id`, `session_id` (`question_sessions` FK, cascade), `target_id` (nullable target FK, set-null), immutable `target_umo`, `snapshot_hash`, `question_sent_at`, `due_at`, `reveal_kind` (`answer`/`explanation`), `status` (`pending`/`sending`/`sent`/`skipped`/`failed`/`needs_review`), `attempt_count`, `attempted_at`, `finished_at`, `page_progress_json`, bounded `error_summary`, and `created_at`/`updated_at`. `UNIQUE(session_id,reveal_kind,prompt_index)` makes creation idempotent per subquestion. The row snapshots the exact session and body identity; it never stores regenerated answer text.
3. `event_status_overrides`: `event_id` primary/FK (cascade), `override_status` (allowlisted presentation status), `actor_id`, non-empty `reason`, `created_at`, `updated_at`. Deleting an override returns display status to evidence-derived status; it never establishes a deadline or confirms evidence.
4. `event_date_reviews`: `id`, `event_id`, nullable `event_date_id`, `evidence_hash`, `evidence_text_snapshot`, `old_value_json`, `proposed_value_json`, `review_status` (`pending`/`accepted`/`rejected`), `actor_id`, `reason`, `created_at`, `resolved_at`. Review cannot rewrite source evidence or make unconfirmed dates actionable. Confirmed reminder/publication eligibility still requires deterministic confirmed evidence.
5. `case_items.active INTEGER NOT NULL DEFAULT 1`, `deleted_at`, and `deleted_by`; `learning_items.deleted_at` and `deleted_by` complement the existing `active` field. Soft delete/restore preserve content, source identity, classification, structured bindings, sessions and history.
6. `structured_item_bindings.verified_real_question_id INTEGER REFERENCES real_questions(id) ON DELETE SET NULL`, `promoted_by`, and `promoted_at` record explicit exact reconciliation. No import, model output, or batch preview sets these fields.
7. `operator_action_audits`: append-only `id`, `actor_id`, `action`, `scope`, `target_ids_json`, `outcome_json`, `created_at`. It records management/promotion/clear outcomes without copying answer or raw source content. Full runtime reset clears these rows in the same transaction as other runtime data.

New state transitions are validated in repository methods and constrained in SQL where compatible with existing migration style. Existing v12 rows get conservative defaults: reveal mode is manual, delays are zero, existing cases are active, and no candidate is implicitly promoted. The migration stays one SQLite transaction, including schema_meta advancement.

## Scheduler ordering and time model

The scheduler has independent scan and due-work loops. The scan loop retains `scan_interval_minutes` and only handles configured scans/reminder discovery. It never determines daily-plan due time. The due-work loop is wakeable and asks the service for the nearest enabled local-plan time or pending reveal due time.

At startup the due loop immediately runs one reconciliation pass, then waits until the nearest due time or a wake signal. It evaluates enabled targets/tasks independently using the configured `ZoneInfo` local date and `HH:MM`. Catch-up is limited to the current local day; a previous-day occurrence is recorded as skipped/expired and is never sent late. A successful/skipped occurrence remains protected by existing `(date, target, content_type)` idempotency. Case and question occurrences have separate keys. Saving or confirming a plan commits first, then wakes/recomputes the due loop; a failed save cannot move the scheduler. Repeated `start()` does not create duplicate loops; `stop()` cancels and awaits both. A nonexistent DST wall time is normalized to the first valid local minute after it; an ambiguous wall time uses the first occurrence (`fold=0`).

Scheduled answer/explanation due times are computed from the successful daily question delivery timestamp plus the configured delay, in the plan timezone. A failed or unknown prompt delivery creates no question session and no reveal jobs. A manual reveal before the automatic due time makes the matching job `skipped`. Closed/superseded sessions are skipped. State transitions are `pending -> sending -> sent|failed|needs_review`; pending work may instead become `skipped` before send. Each page is sent separately and its successful page index is committed immediately before the next page is sent. A known failed result marks the job `failed` and retains progress for explicit operator recovery; it is not automatically replayed. A process crash while an in-flight result is ambiguous is marked `needs_review`; known-success pages are never resent automatically. This deliberately prefers no duplicate/unknown disclosure over blind retry.

## Management and batch transaction boundaries

Normal reads continue using safe DTOs. Operator-only management reads include editable content only after service-layer authorization. Mutations accept validated fields and IDs; no SQL, filesystem path, or arbitrary command surface is exposed.

Batch operations use short-lived in-memory tokens bound to authenticated actor, action, exact ordered IDs, normalized changes, and an expiry. Prepare is read-only and returns an explicit per-item preview. Confirm is one-shot and checks ownership, action, expiry, exact current IDs and current state. Ordinary homogeneous edits/soft-delete/restore use one `BEGIN IMMEDIATE` transaction and are all-or-nothing: any missing/stale row rolls back every item. Explicit candidate promotion uses one `BEGIN IMMEDIATE` transaction with a `SAVEPOINT` per selected item: each outcome is returned, successful candidates reconcile to one exact verified identity, and failed items remain candidates; no answer is inferred. Publishing and long-lived plan changes retain their own existing prepare/confirm contracts.

Soft delete sets inactive/deleted actor/time and keeps evidence, sources, bindings, snapshots and history. Restore is explicit. Existing cases gain the same reversible lifecycle without changing source identity.

## Data-clear scopes

The UI presents a fixed allowlist of clear scopes: `radar`, `library`, `verified_questions`, `cases`, `delivery_history`, `question_sessions`, `daily_plans`, `targets`, and `all_runtime`. The service maps each scope to fixed primary-key snapshots and FK-aware delete order; the browser never sends table names or SQL. `targets` means remove target bindings only after dependent history is cleared or retained with `target_id=NULL`; the preview states which. `all_runtime` clears plugin runtime rows (including operator review/action history) and anchors, but retains schema_meta and schema objects.

Prepare returns per-table counts, examples/target scope, retained data and a short-lived token bound to actor, exact scope, exact row-key snapshot and expiry. Confirm runs `BEGIN IMMEDIATE`, rechecks the exact snapshot and all foreign-key dependencies, then deletes in FK-safe order. Any mismatch/failure rolls back everything and invalidates the token. Full reset removes runtime content including plans, targets and review/action history, but retains `schema_meta` and all schema objects; it does not clear AstrBot configuration. Scheduler wake/reconciliation occurs only after commit. Backup-before-clear is documented.

## Query/API/UI contracts

List APIs return `{items, page, page_size, total, page_count}` and use bounded page sizes (20/50/100); totals are computed from the same filters as the page. Search/detail DTO safety is unchanged. The Plugin Page gets a shared pager and multi-select/table action pattern. Selection is scoped to visible rows; “select current page” is not “select all results”. High-impact batch changes always show exact count and item preview before confirmation.

The management detail path is separate from normal Library/search and Agent Tool paths. Questions expose only editable operator fields; enums use select controls. Delete is reversible. Candidate promotion has a prominent explicit identity action and reconciles through `RealQuestion.from_mapping`/the existing verified-question storage contract.

Daily Plan controls are dependency-driven: subject and question-type modes expose random/fixed/rotation controls; selected labels map to canonical index values. Origin is independent. Reveal settings exist only for daily questions. Preview is separate from save confirmation; cancel does not persist or mutate the draft.

## Truth and content rules

Mock selection uses the injected RNG's `choice` path for the random mock candidate, and uses an exact session-origin exclusion key to avoid an immediate repeat. Explicit source, subject and question-type constraints remain strict. Structured candidates stay pending until operator promotion. Structured imported cases stay `user_case/pending_review`, not official. Case/manual subject changes do not rewrite source identity.

Radar date extraction no longer guesses an arbitrary first `20xx` in body text. Trusted source publication year may anchor a date; an unsupported month/day year is review-only. Manual status override is a display/workflow overlay. Date review preserves original evidence and records actor/time/reason; it cannot silently create a confirmed deadline.

The Chinese command root `法务` routes to the same handlers as `/law`; `/law` remains compatible. Ordinary natural-language guidance does not teach slash-prefixed command syntax.

Question rendering remains lossless and answer-safe: a multi-subquestion question shows its shared top-level stem once before subquestions; each subquestion's own requirements remain attached; prompt, answer and explanation share the configured message budget. Q309-style full stem is covered by synthetic regression and restart persistence.

## Verification / release boundary

Development tests use fake clock, deterministic RNG, fake publisher/source/LLM and synthetic structured content only. Required checks: compileall, Ruff format/check, all pytest tests, Node lifecycle tests, `git diff --check`, exact AstrBot 4.22.0/4.25.0 loader smoke, extracted release-package smokes for both versions, plus Page discovery/API route smoke on 4.25. The build artifact is `astrbot_plugin_law_assistant-0.6.0.zip` with tests/docs/scripts/runtime data/secrets excluded. No real QQ send, production DB import, tag, or Release is part of the cycle.
