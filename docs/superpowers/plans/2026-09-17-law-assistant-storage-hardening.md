# Law Assistant Storage Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 修复 schema 版本迁移语义、保留事件首次发现时间，并让 CI 检查已提交 commit 的 whitespace。

**Architecture:** 保持现有 `LawAssistantService -> SQLiteStorage` 边界不变。`SQLiteStorage` 使用事务内的 sequential migration foundation：读取当前版本、拒绝未来版本、逐步执行迁移并在成功后更新 metadata；事件 upsert 仅更新可变字段。

**Tech Stack:** Python 3.12、stdlib `sqlite3`、pytest、Ruff、GitHub Actions。

**Spec:** 用户提供的 Phase 1 Code Review 修复要求（当前任务 prompt）。

## Global Constraints

- 保持 `SCHEMA_VERSION = 1`。
- 兼容目标保持 `>=4.22.0,<5`。
- 不实现真实信息源、LLM extraction、QQ 发布、Nexus integration 或架构重写。
- 不改变现有 command / LLM Tool 名称与授权规则。

### Task 1: Add regression tests for schema and discovery semantics

**Files:**
- Modify: `tests/test_storage.py`

**Steps:**

- [x] Add tests for fresh initialization, reopen persistence, version 0 migration, future-version rejection, and preservation of the first `discovered_at` across deterministic upsert.
- [x] Run the focused storage tests and confirm the new tests fail against the baseline implementation for the intended reasons.

### Task 2: Implement sequential schema migration and immutable first discovery

**Files:**
- Modify: `storage.py`

**Steps:**

- [x] Add a transaction-bounded schema bootstrap and migration dispatcher that creates version 0 metadata/schema, applies migration `0 -> 1`, updates metadata only after migration success, accepts existing version 1, and rejects versions greater than 1 without mutation.
- [x] Remove unconditional version overwrite behavior.
- [x] Remove `discovered_at` from the existing-event UPDATE assignment while retaining it for INSERT.
- [x] Run focused storage tests, then the full unit test suite.

### Task 3: Harden committed-content whitespace validation

**Files:**
- Modify: `.github/workflows/ci.yml`

**Steps:**

- [x] Replace the clean-checkout `git diff --check` step with `git show --check --format= HEAD`.
- [x] Keep compile, Ruff, pytest, and both real AstrBot compatibility matrix jobs unchanged.

### Task 4: Verify, commit, push, and re-check remote state

**Files:**
- No additional production files.

**Steps:**

- [x] Run compileall, Ruff format check, Ruff check, pytest, local `git diff --check`, and both exact AstrBot compatibility smoke commands.
- [x] Scan the diff and repository for secrets, runtime databases, caches, generated files, and whitespace errors.
- [ ] Commit with `fix: harden storage migration semantics` and push `origin main` without force push.
- [ ] Verify local clean status, branch tracking, remote `main` SHA, and GitHub Actions run for the new SHA.
