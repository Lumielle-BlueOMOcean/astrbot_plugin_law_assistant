# 微光·法务助手 / Lumielle Law Assistant

AstrBot QQ 法律信息助手。`0.2.0` 是核心产品阶段：以官方网页证据为输入，使用确定性代码保存事件、时间线、来源运行记录和更新记录；LLM 只做受证据约束的解释与学习内容生成。

## Implemented

- AstrBot 插件生命周期、`aiocqhttp` 支持、私聊 `/law` 命令和 LLM Tools。
- 法律硕士活动来源：China-JM 通知列表，以及可配置的同主机通知列表页。
- HTML/PDF 异步抓取、编码处理、公告正文提取和规则优先的活动识别。
- 报名/投稿截止、初赛、复赛、决赛等多节点时间线；原文 evidence 和确认状态随事件保存。
- SQLite schema migration、来源文档、事件 revision、确定性 upsert 和首次发现时间保留。
- 最高人民法院/最高人民检察院案例列表接口与案例学习内容接口（需要可用 LLM provider 才生成解读）。
- 可选法律法规更新列表接口。
- 已绑定目标的 DDL 提醒；新事件自动发布可由 operator 预授权开启。
- 发布操作默认 `prepare → preview → explicit confirm → execute`，按事件 revision、目标和发布类型幂等。
- 自动 scheduler 默认关闭；启用后统一调用 service，reload 会取消任务。
- Python 3.12 单元测试、ruff、compile 和真实 AstrBot 4.22.0/4.25.0 loader smoke。

## Planned / deferred

- 更广泛的法律硕士赛事来源和跨来源实体解析。
- 复杂 LLM 法律通知抽取、完整法规数据库、向量/RAG 和 dashboard。
- 细粒度 QQ 群绑定管理、特殊 OneBot 消息和 Nexus runtime integration。

## Architecture

```text
Private command ─┐
LLM Tools ────────┼──> LawAssistantService ──> Source / Extraction ──> SQLite
Scheduler ────────┘                 └──────> Publisher
Future Nexus integration (optional) ────────┘
```

四入口共享同一个 `LawAssistantService`。服务与 `AstrMessageEvent` 解耦；Nexus 是可选集成，Law Assistant 独立运行，不读取 Nexus SQLite，也不依赖 Nexus 内部实现。

LLM interprets evidence; deterministic code owns truth：来源 URL、原文 hash、日期 evidence、权限、是否已发布和去重均由代码/持久化状态决定。

## Compatibility

目标范围为 `>=4.22.0,<5`，Python `>=3.12`。CI 使用官方源码 exact commit 验证：

- AstrBot v4.22.0 — `81c7b0f7150485beb6124a7ec524a8d8534e7f6e`
- AstrBot v4.25.0 — `02291a3217c92faa0c577bf9d89076949c40954c`

## Installation

### WebUI URL install

AstrBot WebUI → Plugins → Install from URL：

`https://github.com/Lumielle-BlueOMOcean/astrbot_plugin_law_assistant`

### Git clone

```bash
git clone https://github.com/Lumielle-BlueOMOcean/astrbot_plugin_law_assistant.git data/plugins/astrbot_plugin_law_assistant
```

## Configuration

配置集中由 `config.py` 解析，完整字段见 `_conf_schema.json`。

- `operator_ids`：可私聊执行管理操作的 QQ 用户 ID；AstrBot Admin 同样有效。
- `timezone`：默认 `Asia/Shanghai`。
- `auto_scan_enabled`：默认 `false`；开启后 scheduler 扫描、案例/法规来源和提醒。
- `extra_event_source_urls`：可选活动通知列表页。
- `case_source_court_enabled` / `case_source_spp_enabled`：官方案例来源开关。
- `law_update_enabled`：法规更新来源开关，默认关闭。
- `auto_publish_events`：默认关闭；只有管理员明确配置后才自动发布。
- `deadline_reminder_days` / `deadline_same_day_enabled`：DDL 提醒策略。
- `llm_provider_id`：可选；留空按当前会话或宿主默认 provider。

运行时数据库只写入：

```text
data/plugin_data/astrbot_plugin_law_assistant/law_assistant.sqlite3
```

该数据库、凭据、API key、QQ token、日志和缓存均不进入 Git。

## Commands and LLM Tools

管理命令需要 AstrBot Admin 或 configured operator。扫描、查询、发布和确认要求私聊；`bind`/`unbind` 可由管理员或 operator 在群聊中绑定当前会话目标。

```text
/law status
/law scan
/law events
/law event <id>
/law deadlines
/law sources
/law targets
/law bind [unified_msg_origin]
/law unbind [unified_msg_origin]
/law publish <id>
/law confirm <token>
/law case
/law question [subject]
/law laws
/law help
```

可用 Tools 包括：`law_status`、`law_scan_events`、`law_list_events`、`law_get_event`、`law_list_deadlines`、`law_get_daily_case`、`law_generate_question`、`law_list_law_updates`、`law_prepare_publish_event`、`law_confirm_publish`。所有 Tool 仍执行相同权限检查。

## Development

```bash
python -m pip install -r requirements.txt
python -m compileall .
ruff format --check .
ruff check .
pytest
git diff --check
```

测试使用 deterministic fakes，不依赖真实 QQ、LLM provider、外部网站或生产数据库。可选 live smoke 只读取公开网页，不写数据库、不发送消息：

```bash
python scripts/live_smoke.py
```

`.github/workflows/ci.yml` 在 `push main` 和 pull request 上运行质量/单元检查，并运行 4.22.0、4.25.0 两个真实 AstrBot loader compatibility job；required failure 不用 `continue-on-error` 隐藏。
