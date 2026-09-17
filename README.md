# 微光·法务助手 / Lumielle Law Assistant

AstrBot QQ 法律信息助手的基础框架插件。当前版本为 `0.1.0` Phase 1 foundation，目标是为后续法律硕士竞赛、活动截止日期、法规更新和提醒能力提供可靠的可加载、可测试、可扩展底座。

## Implemented in Phase 1

- AstrBot plugin lifecycle and `aiocqhttp` metadata。
- Private `/law` command with `status`、`scan`、`events`、`help`。
- `law_status`、`law_scan_events`、`law_list_events` LLM Tools。
- Private-chat plus AstrBot Admin/operator authorization for management entrypoints。
- `LawAssistantService` shared by command、LLM Tool and scheduler。
- SQLite schema version 1 with events、event timeline items and source runs。
- Deterministic upsert by `source_key + source_item_key`。
- Source adapter/document/extractor/validator contracts and deterministic fake pipeline tests。
- Optional scheduler infrastructure, default disabled and safe with zero registered production sources。
- Publisher abstraction with no external send side effect in Phase 1。
- Python 3.12 quality checks and real AstrBot v4.22.0/v4.25.0 compatibility smoke jobs in GitHub Actions。

## Planned

- Real legal master competitions and activity sources。
- Registration/submission deadline extraction and update detection。
- Operator-authorized automatic group publishing and DDL reminders。
- Daily exam-style questions and classic cases。
- Laws and judicial interpretation update radar。
- Optional Nexus integration through a stable contract。

## Architecture

```text
Private command ─┐
LLM Tools ────────┼──> LawAssistantService ──> source/extraction pipeline ──> SQLite
Scheduler ────────┘              │
                                 └────────────> Publisher boundary
```

The service is independent of `AstrMessageEvent`. AstrBot entrypoints perform authorization and formatting, then delegate to the same service methods. `LLM interprets evidence; deterministic code owns truth`: source URLs, evidence text, hashes, persistence, permissions, and deduplication remain code-owned.

Nexus integration is optional. Law Assistant works independently and does not depend on Nexus runtime code or read Nexus SQLite.

## Compatibility

The target range is `>=4.22.0,<5`, with Python 3.12. CI verifies the plugin against the official AstrBot source commits:

- v4.22.0: `81c7b0f7150485beb6124a7ec524a8d8534e7f6e`
- v4.25.0: `02291a3217c92faa0c577bf9d89076949c40954c`

## Installation

### AstrBot WebUI

Open AstrBot WebUI → Plugins → Install from URL and enter:

`https://github.com/Lumielle-BlueOMOcean/astrbot_plugin_law_assistant`

### Git clone

Clone the repository into AstrBot's plugin directory, then reload plugins:

```bash
git clone https://github.com/Lumielle-BlueOMOcean/astrbot_plugin_law_assistant.git data/plugins/astrbot_plugin_law_assistant
```

## Configuration

Configure the plugin in AstrBot WebUI. `operator_ids` is a list of QQ user IDs allowed to use private management commands in addition to AstrBot Admin. `timezone` defaults to `Asia/Shanghai`. `auto_scan_enabled` defaults to `false`; Phase 1 registers no real production sources, so enabling it currently exercises an empty scan safely. `scan_interval_minutes` defaults to 60 and is bounded to 5–1440.

Runtime SQLite is created at:

```text
data/plugin_data/astrbot_plugin_law_assistant/law_assistant.sqlite3
```

This path is runtime-only and is not part of the repository.

## Commands

Use these in a private chat as an AstrBot Admin or configured operator:

```text
/law status
/law scan
/law events
/law help
```

Group messages receive a private-chat instruction; no management scan runs in a group.

## Natural-language examples

Authorized private-chat users can ask the LLM things such as:

- “看看法律助手现在运行正常吗？” → `law_status`
- “检查一下最近有没有新的法律硕士活动。” → `law_scan_events`
- “把当前已经发现的竞赛列给我。” → `law_list_events`

The LLM tools still enforce the same private/operator authorization as `/law`.

## Privacy and side effects

Phase 1 stores only normalized event records, source evidence metadata and source-run status in the plugin data directory. It does not store API keys, provider credentials, QQ tokens or host secrets. No real source fetch or QQ group publication is enabled in this phase. Future external side effects must use `prepare -> preview -> explicit confirm -> execute`, unless an operator has explicitly pre-authorized an automatic scheduler policy.

## Development and testing

Runtime dependencies are Python standard library only in Phase 1. Install test tools in a local virtual environment and run:

```bash
python -m compileall .
ruff format --check .
ruff check .
pytest
git diff --check
```

Tests use deterministic fakes and do not require QQ, an LLM provider, external websites or a production database.

## CI matrix

`.github/workflows/ci.yml` runs quality/unit checks on Python 3.12 and a separate real-source AstrBot compatibility matrix for the exact v4.22.0 and v4.25.0 commits above. Required failures are not hidden with `continue-on-error`.
