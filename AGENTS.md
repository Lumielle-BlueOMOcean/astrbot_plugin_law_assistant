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
- Future side-effect operations use `prepare -> preview -> explicit confirm -> execute`; an automatic scheduler may act only under an operator-preauthorized policy. Phase 1 does not implement a complex confirmation workflow.
- Network operations must be async; do not use `requests`.
- Every new behavior needs a meaningful deterministic test. Tests must not require real QQ, real LLM providers, real websites, or production databases.
- Before commit run format, lint, tests, compile, `git diff --check`, and the two real AstrBot compatibility smokes.
- Keep module boundaries clear; do not let `main.py` or `service.py` grow without a concrete responsibility.
- README must distinguish implemented behavior from planned capabilities and must not claim unfinished legal sources, daily questions/cases, radar, publishing, or Nexus integration are complete.
