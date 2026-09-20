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
