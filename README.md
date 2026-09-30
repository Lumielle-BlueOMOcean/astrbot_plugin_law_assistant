# 微光·法务助手 / Lumielle Law Assistant

面向 AstrBot QQ 会话的法律学习与信息助手。它把可追溯的来源材料、确定性业务规则和明确的权限/确认边界放在首位；LLM 可协助理解请求、整理材料或生成原创练习，但不决定来源身份、真伪、答案核验、截止日期或发布状态。本插件不提供法律意见，学习内容不能替代律师、官方公告或现行法律文本。

| 项目 | 当前值 |
| --- | --- |
| 插件版本 | 0.6.0（正式发布） |
| 数据库 schema | 13 |
| 仓库 | [Lumielle-BlueOMOcean/astrbot_plugin_law_assistant](https://github.com/Lumielle-BlueOMOcean/astrbot_plugin_law_assistant) |
| AstrBot | >=4.22.0,<5；嵌入式 Plugin Page 需 >=4.25.0 |
| Python | >=3.12（以目标 AstrBot 实际环境为准） |

## 当前实现与验收边界

当前代码包括活动雷达、官方案例/法规来源适配、DDL、SQLite 学习库、核验真题 JSON 导入、原创模拟题、受控文档导入、结构化资料候选导入、持久化分阶段 Question Session、多群目标与确认式发布、独立每日计划以及 AstrBot 4.25+ Plugin Page。v0.6.0 增加到点唤醒的每日任务调度、答案/解析延迟揭晓、候选真题人工核验晋级、资料/案例/雷达分页管理、批量事务操作、软删除与恢复及固定范围数据清理。

0.5.2 对应的一次隔离式真实客观题资料验收，使用了全新输出目录与空数据库，重新执行文件哈希校验、validator、prepare/confirm 以及导入后检查；报告统计为 445 道题、11 份共享材料、2,781 个结构块，覆盖 198 道单选、203 道多选和 44 道不定项；Q231、Q309 仍保留为未提供/未核验答案，不由模型补写。该验收只说明当次输入与隔离数据库的结果，不代表本仓库内置题库，也不构成真实 QQ、Windows 主机、真实 LLM provider 或官网持续可用性的证明。

插件支持不等于已提供真实题库或每个方向都有案例。真题库存由操作者依法取得并导入的数据决定；真实案例库存取决于来源扫描结果和已核验分类。仓库不携带真实题库正文。

## 功能概览

- 法律硕士活动扫描、状态筛选、活动详情与多节点时间线；保存来源文档、URL、内容 hash、日期证据和 source run。单个来源失败会隔离记录。
- 官方案例来源与可选法规更新来源；官方合集会拆分为独立案例条目。已保存官方案例由确定性格式器生成学习卡片；案例缺少匹配方向时严格跳过。
- DDL 提醒按配置时区的本地日历日计算；新活动自动发布默认关闭，只有满足来源策略、当前状态和已确认截止日期且由管理员预先配置时才运行。
- 真题、模拟题、候选题、用户案例和官方案例使用不同身份。real_question_candidate 不会自动成为 verified 真题，模拟题不冒充真题；没有可靠答案时不会由 LLM 伪造官方答案。
- 资料库支持明确请求后的文本归档、来源关联、检索、受限更新；导入支持 TXT、Markdown、DOCX 与可抽取文本的 PDF，不支持旧式 DOC 或扫描件 OCR。
- Structured Material v1 的 PDF+JSON hash-bound prepare/confirm 导入，保留共享材料、题干、答题要求、小问、答案/解析块、定位和 provenance；导入只产生待复核候选身份。
- 持久化 Question Session：先显示无答案题面，再由用户分别请求答案与解析；支持长材料/答案分页、多小问、精确 QQ 会话隔离及重启恢复。
- 活动、题目、案例的人工群发布采用固定正文和目标的 prepare → preview → explicit confirm → send；计划修改也先预览再确认。
- 每日案例和每日一题是两个独立任务，支持全局默认、目标群覆盖、随机/固定/日期轮换方向；每日题目还单独支持真题/模拟题/随机来源及题型选择/轮换。
- AstrBot 4.25+ 内嵌 Plugin Page 提供 Overview、Library、Radar、Plans、Targets、History；通过 AstrBot Page Bridge 调用插件 API，不启动独立 Web 服务。
- v0.6.0 的雷达日期采用保守证据规则；人工状态覆盖只影响展示/工作流，不会把未核验日期升级为可提醒截止日期。
- 每日案例、每日题目及定时答案/解析各有持久幂等记录；计划修改后唤醒独立 due-aware scheduler，不依赖扫描间隔估算发送时间。
- Plugin Page 管理数据支持服务端分页、行内详情、授权字段编辑、批量预览确认、可恢复软删除、显式候选真题晋级与范围化清理。

### 尚未实现或不应据此假设已具备

- QQ 文件消息直接接收、扫描 PDF/OCR、旧版 Word .doc。
- 自动替操作者判断真实题目版权、官方身份或答案正确性；候选晋级仍需管理员依据真实证据审核并显式确认。
- RAG、向量数据库、通用 agent 系统、独立后台服务、Nexus runtime 集成。
- 覆盖全国的赛事/案例/法规来源、自动化法律研究或完整商业真题库。
- 特殊 OneBot/QQ 管理能力、独立 dashboard 服务。

## 架构与可信边界

    Private command ─┐
    LLM Tools ────────┼──> LawAssistantService ──> Sources / Extraction / Learning ──> SQLite
    Scheduler ────────┤                         └──> Publisher
    Plugin Page ──────┘
    Future Nexus integration (optional) ───────────> exposed service contract

命令、Tools、Scheduler 和 Plugin Page 共用 LawAssistantService；数据访问由 Storage/Repository 边界负责。插件独立运行，不读取其他插件的 SQLite。Nexus 集成是可选的，目前没有 runtime 依赖。

LLM interprets evidence; deterministic code owns truth。URL、原文 hash、题目来源、答案来源、权限、幂等键、确认 token 和发布状态由代码及持久化记录决定。LLM 可以帮助理解请求、生成明确标注的模拟题或整理有证据的案例学习卡片，但不能补造官方身份、案情、考试年份、参考答案或未证实日期。

## 兼容性、依赖与 LLM

- 支持范围：AstrBot >=4.22.0,<5，Python >=3.12。
- CI 使用 AstrBot 官方精确源码 commit：v4.22.0 81c7b0f7150485beb6124a7ec524a8d8534e7f6e，v4.25.0 02291a3217c92faa0c577bf9d89076949c40954c。后者另验证 Page discovery 和 API route contract。
- AstrBot 4.22–4.24 可使用基础命令、LLM Tools、服务与 Scheduler；内嵌 Plugin Page 要求 AstrBot 4.25+。
- Python 标准库 SQLite 用于运行时存储；requirements.txt 列出实际运行所需的异步 HTTP、HTML/PDF 解析依赖，并在 Windows 条件安装 tzdata 以提供 zoneinfo IANA 时区数据。安装时以当前文件为准。
- 资料检索、已存真题/模拟题选择、答案安全 Session 和已存官方案例卡片不需要插件内部 LLM。自然语言控制需要 AstrBot 对话模型能进行 Tool Calling。生成新模拟题及需要模型辅助的内容整理需要可用 provider；llm_provider_id 留空时插件尝试当前会话或宿主默认 provider。provider 不可用时返回不可用，不伪装生成成功。

## 安装、升级与卸载

### AstrBot 插件管理器 / 仓库 URL

在目标 AstrBot 的插件管理界面使用其“从 URL 安装/添加仓库”一类功能，输入：

    https://github.com/Lumielle-BlueOMOcean/astrbot_plugin_law_assistant

不同 AstrBot 版本的菜单名称可能不同；若当前版本没有 URL 安装入口，使用下方 Git 安装方式。不要把 Plugin Page 与插件安装器混为一谈。

### Git clone

在 AstrBot 安装根目录执行（如已有目录，先按部署流程备份/更新，不要覆盖插件运行数据）：

    git clone https://github.com/Lumielle-BlueOMOcean/astrbot_plugin_law_assistant.git data/plugins/astrbot_plugin_law_assistant

### CI ZIP

GitHub Actions 的成功 release-package job 会产生 astrbot_plugin_law_assistant-0.6.0 artifact，其中 ZIP 文件名为 astrbot_plugin_law_assistant-0.6.0.zip。在支持 ZIP 插件安装的管理器中使用该包；若目标版不提供此能力，解压到：

    {AstrBot 数据目录}/plugins/astrbot_plugin_law_assistant/

确保 metadata.yaml 直接位于插件目录根部，而不是重复嵌套一层目录。ZIP 只含插件代码/静态资源，不含现有配置或运行数据库。

### 升级

1. 停止或按 AstrBot 正常插件更新流程卸载当前代码前，备份运行时 SQLite、AstrBot 配置和 imports/、assets/ 用户文件。
2. 备份位置应在插件数据目录之外，并确认备份可读取。
3. 更新插件文件，不要用安装包覆盖或删除 data/plugin_data/astrbot_plugin_law_assistant/。
4. 启动/重新加载插件，检查“法务 状态”；SQLite 会按顺序执行兼容迁移，当前 schema 为 v13（包含 v12 → v13）。
5. 若数据库迁移或启动失败，停止反复重载，保留日志和原备份以便恢复。

卸载插件代码不会自动意味着可以安全删除 data/plugin_data/astrbot_plugin_law_assistant/。该目录包含数据库、用户导入原件、资料及会话，只有确认不再需要并单独备份后才由管理员手动清理。

## 快速开始

1. 在 AstrBot 安装插件，设置 operator_ids（QQ 用户 ID 列表）；AstrBot Admin 也具管理权限。
2. 重启或重新加载插件，私聊机器人发送“法务 状态”。
3. 管理员在目标群发送“法务 绑定 法硕一群”，为多个群分别绑定。不要把群名当作 UMO；私聊绑定只能使用已知的真实 unified_msg_origin，不能猜测或拼接。
4. 私聊试用“法务 活动”“法务 截止”“法务 题目”“法务 真题 刑法 多选”“法务 模拟题 知识产权 案例分析”。
5. 人工发布时先准备预览，再检查群名、目标和正文，最后确认 token；不要在生产群做未审查的发送测试。
6. 默认自动扫描、活动发布、每日案例和每日一题均关闭。先准备可信来源/内容、确认群绑定及授权策略，再按需启用。

## 权限与会话范围

- 管理查询、扫描、题目/案例选择、资料库访问/修改、计划管理和群发布 Tool 要求私聊，并校验 AstrBot Admin 或配置的 operator_ids。
- 群聊中的 /law bind、bind-umo、unbind、rename 仅供 Admin/operator 管理当前群/绑定目标。
- Question Session 的 current、next、next-question、answer、next-answer、explanation、next-explanation 可由该精确 QQ 会话中的参与者使用；它们只能操作此会话的快照。close 仍要求管理员/operator。
- 多个群目标时，“发到群里”不会默认扩展到所有群；须明确选目标。未绑定或有歧义的名字不会猜测成 UMO。
- 日常使用可直接发送“法务 …”，不需要输入斜杠；传统 `/law …` 命令仍保留兼容。LLM Tool 调用不绕过原有私聊与 Admin/operator 授权。
- 其他管理 Tool 不因由 LLM 发起而绕过权限检查。

## 资料、案例与雷达管理

Plugin Page 的 Library、Review、Radar、Targets、History 使用服务端分页；每页选择只作用于当前已展示行。批量修改、软删除/恢复、复核状态或雷达状态均先预览准确 ID、变更和影响数量，再由操作者确认。单条资料详情在列表内展开；管理编辑入口受 Admin/operator 限制，不会把答案/解析加回普通查询 DTO。

结构化 `real_question_candidate / pending_review` 只有通过授权管理详情、确定性 `RealQuestion` 校验、候选晋级预览与确认后才会建立/关联已核验真题。未提供答案仍为空，不由模型补造；真题身份升级需操作者核对来源、考试定位、使用权限及答案身份。用户上传案例保留 `user_case` 身份，不能通过编辑、批量操作或状态覆盖伪装成官方案例。

雷达时间候选按来源发布时间或明确日期证据谨慎处理；正文中偶然出现的年份不作为默认锚点。日期人工复核记录会保留 evidence/hash、旧值、新提议、操作者及理由；展示状态覆盖不会确认日期，不会单独驱动正式 DDL 或自动公告。

## 每日计划与延迟揭晓

Plans 的方向/题型模式采用依赖式控件：random 不展示固定项，fixed 只展示固定方向/题型，rotation 展示有序列表、起始日和“首日方向/首日题型”。用户不需要编辑内部 rotation index。案例方向、题目方向和题型轮换相互独立；群级明确值只覆盖对应群和对应字段，其余继续沿用全局计划。

每日任务由独立 scheduler 按目标配置时区和最近到期时间唤醒；启动后会立即检查当日任务，允许当日未发送时进行一次 catch-up，不补发前一日任务。重复启用不会产生第二组循环，计划确认后会即时唤醒重新计算。每个目标、日期、任务种类分别幂等，案例失败不吞掉题目执行，反之亦然。

每日一题可以设置“手动揭晓”或“定时揭晓”。定时答案与解析延迟分别从题目在群内成功发送的实际时间开始计算；定时模式要求答案延迟大于 0，解析延迟不得早于答案延迟，手动模式会忽略并归零这两个延迟。揭晓任务只消费同一持久化 Session 快照，不重新抽题/生成；已手动揭晓、已关闭或被替代的会话不会重复投递。多页揭晓逐页持久记录；失败或发送结果不确定时留在历史供管理员检查，不盲目自动重放。

## 批量操作与数据清理

批量内容修改与软删除/恢复在一个事务中复核预览时的 ID 和行状态，过期、越权或数据变化会拒绝整批操作；候选晋级则逐条返回明确结果，不能把失败项静默当成功。普通“删除”是保留证据与历史的可恢复停用；永久清理由单独的“数据管理”固定范围入口处理。

数据管理预览提供范围名称、固定删除描述、逐表数量、不会删除的内容和短期确认 token。范围包括活动雷达、资料库、核验真题、案例、活动/提醒/每日执行历史、答题会话、每日计划/待发送揭晓、群目标和全部插件运行数据。确认在 SQLite 事务内复核快照并按外键依赖清理；全部运行数据重置必须输入准确短语“清空全部数据”。完整重置保留 SQLite schema/schema_meta 和 AstrBot 插件配置，不会删除插件设置；执行前应在插件数据目录之外备份数据库及 WAL/SHM。

范围边界：活动雷达清理只移除活动及 event 来源抓取基线，不影响官方案例和法规来源文档/运行记录或法规更新；清理案例会使关联官方案例来源可重新处理，并允许相同 Structured Material 导入重建缺失案例，混合导入中保留的题目不会重复。清理发布/提醒/每日执行历史不会清理任何来源抓取运行记录。清理核验真题库存会将通过结构化晋级关联的候选题解除关联并恢复为 `real_question_candidate / unverified`，保留原始结构证据以供再次核验晋级。

真题管理中的 linked verified question 编辑会在同一事务中校验并同步核验库存及其全部规范化资料投影；停用会使题目不再参与 `origin=real` 选择，恢复后仍按其当前核验状态处理。linked truth 可通过资料条目停用/恢复管理；候选身份的显式降级仅在执行“核验真题库存”清空范围时发生。既有 Question Session 使用不可变快照，不会被后续管理编辑改写。用户提交案例可由授权操作者通过批量核验操作在 `unverified` 与 `user_verified` 间切换，但身份始终是 `user_case`；不能通过页面或批量操作授予 `official_case`。

雷达“当前/历史/忽略”等人工状态只影响展示分类，不会确认日期证据。日期复核可单独提交“日期已由人工确认”；只有接受且明确确认的日期才可进入相应的截止提醒逻辑，状态覆盖不会代替日期确认，复核操作者、原因和证据摘要会保留在审计记录中。

## 配置参考

配置由 config.py 集中解析，AstrBot UI schema 为 _conf_schema.json。以下为完整 41 个 schema 字段及默认值；群级每日任务计划保存在插件 SQLite，通过计划管理工具/Plugin Page 修改，不是第二份配置 schema。

| 字段 | 类型 / 默认值 | 作用 |
| --- | --- | --- |
| operator_ids | list[string] / [] | 可私聊操作的 QQ 用户 ID；AstrBot Admin 仍有效。 |
| timezone | string / Asia/Shanghai | 活动日期和 Scheduler 使用的 IANA 时区；无效值回退至默认时区。 |
| auto_scan_enabled | bool / false | 自动扫描及相关 DDL 检查总开关。 |
| scan_interval_minutes | int / 60，5–1440 | 自动扫描间隔分钟数。 |
| llm_provider_id | string / "" | 可选 provider ID；空值时按当前会话/宿主默认 provider 解析。 |
| extra_event_source_urls | list[string] / [] | 额外通知列表页 URL；按来源适配器允许的主机约束校验。 |
| radar_keywords | list[string] / [] | 活动识别的附加关键词。 |
| radar_action_keywords | list[string] / [] | 报名/投稿/参与动作的附加关键词。 |
| radar_historical_keywords | list[string] / [] | 历史、结果类信号附加关键词。 |
| radar_auto_publish_sources | list[string] / [] | 允许自动发布的额外来源 key；仍受发布规则约束。 |
| radar_source_policies | list[object] / [] | 每来源设置 key、display_name、discover_enabled、auto_publish_enabled。 |
| auto_publish_events | bool / false | 活动自动发布总开关；需由操作者预授权。 |
| deadline_reminder_days | list[int] / [7,3,1] | 截止日前提醒天数。 |
| deadline_same_day_enabled | bool / true | 是否发送 DDL 当日提醒。 |
| daily_case_enabled | bool / false | 全局每日案例默认启用状态。 |
| daily_case_time | string / 08:00 | 每日案例本地发送时间 HH:MM。 |
| daily_case_selection_mode | string / random | 案例方向模式：random、fixed、rotation。 |
| daily_case_subject | string / "" | 案例固定方向；空值由随机/轮换模式决定。 |
| daily_case_rotation_subjects | list[string] / [] | 案例方向轮换的有序规范方向 ID。 |
| daily_case_rotation_start_date | string / "" | 案例轮换锚点 YYYY-MM-DD；首次启用会按配置时区持久化。 |
| daily_case_rotation_start_index | int / 0，最小 0 | 案例轮换起始列表下标。 |
| daily_case_card_max_chars | int / 1800，600–5000 | 每日案例卡片上限；超限跳过而非截断证据内容。 |
| daily_question_enabled | bool / false | 全局每日一题默认启用状态。 |
| question_message_max_chars | int / 1600，300–4000 | 题面、选项、答题要求、答案/解析消息的字符预算。 |
| daily_question_time | string / 08:00 | 每日一题本地发送时间 HH:MM。 |
| daily_question_selection_mode | string / random | 题目方向模式：random、fixed、rotation。 |
| daily_question_origin | string / random | 每日题来源：real、mock、random。 |
| daily_question_subject | string / "" | 题目固定方向；空值由方向模式决定。 |
| daily_question_type | string / "" | 旧兼容字段；未显式指定新题型模式时，非空值表示固定题型。 |
| daily_question_type_selection_mode | string / random | 独立题型模式：random、fixed、rotation。 |
| daily_question_fixed_type | string / "" | 新模式下的固定题型。 |
| daily_question_rotation_types | list[string] / [] | 题型轮换有序列表。 |
| daily_question_type_rotation_start_date | string / "" | 题型轮换锚点；留空首次启用按本地日期持久化。 |
| daily_question_type_rotation_start_index | int / 0，最小 0 | 题型轮换起始下标。 |
| daily_question_rotation_subjects | list[string] / [] | 题目方向轮换有序列表。 |
| daily_question_rotation_start_date | string / "" | 题目方向轮换锚点；留空首次启用按本地日期持久化。 |
| daily_question_rotation_start_index | int / 0，最小 0 | 题目方向轮换起始下标。 |
| case_source_court_enabled | bool / true | 最高人民法院案例来源开关。 |
| case_source_spp_enabled | bool / true | 最高人民检察院案例来源开关。 |
| law_update_enabled | bool / false | 可选法律更新来源开关。 |
| http_timeout_seconds | int / 20，5–120 | 单次异步 HTTP 请求超时秒数。 |

标准方向 ID：criminal_law、criminal_procedure、civil_law、commercial_law、civil_procedure、intellectual_property、constitutional_administrative、civil_commercial、judicial_practice、economic_law、other；支持代码定义的中文别名（如刑法、刑诉、民法、商法、民诉、知产、宪法与行政法、民商法、司法实务、经济法、其他）。其中 civil_commercial 的检索可匹配 civil_law 与 commercial_law。题型 ID：single_choice、multiple_choice、indefinite_choice、true_false、short_answer、case_analysis。不要填写未支持的枚举。

轮换使用时区本地日期、独立起始日期和有序列表确定方向/题型；不是“每成功发送一次递增”的内存计数。每日案例、每日题方向、每日题型的锚点互相独立。空的起始日期在第一次启用时建立持久锚点；如果没有符合明确方向、来源或题型的库存，严格跳过并记录原因。

学校四方向预设顺序为 intellectual_property → civil_commercial → judicial_practice → economic_law，对应知识产权、民商法、司法实务、经济法。它不自动启用，也不是所有群的默认配置。

群级配置若有明确值，仅覆盖该群、该内容类型的相应设置；未覆盖的值继承全局计划。计划修改通过 prepare → preview → confirm 保存。暂停案例不改变每日一题，反之亦然。

## 用户命令与维护命令

普通使用无需斜杠，优先发送“法务 …”；`/law …` 保留为兼容入口，英文维护命令仍可使用。大多数管理命令必须私聊并由 Admin/operator 使用。{...} 表示参数，[...] 表示可选参数。

| 子命令 | 示例/用途 | 访问与副作用 |
| --- | --- | --- |
| status | /law status | 私聊 Admin/operator；只读运行状态。 |
| scan | /law scan | 私聊 Admin/operator；立即触发已配置来源扫描。 |
| events | /law events current competition、/law events historical | 私聊；按状态/类型/关键词查询，默认 current。状态：current、needs_review、historical、all；review 可指 needs_review。 |
| event | /law event 12 | 私聊；查询活动详情。 |
| deadlines | /law deadlines | 私聊；按配置时区查询提醒相关截止节点。 |
| sources | /law sources | 私聊；来源状态/抓取运行信息。 |
| targets | /law targets | 私聊；查看绑定发布目标。 |
| bind | 群内 /law bind 法硕一群 | Admin/operator；把当前群真实 UMO 与别名绑定。私聊不接受猜测群名。 |
| bind-umo | /law bind-umo {真实UMO} [别名] | 私聊 Admin/operator；明确绑定给定 UMO。 |
| unbind | 群内 /law unbind；私聊 /law unbind {真实UMO} | Admin/operator；解除指定/当前绑定目标。 |
| rename | 群内 /law rename 法硕学习群；私聊 /law rename 一群 法硕学习群 | Admin/operator；只改目标别名，不更改 UMO。 |
| publish | /law publish {event_id} [目标别名/ID...] | 私聊；准备活动发布，返回固定正文和目标预览，不立即发送。多群时必须明确目标。 |
| confirm | /law confirm {token} | 私聊；确认此前的短期发布预览。token 锁定操作者、正文和目标，不能重复使用。 |
| case | /law case [方向] | 私聊；获取当日/匹配案例学习内容，不在群内发送。 |
| question | /law question [real / mock / random] [方向] [题型] | 私聊；按条件选择/生成题目并开启当前私聊 Session；省略项按产品规则随机。非法显式筛选报错。 |
| study | /law study {资料库题目ID} | 私聊 Admin/operator；打开已存模拟题或候选题 Session；身份标签保持原状。 |
| current | /law current | Session 当前精确会话；查看当前题面及当前小问揭晓状态。 |
| next | /law next | Session 当前精确会话；继续阅读当前题面/材料的下一页。 |
| next-question | /law next-question | Session 当前精确会话；进入下一独立小问。 |
| answer / next-answer | /law answer、/law next-answer | Session 当前精确会话；显式揭晓当前小问答案/答案续页。 |
| explanation / next-explanation | /law explanation、/law next-explanation | Session 当前精确会话；单独揭晓解析/解析续页。 |
| close | /law close | Admin/operator；关闭当前精确会话。 |
| import | /law import {imports内相对路径} [auto / case / mock_question / real_question_candidate] | 私聊 Admin/operator；从插件 imports 受控目录立即归档；不能给任意绝对路径。 |
| plans / plan | /law plans [目标别名/ID] | 私聊；只读查看全局/群级每日计划与安排。 |
| question-import / import-questions | /law question-import "{JSON路径}" | 私聊 Admin/operator；导入用户依法取得且经核验的真题 JSON，返回新增数与库存。带空格路径应加引号。 |
| case-tag | /law case-tag {案例ID} {方向...} | 私聊 Admin/operator；设置案例人工方向标签。 |
| review | /law review [来源ID] | 私聊 Admin/operator；列待复核项目，安全题目视图不含答案/解析/原始完整片段。 |
| review-get | /law review-get {复核ID} | 私聊 Admin/operator；读取安全复核详情。 |
| review-status | /law review-status {ID} {pending / resolved / superseded} | 私聊 Admin/operator；更新复核队列状态；resolved 不等于核验真题身份升级。 |
| laws | /law laws | 私聊；只读查看已保存的法规更新。 |
| help | /law help | 显示简要命令帮助。 |

常用中文入口示例：`法务 状态`、`法务 活动`、`法务 截止`、`法务 题目 刑法 多选`、`法务 真题 知识产权 多选`、`法务 模拟题 民法 案例分析`、`法务 案例 司法实务`、`法务 当前`、`法务 下一页`、`法务 下一题`、`法务 答案`、`法务 答案续页`、`法务 解析`、`法务 解析续页`、`法务 结束`、`法务 计划`、`法务 群计划`、`法务 绑定 法硕一群`、`法务 帮助`。管理操作仍受私聊与 Admin/operator 权限约束；群内绑定是明确例外。

参数形式以当前仓库 CLI parser 为准；常用中文题型别名包括单选、多选、不定项、判断、简答、案例分析。发布目标名若重名，应使用唯一别名或 ID 消除歧义。

活动类型筛选可使用 competition、call_for_submissions、forum、training、internship、activity、moot_court、conference、recruitment；未写类型时可将剩余词作为关键词。题目来源 random 的候选为真实已核验题与模拟题；real_question_candidate 不属于其中的真题库存。

## 自然语言与 LLM Tools

可以在私聊中说“看看最近有哪些法硕比赛”“给我一道知识产权真题多选题”“给我一个司法实务案例”“把刚才这道题发到法硕一群”“显示一群未来七天案例和题目计划”。LLM 负责把意图映射为结构化参数；插件继续做权限、方向/类型校验、证据检查和目标锁定。

题目/案例读取 Tool 可返回短期 content_ref；“把刚才那道题发到群里”通过引用锁定相同内容，“重新出一道题再发”则需明确选择新内容。发布流程先预览，确认时不会重新生成或随机选择。长期计划变更必须预览并确认。

当前注册的 25 个 Tools：

| Tool | 用途 |
| --- | --- |
| law_status | 助手状态。 |
| law_scan_events | 触发来源扫描。 |
| law_list_events | 按状态、类型和关键词查询活动。 |
| law_get_event | 活动详情。 |
| law_list_deadlines | 截止日期列表。 |
| law_get_daily_case | 按方向获取案例学习内容并返回可复用引用。 |
| law_generate_question | 按来源/方向/题型获取或生成题目。 |
| law_study_question | 按资料库 ID 开启候选/模拟题学习。 |
| law_question_session | 当前 Session 的题面续读/答案/解析/切换/关闭动作。 |
| law_question_inventory | 查看真题库存与覆盖情况。 |
| law_archive_learning_material | 用户明确要求后收藏学习资料。 |
| law_import_learning_document | 从受控资料目录导入文档。 |
| law_search_learning_library | 搜索资料库。 |
| law_get_learning_item | 读取资料；题目使用答案安全边界。 |
| law_update_learning_item | 修改允许的标题/方向/备注/学习字段。 |
| law_list_targets | 查看绑定群及任务配置。 |
| law_rename_target | 受控修改目标别名。 |
| law_get_daily_plans | 查看当前/未来计划。 |
| law_prepare_publish_question | 准备指定题目的发布预览。 |
| law_prepare_publish_case | 准备案例发布预览。 |
| law_prepare_daily_plan_update | 准备长期每日任务计划变更。 |
| law_confirm_daily_plan_update | 确认已预览的计划变更。 |
| law_list_law_updates | 查询法规更新。 |
| law_prepare_publish_event | 准备活动发布预览。 |
| law_confirm_publish | 确认一次性发布。 |

Tool 注册不等于已验证任意表达下的真实模型理解效果。真实自然语言 Tool Calling 还依赖 AstrBot 会话模型和 provider 配置。

## 活动雷达与 DDL

活动通过来源适配器抓取原始文档，再做规则优先的提取、验证、确定性 upsert 和 SQLite 持久化。内置活动来源聚焦 China-JM 通知列表；extra_event_source_urls 仅提供额外同主机通知列表入口。实际可用范围取决于官网结构和网络/TLS。

雷达状态根据证据和配置本地时钟呈现 current、needs_review、historical；普通“当前活动”查询不会把历史通知伪装成有效报名。时间线区分报名截止、投稿截止、初赛/复赛/决赛、结果等节点。未确认的 LLM 日期不会驱动正式提醒或自动发布。

启用 auto_scan_enabled 才会运行周期扫描和提醒检查。自动发布另受 auto_publish_events、来源发现/发布策略、当前状态、确认截止日期与幂等规则共同限制；默认不自动发群。可通过 /law scan 手动触发一次扫描，通过 /law events current、/law deadlines 和 /law sources 查询。

## 群目标、预览与发布

群内 /law bind {别名} 从当前真实消息会话读取 UMO。私聊新增目标需已知、明确的 UMO；系统不根据别名构造群地址。别名用于用户选择，UMO 是实际路由标识。多个目标时必须明确指定具体目标；“所有群”也必须由操作者明确表达。

人工发布流程：

1. 查询或选择一条活动/题目/案例。
2. 指定已绑定目标；有歧义时先消歧。
3. 插件固定内容正文和目标 ID，生成预览及短期确认 token。
4. 操作者审阅预览后显式确认。
5. 仅向预览时锁定的目标发送；确认不重新调用 LLM、不重新抽取内容、不扩大目标。

token 绑定操作者并限时、一次性。目标失效、token 过期、重复确认或发送部分失败会作为失败/部分结果返回，不把部分成功伪装成全成功。自动 Scheduler 发送仅能依预先配置授权策略执行。

## 每日案例、每日一题与计划

两类计划单独启停、定时、轮换和记录；一个任务失败、暂停或缺货不会推动或阻止另一个任务。同一群、同一天、同一内容类型幂等。scheduler 默认关闭；单独启用每日任务即可按计划唤醒，不要求自动扫描也开启。

- 案例方向：random / fixed / rotation；只从真实官方案例库选择。缺匹配案例则跳过，不用模型编造真实案例。
- 每日题方向：random / fixed / rotation；来源 real / mock / random；题型模式也独立为 random / fixed / rotation。显式约束组合必须全部满足；缺少符合条件库存则跳过，不能静默切换来源、方向或题型。
- 轮换采用配置时区的本地日期、列表、起始日期和起始位置计算。每日案例、每日题方向、每日题型的锚点互相独立。起始日期空值在首次启用时建立持久化锚点；插件重启、漏发某日或重试都不会造成内存索引漂移。
- 全局默认适用于无群级覆盖的绑定目标；目标的群级明确值只覆盖对应任务字段。学校四方向预设可用于案例、题目分别配置，也可自定义顺序/列表。预览可查看未来日期安排；长期变更确认前不持久化。
- 每日题/案例的自动正文分别走安全题目 renderer/案例卡片。题目发布先不含答案；用户在目标群会话中明确请求后再揭晓。不同目标拥有独立的已发送身份历史。

典型计划示例：每日案例轮换 知识产权 → 民商法 → 司法实务 → 经济法；每日题目方向采用另一列表，题目来源仍可独立设为 random，题型采用单独轮换。没有真实内容的轮换日会被跳过并记录，而非替换方向。

## 学习资料库、来源身份与检索

主要身份包括：

| 身份 | 说明 |
| --- | --- |
| verified_real_question / 已核验真题 | 只由经过核验且有来源记录的专用真题导入进入真题库存。 |
| mock_question | AI 原创练习，明确标记模拟题；持久库存可复用。 |
| real_question_candidate | 结构化导入或用户提供的待核验题目；只能被明确学习，不等于真题库存。 |
| official_case | 经受信最高法/最高检来源路径采集的真实案例。 |
| user_case / note | 用户归档内容或普通笔记，不自动变成官方案例。 |

资料记录保留 created_by、原始来源、URL/hash、分类与整理结果。相同原文可复用 Source 记录，但不同条目的方向、备注、摘要等不因去重而覆盖。读、搜、改均经过现有 operator/Admin 权限校验。

题目 safe detail/search 不暴露隐藏答案、解析、原始完整 JSON 或源文；结构化题目搜索面向标题、题干、选项、答题要求、小问和关联材料，不以隐藏答案建立搜索旁路。题目通过 Question Session 显式揭晓。非题目资料可返回受限详情。资料更新字段由 LibraryService 白名单控制。

## 普通文档导入

支持 TXT、Markdown、DOCX 和文本可抽取的 PDF。扫描 PDF 可能无法提取文字，本插件没有 OCR；不支持 .doc。解析保留可得的页码/段落/行号 locator，模糊内容需要复核，不自动生成官方身份或答案。

传统导入命令只接受插件数据目录下 imports/ 的相对文件路径：

    /law import {相对路径} [auto|case|mock_question|real_question_candidate]

示例：/law import reading/contract.md。不会执行宏或将绝对路径开放给 Tool。Plugin Page 上传先进入插件 staging 目录，显示提取和分类预览，确认后才归档；取消/未确认不会写入资料条目。普通文件当前上限为 20 MiB、PDF 200 页、提取文本 600,000 字符、最多拆分 100 个候选条目；超限时拒绝/报告，不应通过绕过限制导入。

## Structured Material v1：PDF 与 JSON 成对导入

该格式是候选资料交换契约，不是官方身份声明。规范详见 docs/structured-material-v1.md。Schema 固定为字符串 "1.0"；prepare 同时校验原 PDF 字节 SHA-256、JSON 原文 hash 与规范化 payload hash，confirm 使用同一快照。请先阅读 validator 预览中的 fatal/error/review、材料、小问、答题要求、locator 与答案来源，再确认。

下面是虚构结构骨架，不是题目示例，也不能原样作为真实导入；64 个零仅是 hash 占位符，必须替换为原 PDF 的真实 SHA-256：

    {
      "schema_version": "1.0",
      "document": {
        "title": "synthetic fixture",
        "source_type": "synthetic_fixture",
        "original_filename": "fixture.pdf",
        "original_file_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "preparation_method": "mixed",
        "verification_status": "pending_review"
      },
      "materials": [
        {"id": "M1", "title": "fixture material", "blocks": [
          {"id": "M1-B1", "order": 1, "kind": "paragraph", "text": "synthetic material only", "locator": "PDF第1页", "provenance": "source_text"}
        ]}
      ],
      "questions": [
        {
          "id": "Q1", "source_number": "Q1", "question_type": "single_choice",
          "material_refs": ["M1"], "stem_blocks": [],
          "options": [{"key": "A", "text": "synthetic option"}],
          "subquestions": [], "answer_requirements": [],
          "answer": {"keys": ["A"], "status": "provided", "provenance": "source_text"},
          "answer_status": "provided", "explanation_blocks": [],
          "locators": ["PDF第1页"], "verification_status": "pending_review"
        }
      ],
      "cases": []
    }

可用题型：single_choice、multiple_choice、indefinite_choice、true_false、short_answer、case_analysis。materials 保存共享事实/材料；Question 的 stem_blocks 保存顶层问题；真正独立的小问才放 subquestions；评分和作答规范放 answer_requirements。答案和解释与题面分开，保留 locator、provenance。JSON 中声称 official/verified 不授予该身份。

确认导入后，题目身份是 real_question_candidate，验证状态 pending_review；它不会进入 /law question real 的核验真题库存。候选题可由有权限的操作者显式学习，屏幕上仍会标明候选身份。官方案例身份只由受信 source adapter 路径确定。

## 已核验真题 JSON 导入

专用真题导入与 Structured Material 候选导入是不同路径。操作者必须拥有使用权并逐项核验来源；本仓库不提供任何真实真题正文。私聊执行：

    /law question-import "/path/to/verified questions.json"

也接受 /law import-questions 别名。文件可以是 JSON 数组或 { "questions": [...] }。每项至少提供 source_name、exam_name、方向 subject、question_type、原题 stem，并提供可定位来源（source_url、source_locator 或 question_number）；verification_status 必须明确为实现接受的 verified 类状态（verified、official 或 user_verified）。答案来源通过 answer_source 标出，例如 official、third_party、user_verified、unverified 或 not_provided；必须依据数据实际证据填写，不要把 AI 生成解析填成官方答案。没有可靠答案时留空/标明 not provided，不猜测。

结构示意（全部字段和值均为占位，不是真实题目）：

    {
      "questions": [
        {
          "source_name": "licensed source label",
          "exam_name": "verified exam title",
          "exam_year": "YYYY",
          "paper": "paper locator",
          "question_number": "question locator",
          "source_url": "https://source.example/item",
          "source_locator": "page/section locator",
          "subject": "criminal_law",
          "question_type": "single_choice",
          "stem": "〔占位：由用户依法提供的原题文本〕",
          "options": ["A. 〔选项占位〕", "B. 〔选项占位〕"],
          "answer": "A",
          "answer_source": "official",
          "explanation": "",
          "verification_status": "verified"
        }
      ]
    }

命令会校验并返回导入计数及库存覆盖信息；身份键确定性去重并可更新同一题定位的记录。重复导入不是版权许可，也不代表插件核实了用户的判断。请保留原许可和来源记录。真题全文存储/群发前须自行确认授权条件。

## 待复核队列

结构化题目复核详情含 answer_safe: true、题干/选项等白名单 safe_preview 与结构化 safe_structure，但不包含答案、解析、原始 fragment 或完整 payload。复核状态为 pending、resolved、superseded；更改队列状态只影响复核记录，不会将候选题自动升级为已核验真题，也不会创建官方案例身份。对身份和答案来源的确认责任仍属于授权操作者的真实证据核验，代码保持来源类型边界。

## Question Session：题面、答案与解析分阶段

/law question ... 选择题目，/law study {ID} 学习库候选/模拟题。群发布或每日一题则在目标发送成功后，才建立该目标精确 UMO 下的 Session。

1. 初次 prompt 先呈现题目身份、方向/题型、公共材料、顶层题干、选项/小问和答题要求；答案与解析不在题面中。
2. 回复“法务 下一页”续读当前题面/材料；回复“法务 下一题”进入下一独立小问；多小问题目仍先呈现共享材料和顶层公共题干。
3. 回复“法务 答案”显式揭晓当前小问可核验答案；答案多页回复“法务 答案续页”继续。
4. 回复“法务 解析”单独揭晓来源解析；多页回复“法务 解析续页”继续。
5. 回复“法务 当前”查看当前小问的题面位置与该小问独立揭晓状态；“法务 结束”关闭（Admin/operator）。

最终每条消息受 question_message_max_chars 约束，标题、进度、来源和分页尾缀也计入预算；长语义块必要时安全拆分，不会删掉内容或混合答案进题面。每个 QQ unified_msg_origin 只有自己的持久 Session；新题仅替代同范围旧 Session。题目快照 hash、进度和按小问揭晓事件持久化，重启后恢复；缺答案/解析时显示 unavailable，不调用 LLM 补造。real_question_candidate / pending_review 可由授权者显式学习，但整个 Session 保留候选标识。

## Plugin Page（AstrBot 4.25+）

插件页面由 AstrBot Plugin Page Bridge 承载，不提供独立服务：

| 页面区域 | 主要用途 |
| --- | --- |
| Overview | 插件/数据库状态、资料与任务概况。 |
| Library | 搜索、安全详情、允许的更新、普通文档上传、结构化 PDF+JSON prepare/confirm、待复核队列。 |
| Radar | 活动扫描、状态/活动查询、详情。 |
| Plans | 全局与群级每日案例/题目计划预览、准备修改、确认/恢复覆盖。 |
| Targets | 已绑定目标、别名、明确的改名/解绑确认流程。 |
| History | 已保存的每日发送、发布、提醒、来源运行记录。 |

页面 API 经 AstrBot 插件路由/Bridge 认证，插件不要求另设 dashboard 账号。AstrBot 4.22–4.24 不提供此内嵌页面能力，但核心插件兼容目标不变。

## 数据、隐私与数据库迁移

SQLite 和用户数据写入 AstrBot 插件专属路径：

    data/plugin_data/astrbot_plugin_law_assistant/law_assistant.sqlite3

同目录另有用户导入原件/暂存文件目录 imports/、assets/。插件不会把 API Key、provider credential、QQ token 存入此业务数据库。数据库保存来源、活动、目标、发布状态、学习条目、Question Session 快照及计划等业务数据；应限制操作系统目录权限，并把数据库和导入原件视为敏感/有版权的数据。

当前 schema v13；迁移按顺序、事务处理。v12 → v13 保留既有 v12 数据，在单一事务中加入每日揭晓任务、雷达状态/日期复核、软删除/晋级关联与操作审计字段。升级前应停止并备份 SQLite 主库及 WAL/SHM，确认备份可读取；版本路径概览：

| 路径 | 主要增加/调整 |
| --- | --- |
| v0 → v1 | 初始活动、活动日期时间线和 source run 表。 |
| v1 → v2 | 活动元数据/修订、原始来源文档、发布目标/发布记录、提醒、案例、每日内容和法规更新表。 |
| v2 → v3 | 将提醒幂等身份改为活动、日期类型、截止值、目标和提前天数，保留已发送状态。 |
| v3 → v4 | 增加真题库存和全局/目标每日计划；旧案例方向字段迁为人工标签。 |
| v4 → v5 | 分离来源提供的案例方向与管理员人工分类，保护人工标签不被重抓覆盖。 |
| v5 → v6 | 学习资料库来源、条目、题目/案例与结构化学习表。 |
| v6 → v7 | 来源唯一性调整，以保留同一原文对应的不同 URL/来源记录。 |
| v7 → v8 | 待复核记录及官方案例拆分条目的 active 状态。 |
| v8 → v9 | 独立题型计划轴、每日内容来源身份、跨来源活动关联和 canonical 发布幂等状态。 |
| v9 → v10 | Structured Material 导入、共享材料、内容块、小问、来源关系与 review。 |
| v10 → v11 | 按精确 QQ 会话隔离的不可变 Question Session 快照和揭晓事件。 |
| v11 → v12 | 真题身份 v2 与历史 answer_source 规范化。 |
| v12 → v13 | 每日题目答案/解析揭晓模式与持久作业、雷达人工状态和日期复核、案例/资料软删除、候选真题精确晋级关联及管理审计。 |

上表是迁移主题摘要，不替代 SQL migrations 或旧 CaseItem/RealQuestion 等兼容说明。升级前备份数据库和插件用户文件。安装包不会携带或覆盖运行时数据。外部分享日志/数据库前应检查，其中可能包含用户提交的学习材料及来源信息。

## 排障

| 现象 | 检查项 |
| --- | --- |
| 法务命令无回应或未注册 | 检查 AstrBot 日志、插件依赖安装、metadata.yaml 兼容范围、插件是否成功 reload；私聊发送“法务 状态”。 |
| 管理命令提示无权限 | 确认是在私聊使用；检查 AstrBot Admin 或 operator_ids 的 QQ 用户 ID 格式。群 Session 交互例外仅限同一精确会话。 |
| 活动扫描 0 来源/失败 | /law sources 查看 source_runs，检查 auto_scan_enabled、来源策略、DNS/TLS/站点变更及超时；不要关闭 TLS 校验作为修复。 |
| 某活动无 DDL | 检查原文日期 evidence、确认状态、日期 kind、配置时区，以及它是否被标为历史/待复核。未验证日期不会触发正式提醒。 |
| 发布没有发出 | 检查目标是否绑定/启用、唯一别名解析、预览 token 是否仍在有效期、目标权限和 publisher 返回；确认前不会群发。 |
| 每日计划跳过 | 查看 Plans 与 History、该群级是否覆盖、目标时间/本地日期、origin/subject/type 库存、内容是否已成功发送；严格约束不满足时按设计 skip。 |
| 真题无匹配 | 查看 question inventory；候选 structured import 不进入 origin=real。检查验证状态、来源/年份/方向/题型筛选和使用授权。 |
| 真题 JSON 导入路径失败 | 确认文件实际位于 AstrBot 所在主机，使用 JSON 路径并为含空格路径加引号；该维护入口仍可使用 `/law question-import "路径"`；检查 UTF-8 JSON 结构和来源身份字段。 |
| Question Session 不显示答案 | 首屏隐藏是预期行为；发送“法务 答案”。若显示未提供，则来源没有可核验答案，不会由模型补写。解析单独发送“法务 解析”。多页继续使用“法务 答案续页”或“法务 解析续页”。 |
| Plugin Page 不显示 | 需 AstrBot 4.25+，并确认插件加载、Page discovery 与 Dashboard Bridge；4.22–4.24 核心命令仍兼容但无内嵌页。 |
| 每日任务或延迟揭晓未执行 | 检查目标启用状态、群级覆盖、本地时区/发送时间、每日执行 History 的 sent/skipped/failed 原因；确认重载后 Scheduler 已启动。已知发送失败不会盲目重发，结果不确定时须人工检查历史。 |
| 迁移失败或数据库异常 | 停止重载、保留日志，复制并校验备份；不要手动改 schema version 或删除 SQLite/WAL/SHM。 |

## 开发与验证

推荐 Python 3.12（AstrBot 4.22/4.25 环境也要求该主版本）：

    python -m pip install -r requirements.txt
    python -m pip install pytest pytest-asyncio ruff quart
    python -m compileall .
    ruff format --check .
    ruff check .
    pytest
    node --test tests/page_lifecycle.test.mjs
    git diff --check

完整兼容性 smoke 需对应版本官方 AstrBot source checkout：

    python tests/compatibility_smoke.py <AstrBot-4.22.0-source> --commit 81c7b0f7150485beb6124a7ec524a8d8534e7f6e
    python tests/compatibility_smoke.py <AstrBot-4.25.0-source> --commit 02291a3217c92faa0c577bf9d89076949c40954c

AstrBot 4.25 还可执行：

    python tests/page_discovery_smoke.py <AstrBot-4.25.0-source> <plugin-root>
    python tests/web_api_route_smoke.py <AstrBot-4.25.0-source> <plugin-root>

单元测试使用确定性 fake、临时 SQLite，不要求真实 QQ、LLM 或外网。可选官网只读 smoke：

    python scripts/live_smoke.py

该脚本可能受站点可用性影响，不能替代 fixture tests，也不发送 QQ 消息。构建 release ZIP：

    python scripts/build_release.py --output dist

ZIP 从 Git 已跟踪文件建立，排除 .git、CI、tests、scripts、docs、缓存、SQLite、日志、虚拟环境和常见凭据文件。当前 CI workflow 在 main push 与 pull request 运行 5 个 required jobs：Quality / Unit、AstrBot compatibility (4.22.0)、AstrBot compatibility (4.25.0)、Release package smoke (4.22.0)、Release package smoke (4.25.0)。release jobs 从最终提交构建/解压 ZIP 并执行包内编译和 loader smoke；4.25 还执行 Page discovery/API route smoke，生成名为 astrbot_plugin_law_assistant-0.6.0 的 artifact。正式发布以通过 exact-SHA CI 的提交创建 v0.6.0 tag 与 GitHub Release，并使用该提交对应 CI 产物作为发布包；具体运行结果以该 commit 的 Actions 页面为准。

回归 fixture 不得包含真实题库正文、凭据或生产数据库。真实 QQ/NapCat、真实模型自然语言稳定性、Windows 本机 AstrBot 部署、官网当前实时可用性、用户真题版权及逐题内容质量，需要分别在有授权的实际环境验收；上述本地/CI 测试不能代替它们。

## 更新日志 / Changelog

版本日期按 Git 中首次引入该 metadata 版本的提交日期记录；不是外部发布或 GitHub Release 日期。重要提交锚点可用仓库 git log 核对。

| 版本 | 首次版本提交 |
| --- | --- |
| 0.1.0 | 201692426ee4926310276c38c539635b467e19db |
| 0.2.0 | 37313c00711e3eca69522170bc24de017b7aa572 |
| 0.3.0 | f950ca8811fa2f86ef394f722bc9109004a2d9fd |
| 0.4.0 | 6f1382c8517d3634c827cc712bf08485ddbfa4c7 |
| 0.5.0 | d45ace225b5229194cff3d936a7dfac78d8dc906 |
| 0.5.1 | cfbb5817cf8bd26a7afe263acfb8f7509f9ecfe4 |
| 0.5.2 | d4b2ddf3f1fdab76043144b7acdf44a82ad5733c |
| 0.6.0 | 3c3c70077fc0901883b0f156d7f743ab85ec89d1 |

### 0.6.0 — 2026-09-30

**新增**

- 独立 due-aware 每日任务调度与可唤醒重算；每日题目支持相对于实际成功发送时间安排答案/解析延迟揭晓，并保存逐页状态和失败诊断。
- 中文用户命令入口；手动模拟题在同一精确会话范围内短期避免立即重复。
- Library、Review、Radar、Targets 与 History 的服务端分页及 Plugin Page 多选、锁定 ID 的批量预览/确认。
- 管理详情字段编辑、可恢复软删除/恢复、结构化候选题确定性晋级/关联、案例结构化预览和固定范围数据清理/完整运行数据重置。
- 增加 `user_case` 的授权批量核验状态管理（身份保持 `user_case`）、可恢复的 Radar 忽略/移除语义、独立的人为日期确认，以及严格按“题面 → 答案 → 解析”排序的延迟揭晓设置。
- 雷达人工状态覆盖与日期 evidence 复核；保守发布日期锚点规则。

**变更与修复**

- Daily Plan 方向/题型改为 random/fixed/rotation 依赖式编辑，使用“首日方向/首日题型”而非暴露内部索引；案例、题目、题型的轮换各自独立。
- 计划确认预览中的方向模式、来源、题型模式、揭晓方式和每日任务类型使用中文标签，避免将内部枚举直接展示给操作员。
- Scheduler 的每日 due 检查不再依赖网络扫描间隔；仅当日 catch-up，计划确认后即时唤醒，成功任务按目标/日期/类型幂等。
- 数据清理按固定业务域隔离：Radar 清理不删除案例/法规来源状态，发布历史清理保留 source runs，案例清理保持官方来源和混合结构化导入可重建；核验真题清理会将关联候选安全退回可复核状态。
- 定时揭晓验证答案延迟必须大于零、解析不早于答案；手动模式不保留无意义延迟。活动显示状态覆盖与日期人工确认分别保存、分别审计。
- Question Session 长材料、小问和来源题干按预算无损分页；首次展示继续隐藏答案，答案与解析显式分开，提示使用中文命令。

**安全与真实性**

- 资料普通读取继续采用 answer-safe DTO；真实候选仍须授权操作者显式校验和确认后晋级，未提供答案不补造。
- 批量事务校验预览 ID/状态与操作者；破坏性清理使用固定范围 allowlist、快照复核及完整重置短语。
- Radar 人工状态不会证明日期真实或将未经确认日期用于提醒/自动发布；手动/用户案例不自动成为官方案例。

**迁移与验收边界**

- schema 12 → 13，保留 v12 已有数据；升级前备份数据库及 WAL/SHM。
- AstrBot CI 兼容目标保持 4.22.0 与 4.25.0；Plugin Page 仍仅由 4.25+ 宿主提供。
- 本地测试/CI 不等同于真实 QQ/NapCat、Windows 主机、真实 LLM provider 或官网持续可用验收；正式发布以 `v0.6.0` tag/GitHub Release 固定通过 exact-SHA 审查与 CI 的发布提交。

### 0.5.2 — 2026-09-27

- 修复结构化选择题进入待复核队列时选项 DTO 导致 safe preview 序列化异常的问题。
- 保持题目复核只返回安全题干/选项预览，不向复核详情暴露答案、解析和原始完整片段。
- 隔离验收真实客观题候选数据处理链：445 题、11 份材料、2,781 个 block；候选数据仍待核验，不作为仓库内置题库。

### 0.5.1 — 2026-09-27

- 修复 Structured Material 选择题的 answer.keys 等已接受答案结构不能在 Question Session 中显式揭晓的问题。
- 旧版本已持久化的结构化选择题无需重新导入即可使用；首屏仍隐藏答案，只有显式 answer action 才揭晓来源答案。
- 同步 schema v12、README changelog 与版本说明。

### 0.5.0 — 2026-09-23

- 建立持久化、精确 QQ 会话范围的 Question Session 快照与按小问记录的揭晓事件。
- 支持题面/共享材料分页、独立小问、indefinite_choice，并将答案和解析分开显式揭晓。
- 强化真实题身份、来源与 answer_source 的持久化约束；候选题仍不能进入 verified-real inventory。
- 收紧 Library、Plugin Page、搜索、详情和待复核题目的 answer-safe 边界；兼容历史会话快照显示。
- 手动/每日题目发布与答案安全 Session 共享展示逻辑。

### 0.4.0 — 2026-09-23

- 引入 Structured Material v1 JSON 契约、确定性 validator、结构化预览和 PDF+JSON hash-bound prepare/confirm 导入。
- 保存 shared materials、stem/answer/explanation blocks、answer requirements、subquestions、locators、provenance 和 review records。
- 结构化导入保持 real_question_candidate / pending_review；数据中的 official/verified 声明不会授予真实身份。
- 对真实 PDF 结构化、年份、answer mapping、污染内容及 block 边界进行仓库外数据审计；真实原件/题目 JSON 未提交 Git。

### 0.3.0 — 2026-09-21

- 增加 AstrBot 4.25+ embedded Plugin Page、受控 Web API、页面国际化与 dashboard navigation。
- 增加 Overview、Library、Radar、Plans、Targets、History 的资料、计划、目标和历史管理流程。
- 页面上传采用 staging 与 prepare/confirm；修正真实 Bridge 响应解包、计划恢复预览及页面翻译路径。
- 保持 4.22+ 基础插件兼容，较新 Page 能力按宿主支持情况提供。

### 0.2.0 — 2026-09-17

- 扩展活动扫描、法律活动状态/来源策略、截止提醒、官方案例与法规更新存储。
- 增加多群目标、确认式发布、每日案例/题目计划、方向/题型轮换及计划预览。
- 建立学习资料库、持久模拟题与核验真题 JSON 导入；增加 TXT/Markdown/DOCX/文本 PDF 受控解析、学习资料拆分与待复核队列。
- 扩展自然语言 LLM Tools、发布目标别名、来源记录/幂等与调度生命周期测试。

### 0.1.0 — 2026-09-17

- 建立 AstrBot Law Assistant 插件基础：/law、LLM Tools、统一 LawAssistantService、来源扩展点、SQLite schema/migration、Scheduler 与 Publisher 边界。
- 建立 Python 3.12 测试和 AstrBot v4.22.0/v4.25.0 精确源码兼容性 CI。
- 初始实现无生产赛事网站清单，不声称尚未实现的自动赛事抓取/每日内容能力可用。

### 版本规则

本项目采用 Semantic Versioning：MAJOR.MINOR.PATCH。

- **PATCH**：向后兼容的错误修复。
- **MINOR**：向后兼容的新功能。
- **MAJOR**：不兼容的 API、配置或行为变化。

任何版本号变更都必须在同一提交更新本 README 的 Changelog；历史记录仅总结该版本在本仓库中已实现的行为，不暗示未经证实的外部发布或生产验收。

## 延伸规范

- AGENTS.md：项目长期开发、可信边界、兼容性与验证规则。
- docs/structured-material-v1.md：完整的 Structured Material v1 交换格式和 validator 规则。
- metadata.yaml 与 _conf_schema.json：插件元数据及 AstrBot WebUI 配置字段事实来源。
- .github/workflows/ci.yml：当前 CI job、版本矩阵和 release artifact 事实来源。
