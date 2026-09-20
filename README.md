# 微光·法务助手 / Lumielle Law Assistant

AstrBot QQ 法律信息助手。`0.2.0` 支持活动、官方案例、法规更新、学习题目和多群定向发布；以来源证据和 SQLite 状态为事实基础，LLM 只做受证据约束的解释或原创模拟题生成。

## Implemented

- AstrBot 插件生命周期、`aiocqhttp` 支持、私聊 `/law` 命令和 LLM Tools。
- 法律硕士活动来源：China-JM 通知列表，以及可配置的同主机通知列表页。
- HTML/PDF 异步抓取、编码处理、公告正文提取和规则优先的活动识别。
- 报名/投稿截止、初赛、复赛、决赛等多节点时间线；原文 evidence 和确认状态随事件保存。
- SQLite schema migration、来源文档、事件 revision、确定性 upsert 和首次发现时间保留。
- 最高人民法院/最高人民检察院案例列表接口与案例学习内容接口（需要可用 LLM provider 才生成解读）。
- 真题 JSON 导入、核验状态、考试定位、答案来源和方向/题型检索；仓库不内置未经授权的商业题库。
- 真题、模拟题和官方案例使用独立身份标注；模拟题不会被标为官方真题，缺少答案证据时不会由 LLM 冒充官方答案。
- 学习资料库基础：私聊中明确要求收藏的文本资料会保存原文、确定性 hash、归属人和结构化学习条目；支持 `user_case`、`real_question_candidate`、持久化 `mock_question` 和 `note`。
- 学习资料库支持 Agent Tool 搜索、读取和有限人工更新；同一原文可以复用 Source，但不同整理结果、方向和备注不会互相覆盖。
- 受控目录资料导入：管理员可导入 UTF-8/常见中文编码 TXT、标准 DOCX 和文本型 PDF；原件保存在插件数据目录，提取文本保留页码/段落/行号定位，题目和案例按可确定边界拆成独立条目并返回待复核统计。
- 最高法/最高检受信来源的合集文章会通过独立官方案例入口按原文案例标题拆分；每日一案优先使用独立 `official_case`，并通过统一学习卡片长度预算阻止过长内容发送。
- 题目来源/方向/题型的统一选择规则，支持 `real`、`mock`、`random` 和单选、多选、判断、简答、案例分析。
- 案例和题目各自独立的每日任务计划：随机、固定或按日期轮换；支持全局默认、群级覆盖、学校四方向预设和未来安排预览。
- 已绑定多个群的名称/别名解析、明确目标的赛事/题目/案例发布预览和一次性确认 token。
- 可选法律法规更新列表接口。
- 已绑定目标的 DDL 提醒；新事件自动发布可由 operator 预授权开启。
- 发布操作默认 `prepare → preview → explicit confirm → execute`，按事件 revision、目标和发布类型幂等。
- 自动 scheduler 默认关闭；计划在运行中经确认启用后会唤醒同一个 scheduler，不需要手动 reload；reload/terminate 会取消任务。
- Python 3.12 单元测试、ruff、compile 和真实 AstrBot 4.22.0/4.25.0 loader smoke。

## Planned / deferred

- 更广泛的法律硕士赛事来源和跨来源实体解析。
- 复杂 LLM 法律通知抽取、完整法规数据库、向量/RAG 和 dashboard。
- 更广泛的题库内容和需要用户授权的真实题目数据；本仓库当前不声称拥有完整真题库。
- 直接接收 QQ 文件附件、旧版 DOC、扫描 PDF/OCR、完整 WebUI，以及 `real_question_candidate` 到 `verified_real_question` 的人工审核流。
- 新资料库尚未接管每日案例/每日一题；当前每日任务仍使用现有官方案例和 verified real question 体系。
- 复杂 LLM 法律通知抽取、完整法规数据库、向量/RAG、dashboard、特殊 OneBot 消息和 Nexus runtime integration。

## Architecture

```text
Private command ─┐
LLM Tools ────────┼──> LawAssistantService ──> Source / Extraction ──> SQLite
Scheduler ────────┘                 └──────> Publisher
Future Nexus integration (optional) ────────┘
```

四入口共享同一个 `LawAssistantService`；学习资料库通过该 facade 进入独立的 `LibraryService`/`LibraryRepository`。服务与 `AstrMessageEvent` 解耦；Nexus 是可选集成，Law Assistant 独立运行，不读取 Nexus SQLite，也不依赖 Nexus 内部实现。

LLM interprets evidence; deterministic code owns truth：来源 URL、原文 hash、日期 evidence、题目来源与核验状态、权限、是否已发布和去重均由代码/持久化状态决定。

## Compatibility

目标范围为 `>=4.22.0,<5`，Python `>=3.12`。Windows 会通过 `requirements.txt` 安装 `tzdata`，为 `zoneinfo` 提供 `Asia/Shanghai` 等 IANA 时区数据。CI 使用官方源码 exact commit 验证：

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
- `auto_scan_enabled`：默认 `false`；开启后 scheduler 扫描活动、案例/法规来源和提醒。
- `daily_case_enabled` / `daily_question_enabled`：独立开启每日案例或每日题 scheduler；不要求同时开启活动扫描。
- `extra_event_source_urls`：可选活动通知列表页。
- `case_source_court_enabled` / `case_source_spp_enabled`：官方案例来源开关。
- `law_update_enabled`：法规更新来源开关，默认关闭。
- `auto_publish_events`：默认关闭；只有管理员明确配置后才自动发布。
- `deadline_reminder_days` / `deadline_same_day_enabled`：DDL 提醒策略。
- `llm_provider_id`：可选；留空按当前会话或宿主默认 provider。
- `daily_question_origin` / `daily_question_subject` / `daily_question_type`：全局每日一题默认来源、方向和题型，默认均为随机。
- `daily_case_selection_mode`、`daily_question_selection_mode`：`random`、`fixed` 或 `rotation`。
- `*_rotation_subjects`、`*_rotation_start_date`、`*_rotation_start_index`：案例和题目各自独立的有序轮换列表及起点。

选择 `rotation` 并留空起始日期时，首次启用会按配置时区当天建立持久化锚点；之后重启不会重新计算，案例和题目分别保存。明确填写的新起始日期及已确认的群级/全局计划优先。

内置学校四方向预设的顺序是：知识产权 → 民商法 → 司法实务 → 经济法。预设不是默认行为，需在配置或确认计划变更时主动应用。

群级计划保存于运行时 SQLite：群级有明确值时只覆盖对应群、对应内容类型；未覆盖的内容类型继续继承全局默认。计划变更必须先预览，再使用确认 token 保存。

运行时数据库只写入：

```text
data/plugin_data/astrbot_plugin_law_assistant/law_assistant.sqlite3
```

当前 schema version 为 `8`。v5→v6 建立学习资料库表，v6→v7 调整来源唯一性以保留同一原文对应的不同 URL，v7→v8 增加待复核记录和官方案例拆分结果的 active 状态；不迁移旧 `CaseItem`、`RealQuestion` 或历史发送内容。升级前请备份运行时数据库，安装包不覆盖现有数据库。

该数据库、凭据、API key、QQ token、日志和缓存均不进入 Git。

## Commands and LLM Tools

管理命令需要 AstrBot Admin 或 configured operator。扫描、查询、发布和确认要求私聊；`bind`/`unbind`/`rename` 可由管理员或 operator 在群聊中操作当前会话目标。DDL 提醒使用配置时区的本地日历日期。

```text
/law status
/law scan
/law events
/law event <id>
/law deadlines
/law sources
/law targets
/law bind [当前群别名]
/law bind-umo <unified_msg_origin> [群别名]
/law unbind [unified_msg_origin]
/law rename <目标群名或 ID> <新别名>
/law publish <id>
/law confirm <token>
/law case [方向]
/law question [real|mock|random] [方向] [题型]
/law import <imports目录相对路径> [case|mock_question|real_question_candidate]
/law plans [群名]
/law question-import <JSON路径>
/law review [来源ID]
/law review-get <待复核ID>
/law review-status <待复核ID> <pending|resolved|superseded>
/law case-tag <案例ID> <方向...>
/law laws
/law help
```

群内 `/law bind 法硕一群` 会把当前群的真实 `unified_msg_origin` 与别名一起保存；私聊绑定其他目标必须使用明确的 `/law bind-umo <unified_msg_origin> [群别名]`，插件不会根据群名猜 UMO。已绑定群可用 `/law rename <目标群名或 ID> <新别名>` 改名，或在当前群使用 `/law rename <新别名>`。

自然语言可以直接说“给我一道知识产权真题多选题”“给我一个刑法案例”“把这道题发到一群和二群”“看看未来七天一群的安排”。`law_get_daily_case` 与 `law_generate_question` 返回短期、绑定操作者和私聊会话的 `content_ref`；随后发布 Tool 传入该引用即可发布刚才看到的同一条内容，不会重新随机或生成。若明确要求重新出题，则不传引用。所有 Tool 仍执行私聊、Admin/operator 权限检查。

题目和案例的实际群发送统一为：选择内容 → 选择目标 → 固定正文预览 → 明确确认 → 发送。确认阶段不重新调用 LLM、不重新随机选择，也不会扩大预览中的目标群。

可用 Tools 包括：`law_status`、`law_scan_events`、`law_list_events`、`law_get_event`、`law_list_deadlines`、`law_get_daily_case`、`law_generate_question`、`law_question_inventory`、`law_archive_learning_material`、`law_import_learning_document`、`law_search_learning_library`、`law_get_learning_item`、`law_update_learning_item`、`law_list_targets`、`law_rename_target`、`law_get_daily_plans`、`law_prepare_publish_event`、`law_prepare_publish_question`、`law_prepare_publish_case`、`law_confirm_publish`、`law_prepare_daily_plan_update`、`law_confirm_daily_plan_update` 和 `law_list_law_updates`。

### 学习资料库

只有用户明确说“收藏”“保存”“归档”“加入题库”或“记下来”时，Agent 才应调用 `law_archive_learning_material`。用户提供的原文会原样保存到同一个 SQLite 数据库，并与 Agent 整理出的摘要、争议焦点、题干、选项或学习要点分开保存。

资料身份由后端确定：普通案例为 `user_case`，未核验题目为 `real_question_candidate`，原创练习题为 `mock_question`。Tool 不能创建 `official_case` 或 `verified_real_question`，也不会因为 structured JSON 中出现 `official` 或 `verified` 就提升身份。现有 verified 真题 JSON 导入路径继续独立运行。

`law_search_learning_library` 支持标题、原文、摘要、题干、学习要点和方向的普通文本查询；`law_get_learning_item` 返回完整原文与结构化条目；`law_update_learning_item` 只允许修改标题、方向、备注、案例学习要点或题目解析，不能修改创建者、来源 hash 或核验身份。四个 Tool 都要求私聊及 AstrBot Admin/operator 权限。

### 真题导入格式

管理员可在私聊执行 `/law question-import <path>`，导入用户合法取得且已核验的 JSON；带空格的路径请使用引号。文件可以是数组，也可以是 `{ "questions": [...] }`。每条记录至少需要：`source_name`、`exam_name`、`subject`、`question_type`、`stem`，以及 `source_url`、`source_locator` 或 `question_number` 之一；还必须明确 `verification_status` 为 `verified`、`official` 或 `user_verified`。导入命令会返回新增数量和当前库存；仓库不内置伪造或未经授权的真题。

自然语言修改每日计划时，服务会先返回作用群、案例/题目范围、来源/题型、方向模式及未来安排预览，只有明确确认后才保存。计划的参数若明确提供了不支持的方向、题型、来源或选择模式会直接报错，不会静默改成随机；rotation 未提供起始日期时按配置时区的当天日期起算。案例与每日一题分别持有自己的全局默认和群级覆盖，暂停一项不会停止其他群或另一种内容。

```json
{
  "questions": [
    {
      "source_name": "用户合法取得题库",
      "exam_name": "某考试试卷",
      "exam_year": "2024",
      "paper": "专业课",
      "question_number": "1",
      "source_url": "https://example.test/question/1",
      "subject": "知识产权",
      "question_type": "多选",
      "stem": "题干",
      "options": ["A", "B", "C", "D"],
      "answer": ["A"],
      "answer_source": "official",
      "verification_status": "verified"
    }
  ]
}
```

插件会保存题目 hash、来源定位和答案身份；没有可靠答案时只显示“未提供”，不让模型猜成官方答案。当前仓库没有随代码提交真实题库，安装后真题数量取决于管理员导入的数据。

### 受控文档导入与官方案例拆分

先将文件复制到运行时目录：

```text
data/plugin_data/astrbot_plugin_law_assistant/imports/
```

然后在管理员私聊执行，例如：

```text
/law import "试卷 with spaces.txt" real_question_candidate
/law import "官方案例合集.docx" case
```

`auto` 会根据题号或案例标题尝试确定类型；对无法可靠确定边界的片段会保留原件并标记 `needs_review`，不会凭空拆分或补写答案。导入支持 `.txt`、`.md`、标准 `.docx` 和文本型 `.pdf`，单文件默认上限 20 MB、PDF 200 页、提取文本 600000 字符、单次 100 个候选条目。旧版 `.doc`、扫描件/OCR 和 QQ 文件附件不在本轮实现范围。

试卷拆分支持 `第1题`、`第 1 题`、`1、`、`1.`、`（一）` 等明确题号，以及文末“答案/参考答案/答案与解析”表和逐题附答案。独立答案表不计入题干定位；答案无法与题号确定对应时会创建持久化待复核记录。管理员可用 `/law review [来源ID]` 查看待复核条目，用 `/law review-get <ID>` 查看原文片段和定位，用 `/law review-status <ID> resolved` 或 `superseded` 维护状态。待复核候选不会进入普通题库或每日案例选择。

原件复制到插件专属 `assets/<sha256>.<ext>`，数据库来源记录保存文件 hash、提取文本 hash、解析状态、警告和每个条目的定位。重复导入同一文件不会重复复制原件或生成相同条目；部分条目失败不会回滚已经成功归档的条目。安装或升级前应备份运行时 SQLite；安装包不覆盖现有数据库。

最高法/最高检适配器取得的真实官方文章才可进入 `official_case` 入口。文章导语不会作为案件；有明确单案标题和案情/裁判结构的文章可以形成一个条目，能识别的每个“案例一/案例二”等独立条目均保留官方文章 URL 和文章内定位；边界不清的片段会持久化为待复核并跳过发布。拆分规则版本变化后，同一官方来源会重新处理，旧的拆分条目停用但历史发送记录不补发；管理员人工方向标签会保留。每日案例与手动案例预览只针对一个独立案例，案例卡片默认 1800 字符，超过预算且无法在证据约束下压缩时返回 `content_too_long`，不发送整篇合集。

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

本轮的 DOCX、文本型 PDF、受控目录导入和官方合集拆分均使用本地 fixture 验证；真实 Windows AstrBot、QQ 文件附件、真实 QQ 群发布、外部官网实时抓取和真实 LLM provider 仍需后续实机验收。
