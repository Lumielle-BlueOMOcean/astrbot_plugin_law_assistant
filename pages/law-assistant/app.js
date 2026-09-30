const bridge = window.AstrBotPluginPage;

const SUBJECTS = [
  ["intellectual_property", "知识产权"],
  ["civil_commercial", "民商法"],
  ["judicial_practice", "司法实务"],
  ["economic_law", "经济法"],
  ["criminal_law", "刑法"],
  ["criminal_procedure", "刑事诉讼法"],
  ["civil_law", "民法"],
  ["commercial_law", "商法"],
  ["civil_procedure", "民事诉讼法"],
  ["constitutional_administrative", "宪法与行政法"],
  ["other", "其他"],
];
const QUESTION_TYPES = [
  ["single_choice", "单项选择题"],
  ["multiple_choice", "多项选择题"],
  ["indefinite_choice", "不定项选择题"],
  ["true_false", "判断题"],
  ["short_answer", "简答题"],
  ["case_analysis", "案例分析题"],
];
const labels = {
  overview: ["page.nav.overview", "总览"],
  library: ["page.nav.library", "资料库"],
  radar: ["page.nav.radar", "活动雷达"],
  plans: ["page.nav.plans", "每日计划"],
  targets: ["page.nav.targets", "群与发布"],
  history: ["page.nav.history", "运行记录"],
};
const state = {
  route: "overview",
  context: {},
  libraryTab: "materials",
  libraryItemId: null,
  historyTab: "daily",
  renderGeneration: 0,
  viewRequests: Object.create(null),
  planDrafts: Object.create(null),
  pages: Object.create(null),
  selectedIds: new Set(),
};
const PAGE_I18N_NAMESPACE = "pages.law-assistant.";

function t(key, fallback) {
  if (!bridge || typeof bridge.t !== "function") return fallback;
  const rawKey = String(key || "");
  const pageKey = rawKey.startsWith(PAGE_I18N_NAMESPACE)
    ? rawKey
    : `${PAGE_I18N_NAMESPACE}${rawKey}`;
  return bridge.t(pageKey, fallback);
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function button(text, handler, className = "") {
  const element = node("button", text, className);
  element.type = "button";
  element.addEventListener("click", handler);
  return element;
}

function selectControl(options, value = "") {
  const select = document.createElement("select");
  options.forEach(([optionValue, label]) => {
    const option = node("option", label);
    option.value = optionValue;
    option.selected = optionValue === value;
    select.append(option);
  });
  return select;
}

function textInput(value = "", type = "text") {
  const input = document.createElement("input");
  input.type = type;
  input.value = value ?? "";
  return input;
}

function textArea(value = "") {
  const input = document.createElement("textarea");
  input.value = value ?? "";
  return input;
}

function editableLines(value) {
  if (Array.isArray(value)) return value.join("\n");
  return value == null ? "" : String(value);
}

function parseEditableJson(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  try { return JSON.parse(text); } catch { return text; }
}

function labeled(label, control, className = "form-field") {
  const wrap = node("label", undefined, className);
  wrap.append(node("span", label, "field-label"), control);
  return wrap;
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.join("、");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function subjectLabel(value) {
  return SUBJECTS.find(([key]) => key === value)?.[1] || formatValue(value);
}

function questionTypeLabel(value) {
  return QUESTION_TYPES.find(([key]) => key === value)?.[1] || formatValue(value);
}

function safeLink(url, label = url) {
  const value = String(url || "").trim();
  try {
    const parsed = new URL(value);
    if (!["http:", "https:"].includes(parsed.protocol)) return node("span", value);
    const link = node("a", label);
    link.href = parsed.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch {
    return node("span", value);
  }
}

function sectionTitle(title, description) {
  const wrap = node("div", undefined, "section-heading");
  wrap.append(node("h2", title));
  if (description) wrap.append(node("p", description, "muted"));
  return wrap;
}

function setFlash(message, error = false) {
  const flash = document.getElementById("flash");
  flash.textContent = message || "";
  flash.className = error ? "flash error" : "flash";
  flash.hidden = !message;
}

function beginViewRequest(key) {
  const next = (state.viewRequests[key] || 0) + 1;
  state.viewRequests[key] = next;
  return next;
}

function isCurrent(generation, route = state.route) {
  return generation === state.renderGeneration && route === state.route;
}

function isCurrentRequest(key, requestId, generation, route = state.route) {
  return isCurrent(generation, route) && state.viewRequests[key] === requestId;
}

async function apiGet(endpoint, params = {}) {
  const result = await bridge.apiGet(endpoint, params);
  if (result == null || result.success === false) throw new Error(result?.message || t("page.error.request", "请求失败"));
  return result;
}

async function apiPost(endpoint, body = {}) {
  const result = await bridge.apiPost(endpoint, body);
  if (result == null || result.success === false) throw new Error(result?.message || t("page.error.request", "请求失败"));
  return result;
}

function renderNavigation() {
  const navigation = document.getElementById("navigation");
  navigation.replaceChildren();
  Object.entries(labels).forEach(([route, [key, fallback]]) => {
    const item = button(t(key, fallback), () => navigate(route), "nav-button");
    item.classList.toggle("active", route === state.route);
    navigation.append(item);
  });
}

function metricGrid(values) {
  const grid = node("div", undefined, "metric-grid");
  values.forEach(([title, value, detail]) => {
    const card = node("article", undefined, "metric-card");
    card.append(node("span", title, "metric-label"));
    card.append(node("strong", value, "metric-value"));
    if (detail) card.append(node("span", detail, "metric-detail"));
    grid.append(card);
  });
  return grid;
}

function table(rows, columns, actions) {
  const wrapper = node("div", undefined, "table-wrap");
  const tableElement = node("table");
  const head = node("tr");
  columns.forEach((column) => head.append(node("th", column[1])));
  if (actions) head.append(node("th", t("page.table.actions", "操作")));
  const thead = node("thead");
  thead.append(head);
  tableElement.append(thead);
  const body = node("tbody");
  (rows || []).forEach((row) => {
    const tr = node("tr");
    columns.forEach(([key, , render]) => {
      const cell = node("td");
      if (render) cell.append(render(row));
      else cell.textContent = formatValue(row?.[key]);
      tr.append(cell);
    });
    if (actions) {
      const cell = node("td", undefined, "actions");
      cell.append(actions(row, tr));
      tr.append(cell);
    }
    body.append(tr);
  });
  tableElement.append(body);
  wrapper.append(tableElement);
  return wrapper;
}

function renderPager(metadata, onPage, onPageSize) {
  const pager = node("div", undefined, "toolbar pagination");
  const current = Number(metadata?.page || 1);
  const pageCount = Number(metadata?.page_count || 0);
  const size = Number(metadata?.page_size || 20);
  const sizeControl = selectControl([["20", "20"], ["50", "50"], ["100", "100"]], String(size));
  const previous = button(t("page.pagination.previous", "上一页"), () => onPage(Math.max(1, current - 1)));
  const next = button(t("page.pagination.next", "下一页"), () => onPage(current + 1));
  previous.disabled = current <= 1;
  next.disabled = pageCount === 0 || current >= pageCount;
  sizeControl.addEventListener("change", () => onPageSize(Number(sizeControl.value)));
  pager.append(previous, node("span", `${current} / ${pageCount || 0} · ${metadata?.total || 0} 条`), next, sizeControl);
  return pager;
}

function detailBlock(title, value, className = "detail-block") {
  const block = node("section", undefined, className);
  block.append(node("h3", title));
  if (value instanceof Node) block.append(value);
  else block.append(node("pre", formatValue(value), "evidence"));
  return block;
}

function keyValueRows(values) {
  const list = node("dl", undefined, "key-values");
  Object.entries(values).forEach(([key, value]) => {
    list.append(node("dt", key), node("dd", formatValue(value)));
  });
  return list;
}

async function renderOverview() {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "overview";
  const requestId = beginViewRequest(requestKey);
  const view = document.getElementById("view-overview");
  view.replaceChildren(sectionTitle(t("page.nav.overview", "总览"), t("page.overview.description", "查看插件、来源、学习资料与最近运行状态。")));
  const loading = node("p", t("page.loading", "正在加载…"), "muted");
  view.append(loading);
  try {
    const data = await apiGet("overview");
    if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
    loading.remove();
    const plugin = data.plugin || {};
    const radar = data.radar || {};
    const learning = data.learning || {};
    view.append(metricGrid([
      [t("page.overview.version", "插件版本"), plugin.version || "0.6.0", `schema v${data.schema_version ?? "—"}`],
      [t("page.overview.currentEvents", "当前活动"), radar.current || 0, t("page.overview.currentEventsDetail", "排除历史与待复核")],
      [t("page.overview.cases", "官方案例"), learning.official_case || 0, t("page.overview.casesDetail", "可用于每日案例")],
      [t("page.overview.realQuestions", "核验真题"), learning.verified_real || 0, t("page.overview.realQuestionsDetail", "来源身份独立保存")],
      [t("page.overview.targets", "绑定群"), data.targets?.count || 0, t("page.overview.targetsDetail", "启用发布目标")],
      [t("page.overview.questionSessions", "进行中的题目会话"), (data.question_sessions || []).length, t("page.overview.questionSessionsDetail", "按 QQ 会话独立保存")],
    ]));
    view.append(sectionTitle(t("page.overview.questionSessions", "进行中的题目会话"), t("page.overview.questionSessionsDescription", "仅显示进度与揭晓状态，不返回题目正文或答案。")));
    view.append(table(data.question_sessions || [], [
      ["target_label", t("page.overview.sessionTarget", "会话/群")],
      ["identity_label", t("page.overview.sessionIdentity", "题目身份")],
      ["subject", t("page.history.subject", "方向")],
      ["question_type", t("page.overview.sessionType", "题型")],
      ["prompt_index", t("page.overview.sessionPrompt", "小问"), (row) => node("span", `${row.prompt_index}/${row.prompt_count || 1}`)],
      ["material_index", t("page.overview.sessionMaterials", "材料进度"), (row) => node("span", `${row.material_index}/${row.material_count}`)],
      ["answer_revealed", t("page.overview.sessionAnswer", "答案"), (row) => node("span", row.answer_revealed ? t("page.overview.revealed", "已揭晓") : t("page.overview.hidden", "未揭晓"))],
      ["explanation_revealed", t("page.overview.sessionExplanation", "解析"), (row) => node("span", row.explanation_revealed ? t("page.overview.revealed", "已揭晓") : t("page.overview.hidden", "未揭晓"))],
    ]));
    view.append(sectionTitle(t("page.overview.recent", "最近每日运行"), t("page.overview.recentDescription", "失败与跳过记录会保留原因，不会伪装成成功。")));
    view.append(table((data.recent?.daily || []).slice(0, 8), [
      ["content_date", t("page.history.date", "日期")],
      ["content_type", t("page.history.type", "内容类型")],
      ["status", t("page.history.status", "状态")],
      ["resolved_subject", t("page.history.subject", "方向"), (row) => node("span", subjectLabel(row.resolved_subject))],
      ["error_summary", t("page.history.reason", "说明")],
    ]));
  } catch (error) {
    if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
    loading.textContent = error.message;
    setFlash(error.message, true);
  }
}

function tabBar(items, current, select) {
  const tabs = node("div", undefined, "tab-bar");
  items.forEach(([value, label]) => {
    const tab = button(label, () => select(value), "tab-button");
    tab.classList.toggle("active", value === current);
    tabs.append(tab);
  });
  return tabs;
}

async function renderLibrary() {
  const generation = state.renderGeneration;
  const route = state.route;
  const view = document.getElementById("view-library");
  view.replaceChildren(sectionTitle(t("page.nav.library", "资料库"), t("page.library.description", "搜索资料、查看证据、修改允许字段并处理待复核条目。")));
  view.append(tabBar([
    ["materials", t("page.library.materials", "资料")],
    ["real-questions", "核验真题库存"],
    ["imports", t("page.library.imports", "文件导入")],
    ["reviews", t("page.library.reviews", "待复核")],
  ], state.libraryTab, (value) => { state.libraryTab = value; renderLibrary(); }));
  if (!isCurrent(generation, route)) return;
  if (state.libraryTab === "real-questions") await renderRealQuestions(view, generation, route);
  else if (state.libraryTab === "imports") await renderImports(view);
  else if (state.libraryTab === "reviews") await renderReviews(view);
  else await renderMaterials(view);
  if (isCurrent(generation, route)) renderDataManagement(view);
}

async function renderRealQuestions(view, generation = state.renderGeneration, route = state.route) {
  const result = node("div");
  view.append(sectionTitle("核验真题库存", "独立导入的真题可在此查看、修订校验、停用或恢复；关联结构化候选请从资料条目管理，以保持两侧一致。"), result);
  try {
    const pageState = state.pages.realQuestions || { page: 1, page_size: 20 };
    const data = await apiGet("management/real-questions", pageState);
    if (!isCurrent(generation, route)) return;
    const items = data.items || [];
    result.append(node("p", `库存记录 ${data.total ?? items.length} 条；本页 ${items.length} 条，其中当前可用于真题检索 ${items.filter((item) => item.selectable).length} 条。`, "muted"));
    result.append(table(items, [
      ["id", "ID"], ["source_name", "来源"], ["exam_name", "考试"], ["exam_year", "年份"],
      ["subject", "方向", (row) => node("span", subjectLabel(row.subject))],
      ["question_type", "题型", (row) => node("span", questionTypeLabel(row.question_type))],
      ["selectable", "可检索", (row) => node("span", row.selectable ? "是" : "否")],
      ["management_mode", "管理路径", (row) => node("span", row.management_mode === "linked" ? `关联资料 ${row.linked_item_ids.join(", ")}` : "独立真题")],
    ], (row) => button("查看 / 管理", async () => {
      const panel = node("article", undefined, "library-inline-detail-row");
      await renderRealQuestionDetail(panel, row.id, generation, route);
      result.append(panel);
    })));
    result.append(renderPager(data, async (page) => {
      state.pages.realQuestions = { page, page_size: pageState.page_size };
      await renderLibrary();
    }, async (pageSize) => {
      state.pages.realQuestions = { page: 1, page_size: pageSize };
      await renderLibrary();
    }));
  } catch (error) {
    if (isCurrent(generation, route)) result.append(node("p", error.message, "error-text"));
  }
}

async function renderRealQuestionDetail(container, questionId, generation, route) {
  const data = await apiGet("management/real-question", { id: questionId });
  if (!isCurrent(generation, route)) return;
  const question = data.question;
  const panel = node("section", undefined, "form-card");
  panel.append(node("h3", `核验真题 #${question.id}`));
  if (question.management_mode === "linked") {
    panel.append(node("p", `此真题关联资料条目 ${question.linked_item_ids.join(", ")}。请在资料库打开关联条目进行修改或停用，避免真题库存与资料视图分离。`));
    container.append(panel);
    return;
  }
  const subject = selectControl(SUBJECTS, question.subject);
  const questionType = selectControl(QUESTION_TYPES, question.question_type);
  const answerSource = selectControl([
    ["not_provided", "未提供"], ["official", "官方答案"],
    ["third_party", "第三方参考答案"], ["user_verified", "用户核验"], ["unverified", "未核验"],
  ], question.answer_source);
  const fields = {
    source_name: textInput(question.source_name), exam_name: textInput(question.exam_name),
    exam_year: textInput(question.exam_year), exam_date: textInput(question.exam_date, "date"),
    paper: textInput(question.paper), question_number: textInput(question.question_number),
    source_url: textInput(question.source_url), source_locator: textInput(question.source_locator),
    stem: textArea(question.stem), options: textArea(JSON.stringify(question.options, null, 2)),
    answer: textArea(question.answer == null ? "" : JSON.stringify(question.answer, null, 2)),
    explanation: textArea(question.explanation),
  };
  panel.append(
    labeled("方向", subject), labeled("题型", questionType),
    ...Object.entries(fields).map(([key, control]) => labeled(key, control)),
    labeled("答案来源身份", answerSource),
  );
  const save = button("保存并重新校验", async () => {
    try {
      const changes = Object.fromEntries(Object.entries(fields).map(([key, control]) => [key, control.value]));
      changes.subject = subject.value;
      changes.question_type = questionType.value;
      changes.answer_source = answerSource.value;
      for (const key of ["options", "answer"]) {
        try { changes[key] = JSON.parse(changes[key]); }
        catch { throw new Error(`${key} 必须是有效 JSON`); }
      }
      const outcome = await apiPost("management/real-question-update", { question_id: question.id, changes });
      if (outcome.success === false) throw new Error(outcome.message || "真题更新失败");
      setFlash("真题已重新校验并保存");
      await renderLibrary();
    } catch (error) { setFlash(error.message, true); }
  }, "primary");
  panel.append(save);
  const activeButton = button(question.selectable ? "预览停用真题" : "预览恢复并重新核验", () => {
    const nextActive = !question.selectable;
    const confirmation = node("article", undefined, "confirm-panel");
    confirmation.append(
      node("p", nextActive ? "确认将该条目恢复为用户已核验，并重新加入真题检索库存？" : "确认将该条目从真题检索库存停用？记录仍会保留，可恢复。"),
      button("确认", async () => {
        try {
          const outcome = await apiPost("management/real-question-status", { question_id: question.id, active: nextActive });
          if (outcome.success === false) throw new Error(outcome.message || "状态更新失败");
          setFlash(nextActive ? "真题已恢复并重新核验" : "真题已停用，可恢复");
          await renderLibrary();
        } catch (error) { setFlash(error.message, true); }
      }, "primary"),
      button(t("page.actions.cancel", "取消"), () => confirmation.remove()),
    );
    panel.append(confirmation);
  }, "secondary");
  panel.append(activeButton);
  container.append(panel);
}

function renderDataManagement(view) {
  const section = node("section", undefined, "form-card");
  section.append(sectionTitle(t("page.management.title", "数据管理"), t("page.management.description", "清理仅作用于固定数据范围；预览不会写入，确认时会重新核对数据快照。")));
  const scope = selectControl([
    ["radar", "活动雷达数据"], ["library", "学习资料库与待复核"],
    ["verified_questions", "核验真题库存"], ["cases", "案例库"],
    ["delivery_history", "活动/提醒/每日执行历史"], ["question_sessions", "答题会话历史"],
    ["daily_plans", "每日计划与待发送揭晓"], ["targets", "群目标绑定"], ["all_runtime", t("page.management.allRuntime", "全部插件运行数据")],
  ]);
  const prepare = button(t("page.management.previewClear", "预览清理"), prepareClear, "secondary");
  const controls = node("div", undefined, "toolbar"); controls.append(scope, prepare);
  const result = node("div"); section.append(controls, result); view.append(section);

  async function prepareClear() {
    prepare.disabled = true;
    try {
      const prepared = await apiPost("management/clear-prepare", { scope: scope.value });
      const panel = node("article", undefined, "confirm-panel");
      panel.append(node("h3", `清理预览：${prepared.scope_label || prepared.scope}`));
      if (prepared.delete_description) panel.append(node("p", `将删除：${prepared.delete_description}`));
      panel.append(node("p", `涉及 ${prepared.total || 0} 行。确认前不会删除任何数据。`));
      panel.append(table(Object.entries(prepared.counts || {}).map(([name, count]) => ({ name: prepared.labels?.[name] || name, count })), [["name", "数据类别"], ["count", "预计删除行数"]]));
      panel.append(detailBlock("不会删除", prepared.retained || []));
      let typedConfirmation;
      if (prepared.scope === "all_runtime") {
        typedConfirmation = textInput(); typedConfirmation.placeholder = t("page.management.typeResetPhrase", "输入：清空全部数据");
        panel.append(labeled(t("page.management.resetPhrase", "完整重置确认短语"), typedConfirmation));
      }
      const confirm = button(t("page.management.confirmClear", "确认清理"), async () => {
        confirm.disabled = true;
        try {
          await apiPost("management/clear-confirm", {
            token: prepared.token,
            ...(typedConfirmation ? { typed_confirmation: typedConfirmation.value } : {}),
          });
          panel.remove(); setFlash("清理已完成"); await renderRoute();
        } catch (error) { confirm.disabled = false; setFlash(error.message, true); }
      }, "primary");
      if (typedConfirmation) {
        confirm.disabled = typedConfirmation.value !== "清空全部数据";
        typedConfirmation.addEventListener("input", () => {
          confirm.disabled = typedConfirmation.value !== "清空全部数据";
        });
      }
      panel.append(confirm, button(t("page.actions.cancel", "取消"), () => panel.remove()));
      result.replaceChildren(panel);
    } catch (error) { setFlash(error.message, true); }
    finally { prepare.disabled = false; }
  }
}

async function renderMaterials(view) {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "library-materials";
  const controls = node("div", undefined, "toolbar");
  const query = textInput(); query.placeholder = t("page.library.searchPlaceholder", "关键词");
  const type = selectControl([
    ["", t("page.library.allTypes", "全部类型")], ["case", "案例"], ["question", "题目"],
    ["mock_question", "模拟题"], ["real_question_candidate", "真题候选"], ["note", "笔记"],
  ]);
  const subject = selectControl([["", "全部方向"], ...SUBJECTS]);
  const includeInactive = document.createElement("input"); includeInactive.type = "checkbox";
  const includeInactiveField = labeled(t("page.management.includeDeleted", "包括已软删除资料"), includeInactive, "checkbox-field");
  const search = button(t("page.actions.search", "搜索"), () => runSearch(1, currentPageSize(), true), "primary");
  controls.append(query, type, subject, includeInactiveField, search);
  includeInactive.addEventListener("change", () => runSearch(1, currentPageSize(), true));
  const batchControls = node("div", undefined, "toolbar");
  const selectionSummary = node("span", "已选 0 条", "muted");
  const selectPage = button(t("page.management.selectPage", "选择本页"), async () => {
    const selectAll = !currentRows.length || !currentRows.every((row) => state.selectedIds.has(row.id));
    currentRows.forEach((row) => selectAll ? state.selectedIds.add(row.id) : state.selectedIds.delete(row.id));
    await runSearch(currentPage, currentPageSize());
  });
  const editTitle = textInput(); editTitle.placeholder = t("page.management.batchTitle", "批量设置标题");
  const editSubjects = textInput(); editSubjects.placeholder = t("page.management.batchSubjects", "批量设置方向，逗号分隔");
  const prepareEdit = button(t("page.management.previewEdit", "预览批量编辑"), () => {
    const changes = {};
    if (editTitle.value.trim()) changes.title = editTitle.value;
    if (editSubjects.value.trim()) changes.subjects = editSubjects.value;
    return prepareBatch("edit", changes);
  }, "secondary");
  const prepareDelete = button(t("page.management.previewDelete", "预览软删除"), () => prepareBatch("delete"), "secondary");
  const prepareRestore = button(t("page.management.previewRestore", "预览恢复"), () => prepareBatch("restore"), "secondary");
  const preparePromote = button(t("page.management.previewPromote", "预览核验身份升格"), () => prepareBatch("promote"), "secondary");
  const caseVerificationStatus = selectControl([
    ["user_verified", "标记为用户已核验"],
    ["unverified", "撤销用户核验"],
  ]);
  const prepareCaseVerification = button(t("page.management.previewCaseVerification", "预览用户案例核验"), () => prepareBatch("verify_case", { verification_status: caseVerificationStatus.value }), "secondary");
  batchControls.append(selectionSummary, selectPage, editTitle, editSubjects, prepareEdit, prepareDelete, prepareRestore, preparePromote, labeled(t("page.management.caseVerificationStatus", "用户案例核验状态"), caseVerificationStatus), prepareCaseVerification);
  const result = node("div");
  view.append(controls, batchControls, result);
  let currentRows = [];
  let expandedDetailRow = null;
  let expandedDetailId = null;
  let currentPage = state.pages.library?.page || 1;
  const currentPageSize = () => state.pages.library?.page_size || 20;

  async function runSearch(page = 1, pageSize = 20, resetSelection = false) {
    expandedDetailRow?.remove();
    expandedDetailRow = null;
    expandedDetailId = null;
    state.libraryItemId = null;
    if (resetSelection) state.selectedIds = new Set();
    currentPage = page;
    state.pages.library = { page, page_size: pageSize };
    const requestId = beginViewRequest(requestKey);
    result.replaceChildren(node("p", t("page.loading", "正在加载…"), "muted"));
    try {
      const data = await apiGet("management/library-search", { query: query.value, type: type.value, subject: subject.value, include_inactive: includeInactive.checked, page, page_size: pageSize });
      if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
      const rows = data.items || [];
      currentRows = rows;
      selectionSummary.textContent = `已选 ${state.selectedIds.size} 条`;
      selectPage.textContent = rows.length && rows.every((row) => state.selectedIds.has(row.id)) ? t("page.management.unselectPage", "取消本页选择") : t("page.management.selectPage", "选择本页");
      result.replaceChildren(node("p", `找到 ${data.total ?? data.count ?? 0} 条资料。`, "muted"));
      result.append(table(rows, [
        ["id", "选择", (row) => {
          const checkbox = document.createElement("input"); checkbox.type = "checkbox";
          checkbox.checked = state.selectedIds.has(row.id);
          checkbox.addEventListener("change", () => {
            if (checkbox.checked) state.selectedIds.add(row.id); else state.selectedIds.delete(row.id);
            selectionSummary.textContent = `已选 ${state.selectedIds.size} 条`;
            selectPage.textContent = rows.length && rows.every((entry) => state.selectedIds.has(entry.id)) ? t("page.management.unselectPage", "取消本页选择") : t("page.management.selectPage", "选择本页");
          });
          return checkbox;
        }],
        ["id", "ID"], ["identity", "身份"], ["title", "标题"],
        ["subjects", "方向", (row) => node("span", (row.subjects || []).map(subjectLabel).join("、"))],
        ["verification_status", "核验"], ["updated_at", "更新时间"],
      ], (row, tableRow) => button(t("page.actions.view", "查看"), async () => {
        if (expandedDetailId === row.id) {
          expandedDetailRow?.remove();
          expandedDetailRow = null;
          expandedDetailId = null;
          state.libraryItemId = null;
          return;
        }
        expandedDetailRow?.remove();
        const detailRow = node("tr", undefined, "library-inline-detail-row");
        const detailCell = node("td");
        detailCell.colSpan = 7;
        detailRow.append(detailCell);
        tableRow.after(detailRow);
        expandedDetailRow = detailRow;
        expandedDetailId = row.id;
        await renderLibraryDetail(detailCell, row.id, generation, route);
      })));
      result.append(renderPager(data, (nextPage) => runSearch(nextPage, pageSize, true), (nextSize) => runSearch(1, nextSize, true)));
    } catch (error) {
      if (isCurrentRequest(requestKey, requestId, generation, route)) result.replaceChildren(node("p", error.message, "error-text"));
    }
  }

async function prepareBatch(action, changes = {}) {
    const itemIds = [...state.selectedIds];
    if (!itemIds.length) return setFlash("请先选择当前资料条目", true);
    if (action === "edit" && !Object.values(changes).some((value) => String(value || "").trim())) return setFlash("请至少填写批量标题或方向", true);
    try {
      const prepared = await apiPost("management/batch-prepare", { action, item_ids: itemIds, changes });
      const panel = node("article", undefined, "confirm-panel");
      panel.append(node("h3", `批量操作预览：${action}`), node("p", `将作用于 ${prepared.count} 条，ID 已锁定。`));
      if (action === "verify_case") panel.append(node("p", "仅更新 user_case 核验状态，不会创建或授予 official_case 身份。"));
      if (action === "promote") {
        panel.append(node("p", `可核验 ${prepared.promotable_count || 0} 条；已关联 ${prepared.already_linked_count || 0} 条；待补充答案 ${prepared.unresolved_answer_count || 0} 条；将更新已有库存 ${prepared.upsert_count || 0} 条。`));
        if (prepared.validation_failures?.length) panel.append(table(prepared.validation_failures, [["item_id", "ID"], ["reason", "校验失败"]]));
      }
      const previewItems = (prepared.items || []).map((item) => ({ ...item, id: item.id ?? item.item_id }));
      panel.append(table(previewItems, [["id", "ID"], ["title", "标题"], ["identity", "身份"], ["verification_before", "当前核验状态"], ["verification_after", "操作后核验状态"], ["eligible", "可处理"], ["reason", "说明"], ["normalized", "规范化核验预览"], ["active", "启用"]]));
      const confirm = button(t("page.actions.confirm", "确认应用"), async () => {
        confirm.disabled = true;
        try {
          const outcome = await apiPost("management/batch-confirm", { token: prepared.token });
          panel.remove(); state.selectedIds = new Set();
          const handled = outcome.count ?? outcome.results?.length ?? outcome.changed_ids?.length ?? prepared.count;
          setFlash(`已处理 ${handled} 条`);
          await runSearch(currentPage, currentPageSize());
        }
        catch (error) { confirm.disabled = false; setFlash(error.message, true); }
      }, "primary");
      panel.append(confirm, button(t("page.actions.cancel", "取消"), () => panel.remove()));
      result.prepend(panel);
    } catch (error) { setFlash(error.message, true); }
  }

  await runSearch(currentPage, state.pages.library?.page_size || 20, false);
}

async function prepareModerationBatch(domain, itemIds, changes, container, refresh) {
  if (!itemIds.length) return setFlash("请先选择本页记录", true);
  try {
    const prepared = await apiPost("management/moderation-batch-prepare", {
      domain, item_ids: itemIds, changes,
    });
    const panel = node("article", undefined, "confirm-panel");
    panel.append(
      node("h3", domain === "radar" && changes.status === "ignored" ? "活动批量删除/移除（可恢复）预览" : domain === "radar" ? "活动批量状态预览" : "复核状态批量预览"),
      node("p", `将处理 ${prepared.count} 条，所选 ID 已锁定；确认前不会写入。`),
      table(prepared.items || [], [
        ["id", "ID"], ["title", "活动/条目"], ["material_type", "资料类型"],
        ["status_before", "当前状态"], ["status_after", "操作后状态"],
        ["review_reason", "复核原因"], ["action", "操作"],
      ]),
    );
    if (domain === "radar" && changes.status === "ignored") panel.append(node("p", "仅标记为已忽略；可通过“清除人工状态”恢复，不会删除活动记录。", "muted"));
    const confirm = button(t("page.actions.confirm", "确认应用"), async () => {
      confirm.disabled = true;
      try {
        const outcome = await apiPost("management/moderation-batch-confirm", { token: prepared.token });
        panel.remove();
        setFlash(`已处理 ${outcome.count ?? prepared.count} 条`);
        await refresh();
      } catch (error) { confirm.disabled = false; setFlash(error.message, true); }
    }, "primary");
    panel.append(confirm, button(t("page.actions.cancel", "取消"), () => panel.remove()));
    container.prepend(panel);
  } catch (error) { setFlash(error.message, true); }
}

async function renderLibraryDetail(container, itemId, parentGeneration = state.renderGeneration, parentRoute = state.route, replace = false) {
  state.libraryItemId = itemId;
  const requestKey = `library-detail-${itemId}`;
  const requestId = beginViewRequest(requestKey);
  let data;
  try { data = await apiGet("library/manage-item", { id: itemId }); }
  catch (error) { if (isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) setFlash(error.message, true); return; }
  if (!isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) return;
  const item = data.item || {};
  const isQuestion = item.item_type === "question";
  const panel = node("article", undefined, "detail-panel");
  panel.append(sectionTitle(`${t("page.library.detail", "资料详情")} #${item.id}`, item.title));
  panel.append(keyValueRows({ 身份: item.identity, 核验: item.verification_status, 方向: (item.subjects || []).map(subjectLabel), 创建者: item.created_by, 更新时间: item.updated_at }));
  if (data.case) panel.append(detailBlock("案例结构化整理", keyValueRows({ 案号: data.case.case_number, 权威机关: data.case.authority, 案情: data.case.case_summary, 争议焦点: data.case.issues, 裁判或检察要旨: data.case.reasoning, 结果: data.case.result, 学习要点: data.case.practice_notes })));
  if (data.question) {
    panel.append(detailBlock("题目结构化整理", keyValueRows({ 题型: questionTypeLabel(data.question.question_type), 题干: data.question.stem, 选项: data.question.options, 考试: data.question.exam_name, 年份: data.question.exam_year, 试卷: data.question.paper, 题号: data.question.question_number, 答案来源: data.question.answer_source })));
    panel.append(node("p", "题目答案与解析通过 Question Session 显式揭晓，资料页默认不提前展示。", "muted"));
  }
  if (item.item_type !== "question" && item.metadata?.body) panel.append(detailBlock("笔记正文", item.metadata.body));
  if (item.item_type !== "question" && item.metadata?.note) panel.append(detailBlock("人工备注", item.metadata.note));
  const evidence = node("div");
  (data.sources || []).forEach((source) => {
    const sourceCard = node("div", undefined, "source-card");
    const sourceLink = (data.source_links || []).find((link) => link.source_id === source.id);
    sourceCard.append(keyValueRows({ 来源标题: source.title, 来源哈希: source.content_hash, 原文件: source.original_filename, MIME: source.mime_type, 定位: sourceLink?.locator, 关联: sourceLink?.relationship }));
    if (source.source_url) sourceCard.append(safeLink(source.source_url));
    if (item.item_type !== "question" && source.raw_text) sourceCard.append(node("pre", source.raw_text, "evidence"));
    evidence.append(sourceCard);
  });
  panel.append(detailBlock("原始 evidence", evidence));
  const edit = node("div", undefined, "form-grid");
  const title = textInput(item.title);
  const subjects = textInput((item.subjects || []).join(","));
  let bodyEditor = null;
  let noteEditor = null;
  edit.append(labeled("标题", title), labeled("方向（可用规范名称或中文）", subjects));
  if (!isQuestion) {
    bodyEditor = textArea(item.metadata?.body || "");
    noteEditor = textArea(item.metadata?.note || "");
    edit.append(labeled("资料正文", bodyEditor), labeled("备注", noteEditor));
    if (data.case) {
      const caseNumber = textInput(data.case.case_number || "");
      const authority = textInput(data.case.authority || "");
      const caseSummary = textArea(data.case.case_summary || "");
      const issues = textArea(editableLines(data.case.issues));
      const reasoning = textArea(data.case.reasoning || "");
      const resultText = textArea(data.case.result_text || data.case.result || "");
      const practiceNotes = textArea(editableLines(data.case.practice_notes));
      edit.append(
        labeled("案号", caseNumber), labeled("声明机关", authority),
        labeled("案情/事实", caseSummary), labeled("争议焦点（每行一项）", issues),
        labeled("裁判/检察要旨", reasoning), labeled("处理结果", resultText),
        labeled("学习要点（每行一项）", practiceNotes),
      );
      panel._caseEditors = { caseNumber, authority, caseSummary, issues, reasoning, resultText, practiceNotes };
    }
  }
  if (isQuestion && data.question) {
    const questionType = selectControl(QUESTION_TYPES, data.question.question_type || "single_choice");
    const answerSource = selectControl([
      ["not_provided", "未提供"], ["official", "官方答案"],
      ["third_party", "第三方参考答案"], ["user_verified", "用户核验"], ["unverified", "未核验"],
    ], data.question.answer_source || "not_provided");
    const stem = textArea(data.question.stem || "");
    const options = textArea(editableLines(data.question.options));
    const answer = textArea(data.question.answer == null ? "" : JSON.stringify(data.question.answer));
    const explanation = textArea(data.question.explanation || "");
    const examName = textInput(data.question.exam_name || "");
    const examYear = textInput(data.question.exam_year || "");
    const examDate = textInput(data.question.exam_date || "", "date");
    const paper = textInput(data.question.paper || "");
    const questionNumber = textInput(data.question.question_number || "");
    edit.append(
      labeled("题型", questionType), labeled("答案来源身份", answerSource),
      labeled("题干", stem), labeled("选项（每行一项）", options),
      labeled("答案结构（JSON 或文本）", answer), labeled("解析", explanation),
      labeled("考试名称", examName), labeled("考试年份", examYear),
      labeled("考试日期", examDate), labeled("试卷", paper),
      labeled("题号", questionNumber),
    );
    panel._questionEditors = { questionType, answerSource, stem, options, answer, explanation, examName, examYear, examDate, paper, questionNumber };
  }
  edit.append(button(t("page.actions.save", "保存"), async () => {
    try {
      const changes = { title: title.value, subjects: subjects.value };
      if (!isQuestion) {
        changes.body = bodyEditor.value;
        changes.note = noteEditor.value;
        if (panel._caseEditors) {
          const fields = panel._caseEditors;
          Object.assign(changes, {
            case_number: fields.caseNumber.value,
            authority: fields.authority.value,
            case_summary: fields.caseSummary.value,
            issues: fields.issues.value.split(/\r?\n/).map((entry) => entry.trim()).filter(Boolean),
            reasoning: fields.reasoning.value,
            result_text: fields.resultText.value,
            practice_notes: fields.practiceNotes.value.split(/\r?\n/).map((entry) => entry.trim()).filter(Boolean),
          });
        }
      }
      if (panel._questionEditors) {
        const fields = panel._questionEditors;
        Object.assign(changes, {
          question_type: fields.questionType.value,
          answer_source: fields.answerSource.value,
          stem: fields.stem.value,
          options: fields.options.value.split(/\r?\n/).map((entry) => entry.trim()).filter(Boolean),
          answer: parseEditableJson(fields.answer.value),
          explanation: fields.explanation.value,
          exam_name: fields.examName.value,
          exam_year: fields.examYear.value,
          exam_date: fields.examDate.value,
          paper: fields.paper.value,
          question_number: fields.questionNumber.value,
        });
      }
      await apiPost("management/library-update", { item_id: item.id, changes });
      setFlash(t("page.messages.saved", "已保存"));
      await renderLibraryDetail(container, item.id, parentGeneration, parentRoute, true);
    }
    catch (error) { setFlash(error.message, true); }
  }, "primary"));
  panel.append(sectionTitle(t("page.library.editAllowed", "允许修改的字段"), "身份、核验状态、创建者与来源哈希不可从页面修改。"), edit);
  if (item.identity === "real_question_candidate" && isQuestion) {
    panel.append(button("预览核验为真题", async () => {
      try {
        const prepared = await apiPost("management/batch-prepare", {
          action: "promote", item_ids: [item.id], changes: {},
        });
        const preview = node("article", undefined, "confirm-panel");
        preview.append(
          node("h3", "真题身份核验预览"),
          node("p", `可核验 ${prepared.promotable_count || 0} 条；${prepared.unresolved_answer_count || 0} 条答案未提供；${prepared.upsert_count || 0} 条将与已有真题记录核对。`),
          table(prepared.items || [], [["item_id", "候选 ID"], ["eligible", "可核验"], ["already_linked", "已关联"], ["will_upsert", "将更新库存"], ["reason", "说明"], ["normalized", "规范化真题内容"]]),
        );
        const confirm = button("确认核验为真题", async () => {
          confirm.disabled = true;
          try {
            const result = await apiPost("management/batch-confirm", { token: prepared.token });
            preview.remove();
            setFlash(`核验处理完成：${result.results?.filter((entry) => entry.success).length || 0} 条成功`);
            await renderLibraryDetail(container, item.id, parentGeneration, parentRoute, true);
          } catch (error) { confirm.disabled = false; setFlash(error.message, true); }
        }, "primary");
        preview.append(confirm, button(t("page.actions.cancel", "取消"), () => preview.remove()));
        panel.append(preview);
      } catch (error) { setFlash(error.message, true); }
    }, "secondary"));
  }
  if (replace) container.replaceChildren(panel);
  else container.append(panel);
}

async function renderImports(view) {
  const generation = state.renderGeneration;
  const route = state.route;
  const box = node("div", undefined, "form-card");
  box.append(node("h3", t("page.library.upload", "文件导入")), node("p", "文件只会先写入插件数据目录并生成预览，确认后才归档。", "muted"));
  const file = document.createElement("input"); file.type = "file"; file.accept = ".txt,.md,.docx,.pdf";
  const kind = selectControl([["auto", "自动判断"], ["case", "案例"], ["mock_question", "模拟题"], ["real_question_candidate", "真题候选"]]);
  const upload = button(t("page.actions.upload", "上传并预览"), async () => {
    if (!file.files?.[0]) return setFlash("请选择文件", true);
    upload.disabled = true;
    try {
      const staged = await bridge.upload("files/stage", file.files[0]);
      if (!isCurrent(generation, route)) return;
      if (staged == null || staged.success === false) throw new Error(staged?.message || "文件 staging 失败");
      const prepared = await apiPost("imports/prepare", { staged_path: staged.staged_path, original_filename: staged.original_filename, content_kind: kind.value });
      if (!isCurrent(generation, route)) return;
      renderImportPreview(box, prepared);
    } catch (error) { setFlash(error.message, true); } finally { upload.disabled = false; }
  }, "primary");
  const controls = node("div", undefined, "toolbar"); controls.append(file, kind, upload); box.append(controls); view.append(box);
  const structuredBox = node("div", undefined, "form-card");
  structuredBox.append(
    node("h3", "结构化资料导入"),
    node("p", "需要同时提供原始 PDF 与 structured-material-v1.json；确认前不会写入资料库。", "muted")
  );
  const original = document.createElement("input"); original.type = "file"; original.accept = ".pdf";
  const jsonFile = document.createElement("input"); jsonFile.type = "file"; jsonFile.accept = ".json";
  const structuredUpload = button("上传结构化资料并预览", async () => {
    if (!original.files?.[0] || !jsonFile.files?.[0]) return setFlash("请选择原始 PDF 和结构化 JSON", true);
    structuredUpload.disabled = true;
    try {
      const stagedOriginal = await bridge.upload("files/stage", original.files[0]);
      if (stagedOriginal == null || stagedOriginal.success === false) throw new Error(stagedOriginal?.message || "原始文件 staging 失败");
      const stagedJson = await bridge.upload("structured/stage-json", jsonFile.files[0]);
      if (stagedJson == null || stagedJson.success === false) throw new Error(stagedJson?.message || "结构化 JSON staging 失败");
      const prepared = await apiPost("structured/prepare", {
        original_staged_path: stagedOriginal.staged_path,
        structured_staged_path: stagedJson.staged_path,
        original_filename: stagedOriginal.original_filename,
        structured_filename: stagedJson.original_filename,
      });
      if (!isCurrent(generation, route)) return;
      renderStructuredImportPreview(structuredBox, prepared);
    } catch (error) { setFlash(error.message, true); } finally { structuredUpload.disabled = false; }
  }, "primary");
  const structuredControls = node("div", undefined, "toolbar"); structuredControls.append(original, jsonFile, structuredUpload);
  structuredBox.append(structuredControls); view.append(structuredBox);
}

function renderImportPreview(container, prepared) {
  const preview = prepared.preview || {};
  const panel = node("article", undefined, "confirm-panel");
  panel.append(node("h3", "导入预览"), node("p", `候选 ${preview.total_candidates || 0} 条，待复核 ${preview.needs_review || 0} 条，失败 ${preview.failed || 0} 条。`));
  panel.append(table(preview.items || [], [["index", "序号"], ["status", "状态"], ["locator", "定位"], ["reason", "说明"]]));
  panel.append(button("确认归档", async () => { try { await apiPost("imports/confirm", { token: prepared.token }); setFlash("导入完成"); panel.remove(); } catch (error) { setFlash(error.message, true); } }, "primary"), button(t("page.actions.cancel", "取消"), () => panel.remove()));
  container.append(panel);
}

function renderStructuredImportPreview(container, prepared) {
  const preview = prepared.preview || {};
  const counts = preview.counts || {};
  const panel = node("article", undefined, "confirm-panel");
  panel.append(
    node("h3", "结构化资料预览"),
    node("p", `${preview.original_filename || "原始文件"} · SHA-256 ${preview.original_file_sha256 || "—"}`),
    node("p", `schema ${preview.schema_version || "—"}；顶层题目 ${counts.questions || 0}；真实小问 ${counts.subquestions || 0}；答题要求 ${counts.answer_requirements || 0}；答案映射已关联 ${counts.answer_mappings_resolved || 0} / 未解决 ${counts.answer_mappings_unresolved || 0}；公共材料 ${counts.materials || 0}；可归档 ${counts.processable || 0}；重复警告 ${counts.duplicate_warnings || 0}；材料污染警告 ${counts.contamination_warnings || 0}；error ${preview.errors || counts.entry_errors || 0}；review ${preview.review || counts.review_items || 0}`)
  );
  const questions = (preview.questions || []).slice(0, 100);
  const cases = (preview.cases || []).slice(0, 100);
  if (questions.length) panel.append(node("h4", "题目预览"));
  panel.append(table(questions, [
    ["id", "ID"], ["source_number", "原始题号"], ["question_type", "题型"],
    ["title", "标题"], ["stem_block_count", "题干块"], ["subquestion_count", "小问"],
    ["answer_requirement_count", "答题要求"], ["answer_mappings_resolved", "已映射答案"],
    ["answer_mappings_unresolved", "未映射答案"],
    ["answer_status", "答案状态"], ["review_status", "复核状态"]
  ]));
  if (cases.length) {
    panel.append(node("h4", "案例预览"));
    panel.append(table(cases, [
      ["id", "ID"], ["title", "标题"], ["authority", "声明机关"],
      ["case_number", "案号"], ["subjects", "方向", (row) => node("span", (row.subjects || []).map(subjectLabel).join("、"))],
      ["basic_facts", "事实/案情"], ["issues", "争议焦点"],
      ["holding", "裁判/检察要旨"], ["result", "结果"],
      ["learning_points", "学习要点"], ["locators", "定位"],
      ["review_status", "复核状态"],
    ]));
  }
  const confirm = button("确认结构化归档", async () => {
    confirm.disabled = true;
    try { await apiPost("structured/confirm", { token: prepared.token }); setFlash("结构化资料已归档"); panel.remove(); }
    catch (error) { setFlash(error.message, true); confirm.disabled = false; }
  }, "primary");
  panel.append(confirm, button(t("page.actions.cancel", "取消"), () => panel.remove()));
  container.append(panel);
}

async function renderReviews(view) {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "library-reviews";
  const controls = node("div", undefined, "toolbar");
  const status = selectControl([["pending", "pending"], ["resolved", "resolved"], ["superseded", "superseded"]]);
  const batchStatus = selectControl([["resolved", "标记 resolved"], ["superseded", "标记 superseded"], ["pending", "重新设为 pending"]]);
  const selected = new Set();
  let currentRows = [];
  let currentPage = state.pages.reviews?.page || 1;
  const pageSize = () => state.pages.reviews?.page_size || 20;
  const selectedCount = node("span", "已选 0 条", "muted");
  const selectPage = button(t("page.management.selectPage", "选择本页"), async () => {
    const allSelected = currentRows.length > 0 && currentRows.every((row) => selected.has(row.id));
    currentRows.forEach((row) => allSelected ? selected.delete(row.id) : selected.add(row.id));
    await load(currentPage, pageSize());
  });
  const prepareBatch = button("预览批量复核状态", () => prepareModerationBatch(
    "review", [...selected], { status: batchStatus.value }, result,
    () => load(currentPage, pageSize(), true),
  ), "secondary");
  const refresh = button(t("page.actions.refresh", "刷新"), () => load(currentPage, pageSize()), "primary");
  controls.append(status, selectedCount, selectPage, batchStatus, prepareBatch, refresh);
  const result = node("div"); view.append(controls, result);
  async function load(page = 1, size = 20, resetSelection = false) {
    if (resetSelection) selected.clear();
    currentPage = page;
    state.pages.reviews = { page, page_size: size };
    const requestId = beginViewRequest(requestKey);
    result.replaceChildren(node("p", t("page.loading", "正在加载…"), "muted"));
    try {
      const data = await apiGet("reviews", { status: status.value, page, page_size: size });
      if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
      currentRows = data.items || [];
      selectedCount.textContent = `已选 ${selected.size} 条`;
      selectPage.textContent = currentRows.length && currentRows.every((row) => selected.has(row.id)) ? t("page.management.unselectPage", "取消本页选择") : t("page.management.selectPage", "选择本页");
      result.replaceChildren(node("p", `共 ${data.total ?? data.count ?? 0} 条复核记录。`, "muted"));
      result.append(table(currentRows, [
        ["id", "选择", (row) => {
          const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = selected.has(row.id);
          checkbox.addEventListener("change", () => {
            if (checkbox.checked) selected.add(row.id); else selected.delete(row.id);
            selectedCount.textContent = `已选 ${selected.size} 条`;
          });
          return checkbox;
        }],
        ["id", "Review ID"], ["material_type", "资料类型"], ["locator", "定位"],
        ["review_reason", "原因"], ["status", "状态"], ["updated_at", "更新时间"],
      ], (row) => button(t("page.actions.view", "查看"), () => renderReviewDetail(result, row.id, generation, route))));
      result.append(renderPager(data, (nextPage) => load(nextPage, size, true), (nextSize) => load(1, nextSize, true)));
    }
    catch (error) { if (isCurrentRequest(requestKey, requestId, generation, route)) result.replaceChildren(node("p", error.message, "error-text")); }
  }
  status.addEventListener("change", () => load(1, pageSize(), true));
  await load(currentPage, pageSize());
}

async function renderReviewDetail(container, reviewId, parentGeneration = state.renderGeneration, parentRoute = state.route) {
  const requestKey = `review-detail-${reviewId}`;
  const requestId = beginViewRequest(requestKey);
  try {
    const data = await apiGet("review", { id: reviewId }); const item = data.item || {};
    if (!isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) return;
    const panel = node("article", undefined, "detail-panel");
    panel.append(sectionTitle(`Review #${item.id}`, item.review_reason));
    panel.append(keyValueRows({ 资料类型: item.material_type, 定位: item.locator, 来源ID: item.source_id, 状态: item.status, 更新时间: item.updated_at }));
    if (item.answer_safe) {
      panel.append(detailBlock("题目安全预览", item.safe_preview));
      panel.append(node("p", "答案与解析通过 Question Session 显式揭晓，复核页默认不提前展示。", "muted"));
    } else {
      panel.append(detailBlock("原始片段", item.raw_fragment), detailBlock("建议结构", item.proposed_structure));
    }
    if (item.status === "pending") panel.append(button("标记 resolved", () => updateReview(item.id, "resolved"), "primary"), button("标记 superseded", () => updateReview(item.id, "superseded")));
    container.append(panel);
    async function updateReview(id, nextStatus) { try { await apiPost("review/status", { review_id: id, status: nextStatus }); setFlash("复核状态已更新"); await renderLibrary(); } catch (error) { setFlash(error.message, true); } }
  } catch (error) { if (isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) setFlash(error.message, true); }
}

async function renderRadar() {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "radar-events";
  const view = document.getElementById("view-radar");
  view.replaceChildren(sectionTitle(t("page.nav.radar", "活动雷达"), "查看活动证据、时间节点与来源状态。"));
  const controls = node("div", undefined, "toolbar");
  const status = selectControl([["current", "当前"], ["needs_review", "待复核"], ["historical", "历史"], ["ignored", "已忽略"], ["all", "全部"]]);
  const bulkStatus = selectControl([
    ["current", "标记当前"], ["needs_review", "标记待复核"],
    ["historical", "标记历史"], ["ignored", "删除/移除（可恢复）"], ["auto", "清除人工状态"],
  ]);
  const reason = textInput(); reason.placeholder = "批量状态变更原因（清除时可留空）";
  const result = node("div");
  const selected = new Set();
  let currentRows = [];
  let currentPage = state.pages.radar?.page || 1;
  const pageSize = () => state.pages.radar?.page_size || 20;
  const selectedCount = node("span", "已选 0 条", "muted");
  const selectPage = button(t("page.management.selectPage", "选择本页"), async () => {
    const allSelected = currentRows.length > 0 && currentRows.every((row) => selected.has(row.id));
    currentRows.forEach((row) => allSelected ? selected.delete(row.id) : selected.add(row.id));
    await load(currentPage, pageSize());
  });
  const prepareBatch = button("预览批量状态", () => prepareModerationBatch(
    "radar", [...selected], { status: bulkStatus.value, reason: reason.value }, result,
    () => load(currentPage, pageSize(), true),
  ), "secondary");
  const scan = button(t("page.actions.scan", "立即扫描"), async () => { scan.disabled = true; try { const data = await apiPost("radar/scan"); setFlash(`扫描完成：发现 ${data.discovered_count || 0} 条`); await load(); } catch (error) { setFlash(error.message, true); } finally { scan.disabled = false; } }, "primary");
  controls.append(status, selectedCount, selectPage, bulkStatus, reason, prepareBatch, scan); view.append(controls, result);
  async function load(page = 1, size = 20, resetSelection = false) {
    if (resetSelection) selected.clear();
    currentPage = page;
    state.pages.radar = { page, page_size: size };
    const requestId = beginViewRequest(requestKey);
    result.replaceChildren(node("p", t("page.loading", "正在加载…"), "muted"));
    try {
      const data = await apiGet("radar/events", { status: status.value, page, page_size: size });
      if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
      currentRows = data.items || [];
      selectedCount.textContent = `已选 ${selected.size} 条`;
      selectPage.textContent = currentRows.length && currentRows.every((row) => selected.has(row.id)) ? t("page.management.unselectPage", "取消本页选择") : t("page.management.selectPage", "选择本页");
      result.replaceChildren(node("p", `共 ${data.total ?? 0} 条活动。`, "muted"));
      result.append(table(currentRows, [
        ["id", "选择", (row) => {
          const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = selected.has(row.id);
          checkbox.addEventListener("change", () => {
            if (checkbox.checked) selected.add(row.id); else selected.delete(row.id);
            selectedCount.textContent = `已选 ${selected.size} 条`;
          });
          return checkbox;
        }],
        ["id", "ID"], ["title", "标题"], ["event_type", "类型"], ["radar_status", "状态"],
        ["organizer", "主办方"], ["source_url", "来源", (row) => safeLink(row.source_url, "打开来源")],
      ], (row) => button(t("page.actions.view", "查看"), () => renderRadarDetail(result, row.id, generation, route))));
      result.append(renderPager(data, (nextPage) => load(nextPage, size, true), (nextSize) => load(1, nextSize, true)));
    }
    catch (error) { if (isCurrentRequest(requestKey, requestId, generation, route)) result.replaceChildren(node("p", error.message, "error-text")); }
  }
  status.addEventListener("change", () => load(1, pageSize(), true)); await load(currentPage, pageSize());
}

async function renderRadarDetail(container, eventId, parentGeneration = state.renderGeneration, parentRoute = state.route) {
  const requestKey = `radar-detail-${eventId}`;
  const requestId = beginViewRequest(requestKey);
  try {
    const event = await apiGet("radar/event", { id: eventId }); const panel = node("article", undefined, "detail-panel");
    if (!isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) return;
    panel.append(sectionTitle(`活动 #${event.id}`, event.title));
    panel.append(keyValueRows({ 活动类型: event.event_type, 雷达状态: event.radar_status, 主办方: event.organizer, 参赛对象: event.eligibility, 报名方式: event.registration_method, 来源发布时间: event.source_published_at, 修订版: event.revision }));
    panel.append(detailBlock("摘要", event.summary), detailBlock("时间节点与 evidence", (event.dates || []).map((date) => `${date.kind} ${date.datetime || ""} ${date.label}\n${date.evidence_text}`)), detailBlock("来源 URL", safeLink(event.source_url, event.source_url)));
    const statusControls = node("div", undefined, "form-grid");
    const status = selectControl([["current", "当前"], ["needs_review", "待复核"], ["historical", "历史"], ["ignored", "已忽略"], ["derived", "恢复证据推导状态"]], event.status_override?.override_status || "derived");
    const statusReason = textInput(event.status_override?.reason || "");
    statusControls.append(labeled(t("page.radarReview.status", "人工展示状态"), status), labeled(t("page.radarReview.reason", "变更原因"), statusReason));
    statusControls.append(button(t("page.radarReview.previewStatus", "预览状态变更"), async () => {
      try {
        const prepared = await apiPost("radar/status-prepare", { event_id: event.id, status: status.value, reason: statusReason.value });
        const preview = node("article", undefined, "confirm-panel");
        preview.append(node("h3", "活动状态变更预览"), keyValueRows({ 活动: prepared.title, 证据推导状态: prepared.derived_status, 操作后展示状态: prepared.status_after, 原因: prepared.reason }));
        const confirm = button(t("page.radarReview.confirmStatus", "确认状态变更"), async () => {
          confirm.disabled = true;
          try { await apiPost("radar/status-confirm", { token: prepared.token }); preview.remove(); setFlash("活动展示状态已更新"); await renderRadarDetail(container, event.id, parentGeneration, parentRoute); }
          catch (error) { confirm.disabled = false; setFlash(error.message, true); }
        }, "primary");
        preview.append(confirm, button(t("page.actions.cancel", "取消"), () => preview.remove()));
        panel.append(preview);
      } catch (error) { setFlash(error.message, true); }
    }, "secondary"));
    panel.append(sectionTitle(t("page.radarReview.dateSection", "日期证据复核"), t("page.radarReview.dateDescription", "日期确认独立于活动展示状态；只有明确确认的证据才可驱动截止提醒。复核不会改写来源原文。")), statusControls);
    if ((event.dates || []).length) {
      const dateId = selectControl((event.dates || []).map((date) => [String(date.id), String(date.kind) + " · " + String(date.datetime || "未解析")]));
      const selectedDate = () => (event.dates || []).find((date) => String(date.id) === dateId.value);
      const proposedDate = textInput(event.dates[0].datetime ? String(event.dates[0].datetime).replace(/Z$/, "+00:00") : "");
      const reviewReason = textInput();
      const proposedConfirmed = document.createElement("input");
      proposedConfirmed.type = "checkbox";
      proposedConfirmed.checked = Boolean(event.dates[0].confirmed);
      const decision = selectControl([["accepted", t("page.radarReview.accept", "接受建议日期")], ["rejected", t("page.radarReview.reject", "拒绝并保留原值")]]);
      dateId.addEventListener("change", () => {
        const current = selectedDate();
        proposedDate.value = current?.datetime ? String(current.datetime).replace(/Z$/, "+00:00") : "";
        proposedConfirmed.checked = Boolean(current?.confirmed);
      });
      const reviewControls = node("div", undefined, "form-grid");
      reviewControls.append(labeled(t("page.radarReview.dateNode", "日期节点"), dateId), labeled(t("page.radarReview.proposedDate", "建议日期（ISO）"), proposedDate), labeled(t("page.radarReview.reason", "复核原因"), reviewReason), labeled(t("page.radarReview.decision", "决定"), decision), labeled(t("page.radarReview.confirmEvidence", "明确确认此日期证据可用于截止与提醒"), proposedConfirmed, "checkbox-field"));
      reviewControls.append(button(t("page.radarReview.previewDate", "预览日期复核"), async () => {
        const current = selectedDate();
        if (!current) return setFlash("请选择日期节点", true);
        try {
          const prepared = await apiPost("radar/date-review-prepare", {
            event_id: event.id, event_date_id: current.id, proposed_datetime: proposedDate.value,
            reason: reviewReason.value, decision: decision.value, proposed_confirmed: proposedConfirmed.checked,
          });
          const preview = node("article", undefined, "confirm-panel");
          preview.append(node("h3", "日期复核预览"), keyValueRows({ "来源 evidence": current.evidence_text, 当前日期: prepared.old_value.datetime, 建议日期: prepared.proposed_value.datetime, 当前已确认: prepared.old_value.confirmed ? "是" : "否", 操作后已确认: prepared.proposed_value.confirmed ? "是" : "否", 决定: prepared.decision, 原因: prepared.reason }));
          const confirm = button(t("page.radarReview.confirmDate", "确认日期复核"), async () => {
            confirm.disabled = true;
            try { await apiPost("radar/date-review-confirm", { token: prepared.token }); preview.remove(); setFlash("日期复核已记录"); await renderRadarDetail(container, event.id, parentGeneration, parentRoute); }
            catch (error) { confirm.disabled = false; setFlash(error.message, true); }
          }, "primary");
          preview.append(confirm, button(t("page.actions.cancel", "取消"), () => preview.remove()));
          panel.append(preview);
        } catch (error) { setFlash(error.message, true); }
      }, "secondary"));
      panel.append(reviewControls);
    }
    container.append(panel);
  } catch (error) { if (isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) setFlash(error.message, true); }
}

function rotationEditor(label, values, options, firstIndex = 0, firstLabel = "首日方向") {
  const wrap = node("div", undefined, "rotation-editor"); const list = node("div", undefined, "rotation-list"); const current = Array.isArray(values) ? [...values] : [];
  let onChange = () => {};
  let firstValue = current.length ? current[Math.max(0, Math.min(Number(firstIndex) || 0, current.length - 1))] : "";
  const first = selectControl([]);
  const render = () => {
    if (!current.includes(firstValue)) firstValue = current[0] || "";
    first.replaceChildren(...current.map((value) => { const option = node("option", options.find(([key]) => key === value)?.[1] || value); option.value = value; option.selected = value === firstValue; return option; }));
    first.value = firstValue;
    list.replaceChildren(); current.forEach((value, index) => { const row = node("div", undefined, "rotation-row"); row.append(node("span", options.find(([key]) => key === value)?.[1] || value)); row.append(button("↑", () => { if (index) [current[index - 1], current[index]] = [current[index], current[index - 1]]; render(); onChange(); })); row.append(button("↓", () => { if (index < current.length - 1) [current[index + 1], current[index]] = [current[index], current[index + 1]]; render(); onChange(); })); row.append(button(t("page.actions.delete", "删除"), () => { current.splice(index, 1); render(); onChange(); })); list.append(row); });
  };
  first.addEventListener("change", () => { firstValue = first.value; onChange(); });
  const addSelect = selectControl([["", "添加方向"], ...options]); addSelect.addEventListener("change", () => { if (addSelect.value && !current.includes(addSelect.value)) current.push(addSelect.value); addSelect.value = ""; render(); onChange(); });
  wrap.append(node("span", label, "field-label"), labeled(firstLabel, first), list, addSelect); render();
  wrap.getValues = () => [...current];
  wrap.getFirstIndex = () => Math.max(0, current.indexOf(firstValue));
  wrap.setOnChange = (callback) => { onChange = callback; };
  return wrap;
}

function planForm(contentType, plan, onPreview, draftKey = contentType) {
  const savedDraft = state.planDrafts[draftKey] || {};
  const effective = { ...plan, ...savedDraft };
  const form = node("div", undefined, "plan-editor");
  const enabled = document.createElement("input"); enabled.type = "checkbox"; enabled.checked = Boolean(effective.enabled);
  const time = textInput(effective.time || "08:00", "time");
  const mode = selectControl([["random", "随机"], ["fixed", "固定"], ["rotation", "轮换"]], effective.selection_mode || "random");
  const fixedSubject = selectControl([["", "请选择方向"], ...SUBJECTS], effective.fixed_subject || "");
  const startDate = textInput(effective.rotation_start_date || "", "date");
  const subjectRotation = rotationEditor("方向顺序", effective.rotation_subjects || [], SUBJECTS, effective.rotation_start_index || 0, "首日方向");
  const origin = contentType === "daily_question" ? selectControl([["random", "随机来源"], ["real", "真题"], ["mock", "模拟题"]], effective.question_origin || "random") : null;
  const typeMode = contentType === "daily_question" ? selectControl([["random", "随机题型"], ["fixed", "固定题型"], ["rotation", "题型轮换"]], effective.question_type_selection_mode || "random") : null;
  const fixedType = contentType === "daily_question" ? selectControl([["", "请选择题型"], ...QUESTION_TYPES], effective.fixed_question_type || effective.question_type || "") : null;
  const typeStartDate = contentType === "daily_question" ? textInput(effective.question_type_rotation_start_date || "", "date") : null;
  const typeRotation = contentType === "daily_question" ? rotationEditor("题型顺序", effective.rotation_question_types || [], QUESTION_TYPES, effective.question_type_rotation_start_index || 0, "首日题型") : null;
  const revealMode = contentType === "daily_question" ? selectControl([["manual", t("page.plans.manualReveal", "手动揭晓")], ["delayed", t("page.plans.delayedReveal", "定时揭晓")]], effective.question_reveal_mode || "manual") : null;
  const answerDelay = contentType === "daily_question" ? textInput(effective.answer_reveal_delay_minutes || 0, "number") : null;
  const explanationDelay = contentType === "daily_question" ? textInput(effective.explanation_reveal_delay_minutes || 0, "number") : null;
  const remember = () => {
    state.planDrafts[draftKey] = {
      enabled: enabled.checked,
      time: time.value,
      selection_mode: mode.value,
      fixed_subject: fixedSubject.value || null,
      rotation_subjects: subjectRotation.getValues(),
      rotation_start_date: startDate.value || null,
      rotation_start_index: subjectRotation.getFirstIndex(),
      ...(origin ? {
        question_origin: origin.value,
        question_type_selection_mode: typeMode.value,
        fixed_question_type: fixedType.value || null,
        rotation_question_types: typeRotation.getValues(),
        question_type_rotation_start_date: typeStartDate.value || null,
        question_type_rotation_start_index: typeRotation.getFirstIndex(),
        question_reveal_mode: revealMode.value,
        answer_reveal_delay_minutes: Number(answerDelay.value || 0),
        explanation_reveal_delay_minutes: Number(explanationDelay.value || 0),
      } : {}),
    };
  };
  subjectRotation.setOnChange(remember);
  typeRotation?.setOnChange(remember);
  [enabled, time, mode, fixedSubject, startDate, origin, typeMode, fixedType, typeStartDate, revealMode, answerDelay, explanationDelay].filter(Boolean).forEach((control) => {
    control.addEventListener("input", remember);
    control.addEventListener("change", remember);
  });
  const fields = node("div", undefined, "form-grid");
  const fixedSubjectField = labeled("固定方向", fixedSubject);
  const subjectStartDateField = labeled("轮换起始日期", startDate);
  const subjectRotationField = node("div", undefined, "form-field"); subjectRotationField.append(subjectRotation);
  fields.append(labeled("启用", enabled, "checkbox-field"), labeled("发送时间", time), labeled("方向模式", mode), fixedSubjectField, subjectStartDateField, subjectRotationField);
  let fixedTypeField; let typeStartDateField; let typeRotationField; let revealDelayFields; let revealError;
  if (origin) {
    fixedTypeField = labeled("固定题型", fixedType);
    typeStartDateField = labeled("题型轮换起始日期", typeStartDate);
    typeRotationField = node("div", undefined, "form-field");
    typeRotationField.append(typeRotation);
    revealDelayFields = node("div", undefined, "form-grid");
    revealDelayFields.append(labeled(t("page.plans.revealMode", "答案/解析揭晓模式"), revealMode), labeled(t("page.plans.answerDelay", "答案延迟分钟"), answerDelay), labeled(t("page.plans.explanationDelay", "解析延迟分钟"), explanationDelay));
    revealError = node("p", "", "error-text");
    revealError.hidden = true;
    revealDelayFields.append(revealError);
    fields.append(labeled("题目来源", origin), labeled("题型模式", typeMode), fixedTypeField, typeStartDateField, typeRotationField, revealDelayFields);
  }
  const updateDependencies = () => {
    fixedSubjectField.hidden = mode.value !== "fixed";
    subjectStartDateField.hidden = mode.value !== "rotation";
    subjectRotationField.hidden = mode.value !== "rotation";
    if (typeMode) {
      fixedTypeField.hidden = typeMode.value !== "fixed";
      typeStartDateField.hidden = typeMode.value !== "rotation";
      typeRotationField.hidden = typeMode.value !== "rotation";
      revealDelayFields.hidden = revealMode.value !== "delayed";
    }
  };
  mode.addEventListener("change", updateDependencies);
  typeMode?.addEventListener("change", updateDependencies);
  revealMode?.addEventListener("change", () => {
    if (revealMode.value === "delayed") {
      const answer = Number(answerDelay.value || 0);
      if (answer <= 0) answerDelay.value = "15";
      if (Number(explanationDelay.value || 0) < Number(answerDelay.value)) {
        explanationDelay.value = String(Math.min(Number(answerDelay.value) + 15, 10080));
      }
    } else {
      answerDelay.value = "0";
      explanationDelay.value = "0";
    }
    updateDependencies();
    remember();
  });
  updateDependencies();
  const preview = button(t("page.actions.preview", "预览并保存"), () => {
    remember();
    if (revealMode?.value === "delayed") {
      const answerMinutes = Number(answerDelay.value);
      const explanationMinutes = Number(explanationDelay.value);
      if (!Number.isInteger(answerMinutes) || answerMinutes <= 0 || answerMinutes > 10080) {
        revealError.textContent = t("page.plans.answerDelayError", "定时揭晓时，答案延迟必须是 1–10080 分钟。");
        revealError.hidden = false;
        return;
      }
      if (!Number.isInteger(explanationMinutes) || explanationMinutes < answerMinutes || explanationMinutes > 10080) {
        revealError.textContent = t("page.plans.explanationDelayError", "解析延迟不能早于答案揭晓，且不得超过 10080 分钟。");
        revealError.hidden = false;
        return;
      }
      revealError.textContent = "";
      revealError.hidden = true;
    }
    const changes = {
      enabled: enabled.checked,
      time: time.value,
      selection_mode: mode.value,
      fixed_subject: mode.value === "fixed" ? fixedSubject.value || null : null,
      rotation_subjects: mode.value === "rotation" ? subjectRotation.getValues() : [],
      rotation_start_date: mode.value === "rotation" ? startDate.value || null : null,
      rotation_start_index: mode.value === "rotation" ? subjectRotation.getFirstIndex() : 0,
    };
    if (origin) {
      changes.question_origin = origin.value;
      changes.question_type_selection_mode = typeMode.value;
      changes.fixed_question_type = typeMode.value === "fixed" ? fixedType.value || null : null;
      changes.rotation_question_types = typeMode.value === "rotation" ? typeRotation.getValues() : [];
      changes.question_type_rotation_start_date = typeMode.value === "rotation" ? typeStartDate.value || null : null;
      changes.question_type_rotation_start_index = typeMode.value === "rotation" ? typeRotation.getFirstIndex() : 0;
      changes.question_reveal_mode = revealMode.value;
      changes.answer_reveal_delay_minutes = Number(answerDelay.value || 0);
      changes.explanation_reveal_delay_minutes = Number(explanationDelay.value || 0);
    }
    onPreview(changes);
  }, "primary");
  form.append(fields, preview); return form;
}

function planModeLabel(value) {
  return ({ random: "随机方向", fixed: "固定方向", rotation: "方向轮换" })[value] || String(value || "随机方向");
}

function questionTypeModeLabel(value) {
  return ({ random: "随机题型", fixed: "固定题型", rotation: "题型轮换" })[value] || String(value || "随机题型");
}

function originLabel(value) {
  return ({ random: "随机来源", real: "真题", mock: "模拟题" })[value] || String(value || "随机来源");
}

function revealModeLabel(value) {
  return ({ manual: "手动揭晓", delayed: "定时揭晓" })[value] || String(value || "手动揭晓");
}

function contentTypeLabel(value) {
  return ({ daily_case: "每日案例", daily_question: "每日一题" })[value] || "每日任务";
}

function planSummary(plan) {
  const typeMode = plan.question_type_selection_mode || "random";
  const subjects = plan.selection_mode === "fixed"
    ? subjectLabel(plan.fixed_subject)
    : plan.selection_mode === "rotation"
      ? (plan.rotation_subjects || []).map(subjectLabel).join(" → ")
      : "每日随机";
  const questionTypes = typeMode === "fixed"
    ? questionTypeLabel(plan.fixed_question_type || plan.question_type)
    : typeMode === "rotation"
      ? (plan.rotation_question_types || []).map(questionTypeLabel).join(" → ")
      : "每日随机";
  return `${planModeLabel(plan.selection_mode)} / ${plan.enabled ? "启用" : "停用"} / ${plan.time || "08:00"} / 方向：${subjects}${plan.content_type === "daily_question" || plan.question_origin ? ` / 来源：${originLabel(plan.question_origin)} / ${questionTypeModeLabel(typeMode)}：${questionTypes} / 揭晓：${revealModeLabel(plan.question_reveal_mode)}` : ""}`;
}

function renderPlanConfirmation(container, prepared, onDone, confirmEndpoint = "plans/confirm") {
  const panel = node("article", undefined, "confirm-panel"); panel.append(node("h3", "计划修改预览"), node("p", prepared.notice || "确认前不会写入配置。"));
  (prepared.plans || []).forEach((entry) => { panel.append(node("p", `${contentTypeLabel(entry.content_type || entry.plan.content_type)}：${planSummary(entry.plan)}`)); panel.append(table(entry.preview || [], [["date", "日期"], ["subject", "方向", (row) => node("span", subjectLabel(row.subject))], ["question_type", "题型", (row) => node("span", questionTypeLabel(row.question_type))], ["origin", "来源", (row) => node("span", originLabel(row.origin))]])); });
  const confirm = button(t("page.plans.confirmSave", "确认保存"), async () => { confirm.disabled = true; try { await apiPost(confirmEndpoint, { token: prepared.token }); setFlash("计划已保存"); await onDone(); } catch (error) { setFlash(error.message, true); confirm.disabled = false; } });
  panel.append(confirm, button(t("page.actions.cancel", "取消"), () => panel.remove())); container.prepend(panel);
}

function renderPlanResetConfirmation(container, prepared, onDone) {
  const target = prepared.target || {};
  const targetLabel = target.label || "未命名群";
  const contentTypes = (prepared.content_types || []).map(contentTypeLabel).join("、");
  const panel = node("article", undefined, "confirm-panel");
  panel.append(
    node("h3", t("page.plans.restorePreview", "恢复全局计划预览")),
    node("p", prepared.notice || t("page.plans.restoreNotice", "确认前不会写入配置。")),
    keyValueRows({
      [t("page.plans.target", "操作目标")]: `${targetLabel} #${target.id}（${target.unified_msg_origin || "UMO 未提供"}）`,
      [t("page.plans.contentTypes", "内容类型")]: contentTypes,
    }),
  );
  panel.append(node("h3", t("page.plans.current", "当前计划")));
  (prepared.current_plans || []).forEach((entry) => {
    panel.append(node("p", `${contentTypeLabel(entry.content_type)}：${planSummary(entry.plan)}`));
    panel.append(table(entry.preview || [], [["date", "日期"], ["subject", "方向", (row) => node("span", subjectLabel(row.subject))], ["question_type", "题型", (row) => node("span", questionTypeLabel(row.question_type))], ["origin", "来源", (row) => node("span", originLabel(row.origin))]]));
  });
  panel.append(node("h3", t("page.plans.restored", "操作后计划（全局计划）")));
  (prepared.restored_plans || []).forEach((entry) => {
    panel.append(node("p", `${contentTypeLabel(entry.content_type)}：${planSummary(entry.plan)}`));
    panel.append(table(entry.preview || [], [["date", "日期"], ["subject", "方向", (row) => node("span", subjectLabel(row.subject))], ["question_type", "题型", (row) => node("span", questionTypeLabel(row.question_type))], ["origin", "来源", (row) => node("span", originLabel(row.origin))]]));
  });
  const confirm = button(t("page.actions.confirm", "确认应用"), async () => {
    confirm.disabled = true;
    try { await apiPost("plans/reset-confirm", { token: prepared.token }); setFlash(t("page.plans.restoredSuccess", "已恢复全局计划")); await onDone(); }
    catch (error) { setFlash(error.message, true); confirm.disabled = false; }
  }, "primary");
  panel.append(confirm, button(t("page.actions.cancel", "取消"), () => panel.remove()));
  container.prepend(panel);
}

async function renderPlans() {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "plans";
  const requestId = beginViewRequest(requestKey);
  const view = document.getElementById("view-plans"); view.replaceChildren(sectionTitle(t("page.nav.plans", "每日计划"), "全局默认与群级覆盖分别管理；页面不自行计算轮换。"));
  try {
    const data = await apiGet("plans", { days: 14 });
    if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
    const renderPlan = (container, scope, target, contentType, entry, title) => {
      const card = node("article", undefined, "plan-card"); card.append(node("h3", title), node("p", entry.override ? "群级覆盖" : "继承全局"));
      const draftKey = `${scope}:${target || "global"}:${contentType}`;
      card.append(planForm(contentType, entry.plan, async (changes) => { try { const prepared = await apiPost("plans/prepare", { scope, target, content_type: contentType, changes }); if (!isCurrent(generation, route)) return; renderPlanConfirmation(card, prepared, async () => { delete state.planDrafts[draftKey]; await renderPlans(); }); } catch (error) { if (isCurrent(generation, route)) setFlash(error.message, true); } }, draftKey));
      if (scope === "target" && entry.override) card.append(button(t("page.plans.restoreGlobal", "恢复全局计划"), async () => { try { const prepared = await apiPost("plans/reset-prepare", { target, content_type: contentType }); renderPlanResetConfirmation(card, prepared, renderPlans); } catch (error) { setFlash(error.message, true); } }));
      container.append(card);
    };
    const global = node("div"); global.append(sectionTitle("全局默认", "Daily Case 与 Daily Question 独立保存。"));
    renderPlan(global, "global", undefined, "daily_case", data.global.daily_case, "每日案例"); renderPlan(global, "global", undefined, "daily_question", data.global.daily_question, "每日一题"); view.append(global, sectionTitle("已绑定群", "群级配置只影响对应群；未覆盖时继承全局。"));
    const targets = node("div"); (data.targets || []).forEach((targetData) => { const group = node("section", undefined, "target-plan-group"); group.append(node("h3", `${targetData.target.label || "未命名群"} #${targetData.target.id}`)); renderPlan(group, "target", String(targetData.target.id), "daily_case", targetData.plans.daily_case, "每日案例"); renderPlan(group, "target", String(targetData.target.id), "daily_question", targetData.plans.daily_question, "每日一题"); targets.append(group); }); view.append(targets);
  } catch (error) { view.append(node("p", error.message, "error-text")); }
}

async function renderTargets() {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "targets";
  const requestId = beginViewRequest(requestKey);
  const view = document.getElementById("view-targets"); view.replaceChildren(sectionTitle(t("page.nav.targets", "群与发布"), "只显示已明确绑定的目标；页面不会根据群名猜测 UMO。"));
  try {
    const page = state.pages.targets?.page || 1;
    const pageSize = state.pages.targets?.page_size || 20;
    const data = await apiGet("targets", { page, page_size: pageSize });
    if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
    const targets = data.items || [];
    if (!targets.length) {
      view.append(node("p", "暂无绑定群。请在目标 QQ 群中发送：法务 绑定 <别名>。", "muted"));
      if (Number(data.page_count || 0) > 0) {
        view.append(renderPager(data, (nextPage) => {
          state.pages.targets = { page: nextPage, page_size: pageSize };
          renderTargets();
        }, (nextSize) => {
          state.pages.targets = { page: 1, page_size: nextSize };
          renderTargets();
        }));
      }
      return;
    }
    view.append(table(targets, [["id", "ID"], ["label", "别名"], ["unified_msg_origin", "UMO"], ["enabled", "启用"], ["updated_at", "更新时间"]], (row) => {
      const wrap = node("div", undefined, "actions"); const input = textInput(row.label || ""); wrap.append(input, button(t("page.actions.rename", "改名"), async () => { try { await apiPost("target/rename", { target: String(row.id), label: input.value }); setFlash("群别名已更新"); await renderTargets(); } catch (error) { setFlash(error.message, true); } }));
      wrap.append(button(t("page.actions.unbind", "解绑"), async () => { try { const prepared = await apiPost("target/unbind-prepare", { target: String(row.id) }); const panel = node("article", undefined, "confirm-panel"); panel.append(node("h3", "解绑预览"), keyValueRows({ 目标: prepared.target.label, UMO: prepared.target.unified_msg_origin }), button("确认解绑", async () => { await apiPost("target/unbind-confirm", { token: prepared.token }); panel.remove(); setFlash("目标已解绑"); await renderTargets(); }, "primary"), button(t("page.actions.cancel", "取消"), () => panel.remove())); view.prepend(panel); } catch (error) { setFlash(error.message, true); } })); return wrap;
    }));
    view.append(renderPager(data, (nextPage) => { state.pages.targets = { page: nextPage, page_size: pageSize }; renderTargets(); }, (nextSize) => { state.pages.targets = { page: 1, page_size: nextSize }; renderTargets(); }));
  } catch (error) { view.append(node("p", error.message, "error-text")); }
}

const historyConfig = {
  daily: ["history/daily", [["content_date", "本地日期"], ["intended_local_at", "原定本地时间"], ["attempted_at", "实际尝试时间"], ["target_label", "群别名"], ["target_id", "目标 ID"], ["content_type", "类型"], ["resolved_origin", "来源"], ["resolved_subject", "方向", (row) => node("span", subjectLabel(row.resolved_subject))], ["resolved_question_type", "题型", (row) => node("span", questionTypeLabel(row.resolved_question_type))], ["source_kind", "来源类型"], ["source_item_key", "来源条目"], ["status", "状态"], ["error_summary", "原因"]]],
  publications: ["history/publications", [["event_id", "活动"], ["target_id", "目标"], ["kind", "类型"], ["status", "状态"], ["error_summary", "原因"]]],
  reminders: ["history/reminders", [["event_id", "活动"], ["target_id", "目标"], ["reminder_offset", "提前天数"], ["status", "状态"], ["error_summary", "原因"]]],
  sources: ["history/sources", [["source_key", "来源"], ["source_type", "类型"], ["success", "成功"], ["discovered_count", "发现数"], ["error_summary", "原因"]]],
  reveals: ["history/reveals", [["target_label", "群别名"], ["target_umo", "目标会话"], ["session_id", "会话"], ["question_sent_at", "题目发送时间"], ["prompt_index", "小问"], ["reveal_kind", "内容"], ["due_at", "计划时间"], ["attempted_at", "实际尝试时间"], ["finished_at", "完成时间"], ["status", "状态"], ["page_progress", "已发送页"], ["error_summary", "说明"]]],
};

async function renderHistory() {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "history";
  const view = document.getElementById("view-history"); view.replaceChildren(sectionTitle(t("page.nav.history", "运行记录"), "每日内容、活动发布、DDL 提醒和来源运行分别查看有限历史。"));
  view.append(tabBar([["daily", "每日内容"], ["publications", "活动发布"], ["reminders", "DDL 提醒"], ["sources", "来源运行"], ["reveals", "答案/解析定时揭晓"]], state.historyTab, (value) => { state.historyTab = value; renderHistory(); }));
  const result = node("div"); const refresh = button(t("page.actions.refresh", "刷新"), load, "primary"); view.append(refresh, result);
  async function load(page = 1, pageSize = state.pages.history?.page_size || 20) {
    state.pages.history = { page, page_size: pageSize };
    const requestId = beginViewRequest(requestKey); result.replaceChildren(node("p", t("page.loading", "正在加载…"), "muted"));
    try {
      const [endpoint, columns] = historyConfig[state.historyTab];
      const data = await apiGet(endpoint, { page, page_size: pageSize });
      if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
      result.replaceChildren(table(data.items || [], columns));
      result.append(renderPager(data, (nextPage) => load(nextPage, pageSize), (nextSize) => load(1, nextSize)));
    } catch (error) { if (isCurrentRequest(requestKey, requestId, generation, route)) result.replaceChildren(node("p", error.message, "error-text")); }
  }
  await load();
}

async function renderRoute() {
  const generation = ++state.renderGeneration;
  document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== `view-${state.route}`; });
  renderNavigation();
  const renderer = { overview: renderOverview, library: renderLibrary, radar: renderRadar, plans: renderPlans, targets: renderTargets, history: renderHistory }[state.route];
  if (renderer) await renderer(generation);
}

async function navigate(route) {
  const nextRoute = labels[route] ? route : "overview";
  if (nextRoute !== state.route) {
    state.route = nextRoute;
    if (window.location.hash.slice(1) !== state.route) {
      window.location.hash = state.route;
      return;
    }
  }
  await renderRoute();
}

async function init() {
  if (!bridge) { setFlash("AstrBot Plugin Page Bridge 不可用", true); return; }
  await bridge.ready(); state.context = bridge.getContext?.() || {};
  let pageInitialized = false;
  const updateContext = (context) => {
    const previousLocale = state.context.locale;
    const previousTheme = state.context.isDark;
    state.context = context || {};
    document.documentElement.dataset.theme = state.context.isDark ? "dark" : "light";
    document.getElementById("context-badge").textContent = state.context.locale || "Dashboard";
    if (!pageInitialized || previousLocale !== state.context.locale) {
      renderNavigation();
      if (pageInitialized) renderRoute().catch((error) => setFlash(error.message, true));
    } else if (previousTheme !== state.context.isDark) {
      renderNavigation();
    }
  };
  updateContext(state.context); bridge.onContext?.(updateContext);
  const requested = window.location.hash.slice(1); state.route = labels[requested] ? requested : "overview"; await renderRoute();
  pageInitialized = true;
}

window.addEventListener("hashchange", () => {
  const nextRoute = window.location.hash.slice(1);
  state.route = labels[nextRoute] ? nextRoute : "overview";
  renderRoute().catch((error) => setFlash(error.message, true));
});
init().catch((error) => setFlash(error.message, true));

if (globalThis.__LAW_ASSISTANT_TEST__) {
  globalThis.__lawAssistantTest = {
    apiGet,
    apiPost,
    navigate,
    renderOverview,
    renderMaterials,
    renderRealQuestions,
    renderRealQuestionDetail,
    renderLibraryDetail,
    renderDataManagement,
    renderReviewDetail,
    renderReviews,
    renderImports,
    renderStructuredImportPreview,
    renderPlans,
    renderTargets,
    renderHistory,
    renderRadarDetail,
    renderRadar,
    planForm,
    state,
  };
}
