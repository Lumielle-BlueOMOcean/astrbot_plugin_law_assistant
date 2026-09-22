# Structured Material Exchange v1

本文件定义 Lumielle Law Assistant Phase 0.4A 使用的 UTF-8 JSON 交换格式。
它用于外部模型或人工整理者向插件提交“待验证的结构化资料”，不代表资料已经成为核验真题或官方案例。

## 顶层对象

```json
{
  "schema_version": "1.0",
  "document": {},
  "materials": [],
  "questions": [],
  "cases": []
}
```

`schema_version` 必填且目前只能是字符串 `"1.0"`。未知重大版本会被拒绝，插件不会静默解释未知字段。`materials`、`questions`、`cases` 可以为空；一份资料可以只包含题目、只包含案例，或包含公共材料与其中任意一种条目。

校验入口是 `structured_material.validate_structured_material()`，预览入口是 `structured_material.build_structured_material_preview()`。两者都是纯校验/转换操作，不写 SQLite、不保存文件、不调用网络或 LLM。

## document：原始资料身份

以下字段为必填非空字符串：

| 字段 | 含义 |
| --- | --- |
| `title` | 原始资料标题 |
| `source_type` | 来源类别，例如 `synthetic_fixture`、`user_file` |
| `original_filename` | 原始文件名 |
| `original_file_sha256` | 原始文件实际字节的 SHA-256；模板中的值只是声明 |
| `preparation_method` | `human_transcribed`、`external_model_assisted`、`plugin_extracted` 或 `mixed` |
| `verification_status` | 当前资料真实性状态，例如 `pending_review` |

可选字段包括 `source_url`、`source_description`、`exam_name`、`exam_year`、`paper`。原始文件哈希必须是 64 位十六进制字符串；下一阶段同时收到 PDF 和模板时，插件必须重新计算 PDF 字节哈希并核对，不能信任模型填入的哈希。

`preparation_method` 只记录整理方式，不决定真题或官方案例身份。结构化模板中的 `verified_real_question`、`official_case`、`verified_official` 等声明会进入待复核，校验器不会授予这些身份。官方案例仍只能通过现有可信 `court_cases`/`spp_cases` 来源路径获得身份。

## materials：公共材料

每个材料需要文档内唯一的 `id`，并有 `blocks` 数组。块的 `order` 是正整数且不能重复；使用 `order` 排序，不依赖 JSON 对象键顺序。

```json
{
  "id": "M01",
  "title": "公共案情",
  "blocks": [
    {
      "id": "M01-B01",
      "order": 1,
      "kind": "paragraph",
      "text": "案情正文。",
      "locator": "PDF第12页",
      "provenance": "source_text"
    }
  ]
}
```

支持的 `kind`：`paragraph`、`heading`、`list`、`table_text`、`image_reference`、`formula_text`。如果图片或公式没有可靠转录，仍可使用 `image_reference` 或 `formula_text` 保存引用状态，例如 `text` 写明“图片存在但尚未完整转录”，并将 `provenance` 设为 `unknown`。不能补写未读取到的条件。

每个块应提供 `locator` 和 `provenance`。缺失定位或来源标记会进入人工复核，而不是被当作已核实原文。`provenance` 只能是 `source_text`、`external_model`、`human_authored` 或 `unknown`。

## questions：题目

每道题需要文档内唯一的 `id`、原始 `source_number`、规范 `question_type`、`stem_blocks` 和 `locators`。如果所有具体问题都保存在 `subquestions` 中，顶层 `stem_blocks` 可以为空；否则题干必须有实质内容。题型使用现有规范值：

- `single_choice`
- `multiple_choice`
- `true_false`
- `short_answer`
- `case_analysis`

题干的每个段落保留为独立 block。公共材料通过 `material_refs` 引用，所有引用必须指向同一文档中存在的材料 ID。`materials[].blocks` 只保存共享事实、案情、论述材料、法条附件或其他上下文；问题正文、答题要求和参考答案不能放入公共材料。引用了公共材料的题干不得再复制同一材料正文。

```json
{
  "id": "Q001",
  "source_number": "第1题",
  "question_type": "single_choice",
  "material_refs": ["M01"],
  "stem_blocks": [
    {
      "id": "Q001-S01",
      "order": 1,
      "kind": "paragraph",
      "text": "请根据公共材料判断甲的行为。",
      "locator": "PDF第13页",
      "provenance": "source_text"
    }
  ],
  "options": [
    {"key": "A", "text": "选项一", "locator": "PDF第13页"},
    {"key": "B", "text": "选项二", "locator": "PDF第13页"}
  ],
  "subquestions": [],
  "answer_requirements": [
    {
      "order": 1,
      "text": "答题时应说明理由。",
      "locator": "PDF第13页",
      "provenance": "source_text"
    }
  ],
  "answer": {
    "keys": ["A"],
    "status": "provided",
    "provenance": "source_text",
    "locator": "PDF第14页"
  },
  "answer_status": "provided",
  "explanation_blocks": [],
  "locators": ["PDF第13-14页"],
  "verification_status": "pending_review"
}
```

选择题 `options` 使用对象数组；单选答案必须只有一个存在的 `keys`，多选答案可以有多个但每个键都必须存在。判断题答案可使用 `{"value": true, "provenance": "source_text"}`。简答题和案例分析题可把答案写成带 `blocks` 的对象。

没有原始参考答案时使用：

```json
{
  "answer": null,
  "answer_status": "not_provided",
  "answer_reason": "原始资料未提供参考答案"
}
```

答案必须与题干分开。`external_model` 或 `human_authored` 只表示补充答案/学习解答，不会被识别为原始参考答案，并会进入待复核。模型不得根据题干自行填写官方答案。

`answer_requirements` 是可选数组，用于保存字数限制、作答形式要求和评分说明。每项至少包含正整数 `order` 与非空 `text`，并可带 `locator`、`provenance`。它不是题干、小问、答案或解析，不得合并进这些字段；`PDF第N页` 这样的定位只属于 `locator` 元数据。导入时答题要求作为独立的 `answer_requirement` 结构化内容块持久化，可被资料搜索命中，但不并入默认题干或答案正文。

## 多小问

公共材料对应多个独立作答单元时，保留一个主 Question，并在 `subquestions` 中为每个小问提供独立 ID、原始小问编号、题干块、答案、解析块和定位：

```json
{
  "id": "Q002",
  "source_number": "材料题一",
  "question_type": "case_analysis",
  "material_refs": ["M01"],
  "stem_blocks": [],
  "subquestions": [
    {
      "id": "Q002-S01",
      "source_number": "（1）",
      "stem_blocks": [],
      "answer": null,
      "answer_reason": "原始资料未提供参考答案",
      "explanation_blocks": [],
      "locators": ["PDF第20页"]
    }
  ]
}
```

一道只有多个自然段的长题仍然只使用一个 Question。不能把 block ID 当成题目 ID，也不能把“题干1、题干2”伪装成独立小问。

只有原资料确实列出多个独立作答问题时才建立小问。编号列表本身不是充分依据：字数、评分和表达规范等答题要求属于 `answer_requirements`。小问答案只有在来源中的编号/题目标题能够确定性对应时才关联；无法唯一对应时保留 unresolved 状态，不按位置猜配。

## cases：案例

案例至少需要唯一 `id`、`title`、`locators` 和一组有序内容块。可以使用 `blocks`，也可以按语义使用 `basic_facts_blocks`、`issues_blocks`、`holding_blocks`、`result_blocks`、`learning_points_blocks`。这些较长字段都使用和材料相同的 block 结构。

`authority`、`case_number`、`verification_status` 只是模板声明。用户上传或外部模型整理的案例不能凭填写“最高人民法院”自动成为官方案例；校验预览会保留声明但设置 `identity_granted: false` 并进入待复核。真实官方身份仍由现有官方来源专属路径决定。

## 错误分级

每条 issue 都包含稳定的 `code`、定位 `path`、`severity` 和中文 `message`，必要时包含 `item_id`。

- `fatal`：整份模板拒绝，例如 JSON 语法错误、未知版本、根字段类型错误、重复关键 ID、悬空 `material_ref`、超限或过深 JSON。
- `error`：条目不能进入可用预览，例如题干为空、题型不支持、选择题选项不足、答案键不存在。其他独立条目仍可继续预览。
- `review`：条目结构可读但必须人工确认，例如没有参考答案、没有定位、图片未转录、答案来自外部模型、题干疑似合并了多个题、官方身份声明。

没有原始参考答案不等于整份资料损坏；只要明确提供 `answer_reason`，该题会保留并标记待复核。

## 资源限制

当前校验器限制 JSON 10 MiB、文本总长度 600000 字符、材料 500 份、题目 1000 道、案例 500 个、单条目内容块 500 个、单块文本 200000 字符、嵌套深度 20 层。超长题干本身不是错误，合理分段且不超过单块/总量限制即可。

## 预览对象

`build_structured_material_preview()` 返回可 JSON 序列化对象，包含：

- `schema_version`、`document`；
- `counts.questions/cases/materials`；
- 顶层题组数、真实小问数、答题要求数；
- 小问答案已映射/未解决数，以及材料/题干重复和材料区段污染警告数；
- `counts.processable`、`counts.entry_errors`、`counts.review_items`；
- 材料块数；
- 每题标题/原始题号、方向、题型、公共材料引用、题干与解析块数、小问数、答案状态/来源、定位和复核状态；
- 每个案例的标题、方向、块数、定位和复核状态；
- 全部 issues 及其路径。

预览不核对未提供的原始 PDF，不生成正式导入确认 token，也不写入数据库。0.4B 的正式导入同时接收原始 PDF 与本 JSON：后端重新计算 PDF 字节 SHA-256、JSON 原文 SHA-256 和规范化 payload SHA-256，只有三者与 prepare 快照一致时才允许 confirm。prepare 阶段不写资料库；confirm 通过后才把原始 PDF、JSON、公共材料、内容块、小问、来源关系和候选条目写入 SQLite。

正式导入保留两条来源记录：原始文件来源和结构化 JSON 来源。条目写入 `learning_items` 作为兼容检索索引，同时写入结构化表保存原始顺序和富结构。题目身份固定为 `real_question_candidate`，案例身份固定为用户候选；`verification_status` 为 `pending_review`，不会因为 JSON 中的声明自动升级为核验真题或官方案例。带有缺失定位、未提供答案或外部整理答案的条目会在预览中标为 review，并关联 `learning_review_items`。

当前 Plugin Page 只允许以原始 PDF + JSON 的组合启动 0.4B 导入；旧的 TXT/Markdown/DOCX/PDF `prepare → confirm` 仍走原有分段路径，不能用旧正则分段器替代结构化导入。原始资料和 JSON 只保存于插件数据目录，不进入 Git。

## 当前边界

0.4B 不实现官方身份升级、答案继续作答协议、独立会话系统、RAG 或每日任务接管。正式导入仍是候选资料保存与检索基础；真实 PDF 的内容、版权和逐题核验责任由操作者承担，插件不会把外部模型整理结果当作官方事实。
