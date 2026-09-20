# Phase 0.3A Learning Library Foundation

## Goal

Add a persistent, evidence-preserving learning library to the existing Law Assistant without replacing the current event, official-case, verified-question, daily-plan, scheduler, or publication systems.

## Scope and boundaries

The new flow accepts explicitly archived plain text from an authorized private-chat operator/Admin through LLM Tools. It stores the exact source text and a separately structured learning item in the same SQLite database. It supports `user_case`, `real_question_candidate`, `mock_question`, and `note`; it never promotes user-provided content to `official_case` or `verified_real_question` based on model claims.

This phase does not add DOCX/PDF/OCR/QQ-file ingestion, official-article splitting, RAG/vector search, WebUI, new event sources, or handoff of daily tasks to the new library. Existing systems remain authoritative for official cases and verified real questions.

## Architecture

```text
LLM Tools
    -> LawAssistantService facade
        -> LibraryService (validation, identity, hash, dedup, DTOs)
            -> LibraryRepository (CRUD on SQLiteStorage-owned connection)
                -> SQLiteStorage schema v6 migrations
```

`SQLiteStorage` remains the only database lifecycle and schema owner. `LibraryRepository` receives the live connection from `SQLiteStorage`; it does not open a second database, create its own schema version, or commit independently outside the repository operation transaction. `LawAssistantService` owns the library service so commands and Tools retain the single business-entrypoint rule.

## Data model

### `library_sources`

Stores the user-provided evidence: `id`, `source_kind`, `title`, `raw_text`, `source_url`, deterministic `content_hash`, `created_at`, `created_by`, `session_origin`, optional filename/mime/storage fields, and metadata JSON. A unique `(created_by, content_hash)` constraint reuses identical source evidence for the same operator while allowing different operators to retain ownership records.

### `learning_items`

Stores reusable item identity and common fields: `id`, `item_type` (`case`, `question`, `note`), `identity`, deterministic `item_hash`, title, subjects JSON, verification status, source summary, created/updated timestamps, created_by, and metadata JSON. The item hash includes the structured archive payload and presentation fields, so sharing a source never overwrites another item’s classification, summary, note, or manual updates.

### `learning_item_sources`

Associates items and sources with `item_id`, `source_id`, locator, and relationship. This is an N:M link table with foreign keys and a uniqueness constraint for the same association.

### `learning_cases` and `learning_questions`

One-to-one extensions keyed by `item_id`. Case details store case number, authority, summary, issues JSON, reasoning, result, and practice notes JSON. Question details store question identity, type, stem, options JSON, answer JSON, explanation, exam metadata, question number, and answer source. Empty optional fields remain empty; the service never fills unsupported facts.

## Identity and validation rules

`law_archive_learning_material` accepts only `case`, `real_question_candidate`, `mock_question`, and `note`. The server maps these to deterministic identities and ignores or rejects any `identity`, `verification_status`, `official`, or `verified` values inside Agent JSON. Case archives become `user_case`; candidate questions become `real_question_candidate`; mock archives become `mock_question`.

Raw text is required and stored byte-for-byte as received. Structured JSON must be an object. Cases preserve supplied summary/issues/reasoning/practice notes. Questions require a non-empty stem and a supported question type; options, answer, explanation, and provenance are preserved without claiming official verification. Notes preserve a body or the raw text.

Exact repeated submissions by the same operator reuse the source. If the structured item is identical, the existing item is returned as a duplicate. If the same source is archived with different title, subjects, summary, notes, or question details, a distinct item is created and both items point to the same source. No semantic merging is attempted.

Search is parameterized SQLite text search over title, raw text, source summary, case details, question stem, practice notes, and subjects. Results are limited and contain stable summary fields. Get returns the complete item, linked original source, and type-specific details. Update permits title, subjects, note, case practice notes, and question explanation only; it preserves source hash, creator, identity, and verification status.

## Security and user flow

All four library Tools use the existing private-chat plus Admin/operator authorization. `created_by` is the real sender ID. Search, get, and update apply the same check; no public group writes or arbitrary SQL/file tools are added. Tool failures return stable JSON with an error code/message rather than tracebacks.

The acceptance flow is tested end-to-end with a fake authorized event: archive → receive item ID/identity → terminate plugin → create a new plugin using the same temporary SQLite → search → get → verify original text, subject, and summary. The test uses deterministic fake Tool invocation, not a real provider, but exercises the actual plugin entrypoints and persistence.

## Compatibility and regression

Schema v5 migrates to v6 by creating only the new library tables and indexes. No old `CaseItem`, `RealQuestion`, publication, or daily-plan content is migrated. Existing tests and real AstrBot loader smoke for v4.22.0 and v4.25.0 remain required.
