# Law Assistant Phase 0.3C Design

## Scope

Phase 0.3C upgrades the existing plugin in two bounded areas: a deterministic activity radar and a unified daily-learning inventory. It preserves the current `LawAssistantService` facade, AstrBot 4.22-compatible public APIs, SQLite storage, prepare/confirm publication boundaries, and independent case/question scheduler tasks. It does not add WebUI, OCR, RAG, Nexus, a new agent system, or new runtime services.

## Activity radar

Activity lifecycle (`OPEN`, `DEADLINE_SOON`, `UPCOMING`, `CLOSED`, `UNKNOWN`) remains source-derived data. A new pure radar resolver derives `current`, `needs_review`, or `historical` from confirmed evidence, configured local date, publication age, participation evidence, and historical/result signals without rewriting source revisions when only the date changes. `/law events` and `law_list_events` default to `current`; explicit status filters expose review and historical records.

Configured keyword extensions are merged with safe built-in activity, participation, and historical keyword sets. Existing China-JM and extra URL sources remain compatible. Source policy distinguishes discovery from automatic publication; ordinary extra sources discover by default but do not auto-publish unless explicitly allowed. Automatic event publication requires current radar status, an allowed source, confirmed actionable evidence, and an unclaimed event revision/target/kind.

Strong deterministic cross-source matches use normalized title, organizer, event type, and confirmed primary deadline. Separate event rows and source URLs remain intact; a small relation table records the association and automatic publication checks the canonical group to prevent duplicate pushes. Weak matches remain separate.

## Daily learning

`DailyPlan` retains the subject axis and adds an independent question-type axis (`random`, `fixed`, `rotation`) with its own list, date anchor, and index. A shared resolver calculates both axes from the configured local calendar date, target, and content type. Preview and scheduler call the same resolver; no in-memory progress counter is used.

The learning inventory facade selects only active official cases, verified real questions, and valid active persistent mock questions. `real` is strict and skips when no exact match exists. `random` may select either valid real or mock content and reports the selected origin. Missing mock inventory may invoke the existing LLM, but the generated question is validated and persisted as `mock_question` before it can be sent. Invalid, inactive, candidate, or unverified items never enter automatic inventory.

Daily history keeps the old integer field for compatibility and additionally stores stable `source_kind`/`source_item_key`, resolved subject, question type, and origin. Case and question claims remain independently idempotent. Existing skip records are retained so deterministic no-match conditions do not retry forever in the same local day.

## Compatibility and verification

The implementation uses schema v9 migrations from v8, preserves older plan fields, updates configuration/schema/README/AGENTS, and adds deterministic tests for radar time rollover, source policy, strict inventory selection, generated mock persistence, independent rotations, stable history identity, and scheduler isolation. The final checks are compileall, Ruff format/check, pytest, diff check, real AstrBot 4.22.0 and 4.25.0 loader smokes, and CI on `main`.
