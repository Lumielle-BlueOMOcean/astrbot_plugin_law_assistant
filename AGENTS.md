# ChatGPT + Codex Workflow

GitHub repository state is the shared source of truth.

When Codex receives an implementation task:

1. Treat the current repository code as authoritative.
2. Read the task first, then inspect only files directly relevant to it.
3. If ChatGPT Sol has already specified architecture, files, API behavior, data structures, constraints, and acceptance criteria, treat that as the approved implementation specification unless it conflicts with the repository.
4. Do not unnecessarily redesign an already-defined solution.
5. Do not perform unrelated refactors or cleanup.
6. Keep changes focused and reasonably small.
7. If the request conflicts with actual code, dependencies, architecture, security constraints, or tests, report the conflict instead of silently replacing the design.
8. Run checks relevant to every change and report files changed, implementation, verification, results, and remaining risks.

The intended division is ChatGPT Sol for analysis, architecture, debugging reasoning, and review; Codex for implementation, command execution, testing, verification, commit, and push. Do not add an Agent system, API bridge, MCP server, background service, or issue automation for this workflow.

## Project Rules

- GitHub `main` is the shared source of truth; do not modify AstrBot Core.
- The minimum target is AstrBot 4.22.0. Do not use an API that exists only after 4.22 as a core dependency; use capability detection and graceful degradation for optional newer features.
- Do not use deprecated AstrBot APIs. Use public APIs shared by the compatibility targets, including `@filter.command`, `@filter.llm_tool`, `StarTools.get_data_dir()`, and plugin lifecycle methods.
- Runtime data must go only under `StarTools.get_data_dir("astrbot_plugin_law_assistant")`; never commit runtime SQLite, secrets, credentials, QQ tokens, caches, logs, or generated files.
- Commands, LLM tools, scheduler, and future integrations must call the same `LawAssistantService` rather than duplicate business logic.
- Law Assistant and other plugins must not read each other’s SQLite databases or import each other’s internal implementation. Nexus integration is optional and must use a stable exposed contract.
- LLM interprets evidence; deterministic code owns truth. Stored source evidence, hashes, permissions, publication state, and deduplication cannot be decided by an LLM alone.
- Side-effect operations use `prepare -> preview -> explicit confirm -> execute`; automatic scheduler publication/reminders may act only under an operator-preauthorized policy. Publication must remain idempotent by event revision, target and kind.
- Network operations must be async; do not use `requests`.
- Every new behavior needs a meaningful deterministic test. Tests must not require real QQ, real LLM providers, real websites, or production databases.
- Before commit run format, lint, tests, compile, `git diff --check`, and the two real AstrBot compatibility smokes.
- Keep module boundaries clear; do not let `main.py` or `service.py` grow without a concrete responsibility.
- Source adapters must preserve official URL, fetched evidence and content hash; one source failure is isolated and recorded in `source_runs`.
- Unconfirmed LLM dates cannot drive deadline lists, reminders or publication previews. LLM-generated learning content must be marked as study material, not legal advice.
- Learning content has three distinct identities: verified real questions, AI-generated mock questions, and official-source cases. Never label generated or unverifiable material as a real exam question or official case.
- Real questions may enter the inventory only through a source-recorded, verification-marked import. Preserve source, locator, answer provenance and verification status; an unavailable answer must not be guessed by an LLM.
- Daily case and daily question plans are independent. Global defaults may be overridden per enabled target; rotation uses the configured local calendar date, start date, ordered subjects and start index rather than an in-memory counter.
- A missing subject-matched case or question is a strict skip and must not silently fall back to another subject, type, origin or task. Case failure or disablement must not advance or block the question plan, and vice versa.
- Long-lived plan changes require a preview and explicit confirmation. Content publication also locks the selected body and exact target IDs at preview time; confirmation must not regenerate content or expand targets.
- README must distinguish implemented behavior from planned capabilities and must not claim deferred sources, advanced radar, full QQ management, or Nexus runtime integration are complete.
- User-provided unverified learning material may enter the library only with its original evidence preserved; `real_question_candidate` is not a verified real question and `user_case` is not an official case.
- LLM/Agent may summarize, structure, and classify learning material, but deterministic code owns identity, content hashes, verification state, validation, deduplication, and protected update fields.
- Learning-library writes, reads, searches, and updates must go through `LawAssistantService` and `LibraryService`; never expose arbitrary SQL, database writes, or filesystem writes as an Agent Tool.
- Controlled document imports may read only relative paths under the plugin data `imports/` directory; preserve the original asset and extracted-text hash before parsing, and never execute document macros or accept arbitrary absolute paths.
- Document segmentation must keep source locators. Ambiguous boundaries become `needs_review`; deterministic code must not invent answers, official identities, case boundaries, or source evidence.
- Ordinary learning-material archive paths cannot create `official_case`; only the trusted `court_cases`/`spp_cases` source path may grant verified official-case identity.
- Official multi-case articles must be split into independent items before daily-case selection or publication. Shared case-card formatting enforces a bounded reading budget and returns `content_too_long` instead of truncating evidence-bearing content or sending a whole collection.
- Activity Radar status is derived from stored confirmed evidence and the configured local clock; default current queries exclude historical and needs-review notices. Automatic activity publication additionally requires an allowed source policy, current status, and a confirmed registration or submission deadline.
- Daily learning selection must use the shared deterministic resolver and `LearningContentProvider`: subject rotation and question-type rotation are independent, persistent mock inventory is reusable, and an unmet explicit origin/subject/type constraint is a recorded strict skip rather than a fallback.
- Activity source policy is two-dimensional: `discover_enabled` must be checked before adapter fetch, independently from `auto_publish_enabled`; disabling discovery must not delete or rewrite stored history.
- Scheduler-only learning selection may exclude successful `(source_kind, source_item_key)` identities scoped to the target and content type. Manual selection must not inherit another target's history, and failed/skipped attempts are not used-history.
- In `DailyPlan.from_mapping`, an explicitly supplied question-type mode has priority over legacy `question_type`: only legacy-only input implies fixed mode; explicit `random` stays random and explicit `rotation` requires its own valid rotation list.
- Keep schema migrations sequential and transactional; schema version 9 fields for daily content identity and cross-source publication state must remain backward-compatible with older daily plans.
- Plugin Pages are another LawAssistantService entrypoint; Web API handlers must not create a second business-logic path or expose arbitrary SQL/exec/file APIs.
- Treat all Plugin Page user and source text as untrusted; render it as text, validate URLs, and never inject it as HTML.
- Web uploads may only be staged below the plugin data directory `imports/`; prepare/confirm writes must verify the staged hash and use one-time short-lived tokens.
- AstrBot 4.25+ may expose the embedded Plugin Page, while AstrBot 4.22 compatibility must continue loading the plugin and Web API registration without requiring the page bridge.
- Release ZIPs are built from tracked plugin files and must not include runtime data, tests, development scripts, caches, secrets, or SQLite files.
