# Law Assistant Phase 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real AstrBot 4.22-compatible Law Assistant foundation with unified service entrypoints, SQLite persistence, extensible source/extraction contracts, scheduler lifecycle, tests, and CI compatibility smoke.

**Architecture:** `main.py` owns AstrBot lifecycle, dependency wiring, command handlers, and LLM tools. Commands, tools, and scheduler all enforce the same private-operator authorization and delegate to `LawAssistantService`; the service owns the source pipeline and uses focused storage, publisher, and scheduler components. Runtime SQLite lives below `StarTools.get_data_dir(PLUGIN_NAME)` and no Nexus runtime dependency is introduced.

**Tech Stack:** Python 3.12, AstrBot public APIs available in v4.22.0, stdlib `sqlite3`, `asyncio`, `hashlib`, `dataclasses`, `pytest`, `pytest-asyncio`, and Ruff.

**Spec:** User-provided `PHASE 1 — FOUNDATION / INITIALIZATION` construction specification in the task prompt.

## Global Constraints

- `metadata.yaml` must declare `astrbot_version: ">=4.22.0,<5"`.
- The compatibility targets are AstrBot commits `81c7b0f7150485beb6124a7ec524a8d8534e7f6e` and `02291a3217c92faa0c577bf9d89076949c40954c`.
- Python baseline is 3.12; no support code is added for Python versions AstrBot does not support.
- Runtime data must be stored under `StarTools.get_data_dir(PLUGIN_NAME)`, never in the source tree.
- Commands, LLM tools, scheduler, and future integrations delegate to `LawAssistantService`.
- No `requests`, production LLM extraction, real external sources, Nexus dependency, web dashboard, RAG, vector database, or extra agent/service infrastructure.
- Source failures are isolated and recorded; scheduler cancellation and lifecycle behavior remain correct.
- Secrets, credentials, caches, generated files, `__pycache__`, and runtime SQLite are excluded from Git.
- Before completion run compile, Ruff format/check, pytest, `git diff --check`, and both compatibility smokes.

---

### Task 1: Repository metadata, configuration, and developer contract

**Files:**
- Create: `.gitignore`, `metadata.yaml`, `_conf_schema.json`, `requirements.txt`, `AGENTS.md`, `README.md`.
- Test: `tests/test_config.py`.

**Interfaces:**
- Produces `PluginConfig.from_mapping(mapping) -> PluginConfig` with normalized operator IDs, timezone, auto-scan flag, and bounded interval.

- [ ] **Step 1: Write failing configuration tests** for defaults, operator normalization, invalid interval, and invalid timezone fallback.
- [ ] **Step 2: Run `pytest tests/test_config.py -q` and confirm the missing `config` module/API failure.
- [ ] **Step 3: Implement `PluginConfig` and the schema with no configuration lookups outside `config.py`.
- [ ] **Step 4: Run the focused tests and confirm they pass.
- [ ] **Step 5: Add metadata, README, AGENTS workflow/truth/side-effect rules, and a conservative ignore list.

### Task 2: Domain model, source contracts, and SQLite storage

**Files:**
- Create: `models.py`, `sources/__init__.py`, `sources/base.py`, `storage.py`.
- Test: `tests/fakes.py`, `tests/test_storage.py`, `tests/test_models.py`.

**Interfaces:**
- `EventDate(kind, datetime, timezone, label, evidence_text)` and `LegalEvent(source_key, source_item_key, title, source_url, organizer, event_type, eligibility, status, raw_content_hash, discovered_at, updated_at, metadata, dates, id=None)` are immutable-friendly dataclasses.
- `SourceDocument` carries source/item keys, URL, title, raw content, fetch time, content hash, and metadata.
- `SourceAdapter.fetch() -> list[SourceDocument]` and `Extractor.extract(document) -> list[LegalEvent]` are async-capable contracts with deterministic fake implementations for tests.
- `SQLiteStorage(path)` initializes/migrates schema version 1 and exposes `upsert_event`, `list_events`, `get_event`, `record_source_run`, and `close`.

- [ ] **Step 1: Write failing tests** for deterministic event upsert, event dates, source-run success/failure, schema version, and persistence after reopening.
- [ ] **Step 2: Run `pytest tests/test_storage.py tests/test_models.py -q` and confirm the missing production APIs fail.
- [ ] **Step 3: Implement the dataclasses, source protocols, schema migration, transactional upsert, and JSON metadata serialization.
- [ ] **Step 4: Run the focused tests and confirm they pass, including a reopened connection.

### Task 3: Service pipeline and publisher boundary

**Files:**
- Create: `service.py`, `publisher.py`.
- Test: `tests/test_service.py`, `tests/test_publisher.py`.

**Interfaces:**
- `ScanResult` reports trigger, source count, discovered count, upserted count, failures, and duration.
- `LawAssistantService.status()`, `scan_events(trigger="manual")`, `list_events(limit=20)`, and `get_event(event_id)` are async service operations independent of `AstrMessageEvent`.
- A service instance serializes scans with an async lock, registers adapters/extractors, isolates source errors, upserts by `(source_key, source_item_key)`, and records each source run.
- `Publisher.publish_text(destination, text)` is an abstraction; `FakePublisher` records calls without external effects.

- [ ] **Step 1: Write failing tests** for zero-source scans, successful fake pipeline, failure isolation, duplicate upsert, parallel-scan protection, list/get, and fake publisher behavior.
- [ ] **Step 2: Run the focused tests and confirm they fail because `service.py` and `publisher.py` are absent.
- [ ] **Step 3: Implement the minimal service and publisher contracts, keeping storage and source code out of the service entrypoint handlers.
- [ ] **Step 4: Run the focused tests and confirm they pass.

### Task 4: Scheduler lifecycle

**Files:**
- Create: `scheduler.py`.
- Test: `tests/test_scheduler.py`.

**Interfaces:**
- `LawAssistantScheduler(service, enabled, interval_minutes, sleep=asyncio.sleep)` exposes `start()`, `stop()`, and an internal loop that calls `service.scan_events(trigger="scheduler")`.
- Disabled schedulers create no task; enabled schedulers create one task, swallow/log one-round failures, never overlap service scans, and cancel/await the task during stop.

- [ ] **Step 1: Write failing lifecycle tests** for disabled mode, enabled start, termination cancellation, and scan exception survival.
- [ ] **Step 2: Run `pytest tests/test_scheduler.py -q` and confirm the missing scheduler API failure.
- [ ] **Step 3: Implement the cancellable scheduler with `asyncio.create_task`, `CancelledError` propagation during stop, and per-round exception logging.
- [ ] **Step 4: Run the focused tests and confirm they pass without leaked tasks.

### Task 5: AstrBot plugin wiring, command, and LLM tools

**Files:**
- Create: `main.py`.
- Modify: `tests/fakes.py`.
- Test: `tests/test_plugin.py`.

**Interfaces:**
- `LawAssistant(Star)` wires config, storage, service, publisher, and scheduler; `initialize()` starts the scheduler and `terminate()` stops it and closes storage.
- `@filter.command("law")` handles `status`, `scan`, `events`, and `help`; control operations require private chat plus AstrBot admin or configured operator ID, and group requests return the private-chat instruction.
- `@filter.llm_tool(name="law_status")`, `law_scan_events`, and `law_list_events` use AstrBot v4.22 docstring type syntax and call the same service methods after the same authorization check.

- [ ] **Step 1: Write failing plugin tests** for import, lifecycle, private/admin/operator authorization, group denial, command delegation, and all three LLM tools delegating to the same service methods.
- [ ] **Step 2: Run the focused plugin tests and confirm registration/wiring failures.
- [ ] **Step 3: Implement `main.py` as lifecycle/wiring/entrypoint code only, using `from astrbot.api...` public imports confirmed in v4.22.
- [ ] **Step 4: Run plugin tests and the full unit suite; fix only behavior covered by failing tests.

### Task 6: Real AstrBot compatibility smoke and GitHub Actions

**Files:**
- Create: `tests/compatibility_smoke.py`, `.github/workflows/ci.yml`.
- Modify: `README.md` if the final command/matrix wording needs exact alignment.

**Interfaces:**
- The smoke script accepts an AstrBot source path, imports the real source package, imports the plugin through the loader-compatible `data.plugins.<plugin>.main` namespace, confirms handler/tool registration, instantiates the plugin with a minimal real-API-compatible context, and exercises initialize/terminate.
- CI has separate `quality` and `astrbot-compatibility` jobs, Python 3.12, required non-ignored failures, and a matrix for the two exact commits.

- [ ] **Step 1: Write the smoke assertions and CI YAML validation test in `tests/test_compatibility_smoke.py`.
- [ ] **Step 2: Run the new smoke test against the downloaded AstrBot v4.22 source and confirm it fails only on the not-yet-wired plugin/loader behavior.
- [ ] **Step 3: Implement the source-based smoke harness without providing a fake `astrbot` module or claiming a stub is compatibility.
- [ ] **Step 4: Run the v4.22 smoke, then run the v4.25 smoke using the exact commit source; record PASS/FAIL honestly.
- [ ] **Step 5: Add CI commands for compileall, Ruff format/check, pytest, diff check, and the same two real-source smoke targets.

### Task 7: Final verification, commit, push, and boundary audit

**Files:**
- Modify only files already listed above unless verification reveals a direct defect.

- [ ] **Step 1: Run `python -m compileall .`, `ruff format --check .`, `ruff check .`, `pytest`, and `git diff --check` freshly.
- [ ] **Step 2: Run both exact compatibility smokes and inspect their full exit status/output.
- [ ] **Step 3: Audit tracked files for secrets, runtime SQLite, caches, test temp files, and unrelated generated output.
- [ ] **Step 4: Confirm `git status`, `git diff --stat`, and the staged whitelist contain only the foundation implementation.
- [ ] **Step 5: Commit with `feat: initialize law assistant plugin foundation`.
- [ ] **Step 6: Push the commit to `origin main` without force push.
- [ ] **Step 7: Re-run `git status`, `git remote -v`, `git branch -vv`, and `git rev-parse HEAD`; report the exact SHA and any compatibility limitation.
