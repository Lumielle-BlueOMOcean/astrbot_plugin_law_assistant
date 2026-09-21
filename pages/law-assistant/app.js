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
      cell.append(actions(row));
      tr.append(cell);
    }
    body.append(tr);
  });
  tableElement.append(body);
  wrapper.append(tableElement);
  return wrapper;
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
      [t("page.overview.version", "插件版本"), plugin.version || "0.3.0", `schema v${data.schema_version ?? "—"}`],
      [t("page.overview.currentEvents", "当前活动"), radar.current || 0, t("page.overview.currentEventsDetail", "排除历史与待复核")],
      [t("page.overview.cases", "官方案例"), learning.official_case || 0, t("page.overview.casesDetail", "可用于每日案例")],
      [t("page.overview.realQuestions", "核验真题"), learning.verified_real || 0, t("page.overview.realQuestionsDetail", "来源身份独立保存")],
      [t("page.overview.targets", "绑定群"), data.targets?.count || 0, t("page.overview.targetsDetail", "启用发布目标")],
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
    ["imports", t("page.library.imports", "文件导入")],
    ["reviews", t("page.library.reviews", "待复核")],
  ], state.libraryTab, (value) => { state.libraryTab = value; renderLibrary(); }));
  if (!isCurrent(generation, route)) return;
  if (state.libraryTab === "imports") return renderImports(view);
  if (state.libraryTab === "reviews") return renderReviews(view);
  return renderMaterials(view);
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
  const search = button(t("page.actions.search", "搜索"), runSearch, "primary");
  controls.append(query, type, subject, search);
  const result = node("div");
  view.append(controls, result);

  async function runSearch() {
    const requestId = beginViewRequest(requestKey);
    result.replaceChildren(node("p", t("page.loading", "正在加载…"), "muted"));
    try {
      const data = await apiGet("library/search", { query: query.value, type: type.value, subject: subject.value, limit: 50 });
      if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
      const rows = data.items || [];
      result.replaceChildren(node("p", `找到 ${data.count || 0} 条资料。`, "muted"));
      result.append(table(rows, [
        ["id", "ID"], ["identity", "身份"], ["title", "标题"],
        ["subjects", "方向", (row) => node("span", (row.subjects || []).map(subjectLabel).join("、"))],
        ["verification_status", "核验"], ["updated_at", "更新时间"],
      ], (row) => button(t("page.actions.view", "查看"), () => renderLibraryDetail(result, row.id))));
      if (state.libraryItemId) await renderLibraryDetail(result, state.libraryItemId, generation, route);
    } catch (error) {
      if (isCurrentRequest(requestKey, requestId, generation, route)) result.replaceChildren(node("p", error.message, "error-text"));
    }
  }
  await runSearch();
}

async function renderLibraryDetail(container, itemId, parentGeneration = state.renderGeneration, parentRoute = state.route) {
  state.libraryItemId = itemId;
  const requestKey = `library-detail-${itemId}`;
  const requestId = beginViewRequest(requestKey);
  let data;
  try { data = await apiGet("library/item", { id: itemId }); }
  catch (error) { if (isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) setFlash(error.message, true); return; }
  if (!isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) return;
  const item = data.item || {};
  const panel = node("article", undefined, "detail-panel");
  panel.append(sectionTitle(`${t("page.library.detail", "资料详情")} #${item.id}`, item.title));
  panel.append(keyValueRows({ 身份: item.identity, 核验: item.verification_status, 方向: (item.subjects || []).map(subjectLabel), 创建者: item.created_by, 更新时间: item.updated_at }));
  if (data.case) panel.append(detailBlock("案例结构化整理", keyValueRows({ 案号: data.case.case_number, 权威机关: data.case.authority, 案情: data.case.case_summary, 争议焦点: data.case.issues, 裁判或检察要旨: data.case.reasoning, 结果: data.case.result, 学习要点: data.case.practice_notes })));
  if (data.question) panel.append(detailBlock("题目结构化整理", keyValueRows({ 题型: questionTypeLabel(data.question.question_type), 题干: data.question.stem, 选项: data.question.options, 答案: data.question.answer, 解析: data.question.explanation, 考试: data.question.exam_name, 年份: data.question.exam_year, 试卷: data.question.paper, 题号: data.question.question_number, 答案来源: data.question.answer_source })));
  if (item.metadata?.body) panel.append(detailBlock("笔记正文", item.metadata.body));
  if (item.metadata?.note) panel.append(detailBlock("人工备注", item.metadata.note));
  const evidence = node("div");
  (data.sources || []).forEach((source) => {
    const sourceCard = node("div", undefined, "source-card");
    sourceCard.append(keyValueRows({ 来源标题: source.title, 来源哈希: source.content_hash, 原文件: source.original_filename, 创建者: source.created_by }));
    if (source.source_url) sourceCard.append(safeLink(source.source_url));
    sourceCard.append(node("pre", source.raw_text, "evidence")); evidence.append(sourceCard);
  });
  panel.append(detailBlock("原始 evidence", evidence));
  const edit = node("div", undefined, "form-grid");
  const title = textInput(item.title);
  const subjects = textInput((item.subjects || []).join(","));
  const note = textInput(item.metadata?.note || "");
  edit.append(labeled("标题", title), labeled("方向（可用规范名称或中文）", subjects), labeled("备注", note));
  edit.append(button(t("page.actions.save", "保存"), async () => {
    try { await apiPost("library/update", { item_id: item.id, changes: { title: title.value, subjects: subjects.value, note: note.value } }); setFlash(t("page.messages.saved", "已保存")); await renderLibraryDetail(container, item.id); }
    catch (error) { setFlash(error.message, true); }
  }, "primary"));
  panel.append(sectionTitle(t("page.library.editAllowed", "允许修改的字段"), "身份、核验状态、创建者与来源哈希不可从页面修改。"), edit);
  container.append(panel);
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
}

function renderImportPreview(container, prepared) {
  const preview = prepared.preview || {};
  const panel = node("article", undefined, "confirm-panel");
  panel.append(node("h3", "导入预览"), node("p", `候选 ${preview.total_candidates || 0} 条，待复核 ${preview.needs_review || 0} 条，失败 ${preview.failed || 0} 条。`));
  panel.append(table(preview.items || [], [["index", "序号"], ["status", "状态"], ["locator", "定位"], ["reason", "说明"]]));
  panel.append(button("确认归档", async () => { try { await apiPost("imports/confirm", { token: prepared.token }); setFlash("导入完成"); panel.remove(); } catch (error) { setFlash(error.message, true); } }, "primary"), button(t("page.actions.cancel", "取消"), () => panel.remove()));
  container.append(panel);
}

async function renderReviews(view) {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "library-reviews";
  const controls = node("div", undefined, "toolbar");
  const status = selectControl([["pending", "pending"], ["resolved", "resolved"], ["superseded", "superseded"]]);
  const refresh = button(t("page.actions.refresh", "刷新"), load, "primary"); controls.append(status, refresh);
  const result = node("div"); view.append(controls, result);
  async function load() {
    const requestId = beginViewRequest(requestKey);
    result.replaceChildren(node("p", t("page.loading", "正在加载…"), "muted"));
    try { const data = await apiGet("reviews", { status: status.value, limit: 50 }); if (!isCurrentRequest(requestKey, requestId, generation, route)) return; result.replaceChildren(table(data.items || [], [["id", "Review ID"], ["material_type", "资料类型"], ["locator", "定位"], ["review_reason", "原因"], ["status", "状态"], ["updated_at", "更新时间"]], (row) => button(t("page.actions.view", "查看"), () => renderReviewDetail(result, row.id, generation, route)))); }
    catch (error) { if (isCurrentRequest(requestKey, requestId, generation, route)) result.replaceChildren(node("p", error.message, "error-text")); }
  }
  await load();
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
    panel.append(detailBlock("原始片段", item.raw_fragment), detailBlock("建议结构", item.proposed_structure));
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
  const status = selectControl([["current", "当前"], ["needs_review", "待复核"], ["historical", "历史"], ["all", "全部"]]);
  const result = node("div");
  const scan = button(t("page.actions.scan", "立即扫描"), async () => { scan.disabled = true; try { const data = await apiPost("radar/scan"); setFlash(`扫描完成：发现 ${data.discovered_count || 0} 条`); await load(); } catch (error) { setFlash(error.message, true); } finally { scan.disabled = false; } }, "primary");
  controls.append(status, scan); view.append(controls, result);
  async function load() {
    const requestId = beginViewRequest(requestKey);
    result.replaceChildren(node("p", t("page.loading", "正在加载…"), "muted"));
    try { const rows = await apiGet("radar/events", { status: status.value, limit: 50 }); if (!isCurrentRequest(requestKey, requestId, generation, route)) return; result.replaceChildren(table(rows || [], [["id", "ID"], ["title", "标题"], ["event_type", "类型"], ["radar_status", "状态"], ["organizer", "主办方"], ["source_url", "来源", (row) => safeLink(row.source_url, "打开来源")]], (row) => button(t("page.actions.view", "查看"), () => renderRadarDetail(result, row.id, generation, route)))); }
    catch (error) { if (isCurrentRequest(requestKey, requestId, generation, route)) result.replaceChildren(node("p", error.message, "error-text")); }
  }
  status.addEventListener("change", load); await load();
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
    container.append(panel);
  } catch (error) { if (isCurrentRequest(requestKey, requestId, parentGeneration, parentRoute)) setFlash(error.message, true); }
}

function rotationEditor(label, values, options) {
  const wrap = node("div", undefined, "rotation-editor"); const list = node("div", undefined, "rotation-list"); const current = Array.isArray(values) ? [...values] : [];
  const render = () => { list.replaceChildren(); current.forEach((value, index) => { const row = node("div", undefined, "rotation-row"); row.append(node("span", options.find(([key]) => key === value)?.[1] || value)); row.append(button("↑", () => { if (index) [current[index - 1], current[index]] = [current[index], current[index - 1]]; render(); })); row.append(button("↓", () => { if (index < current.length - 1) [current[index + 1], current[index]] = [current[index], current[index + 1]]; render(); })); row.append(button(t("page.actions.delete", "删除"), () => { current.splice(index, 1); render(); })); list.append(row); }); };
  const addSelect = selectControl([["", "添加方向"], ...options]); addSelect.addEventListener("change", () => { if (addSelect.value && !current.includes(addSelect.value)) current.push(addSelect.value); addSelect.value = ""; render(); });
  wrap.append(node("span", label, "field-label"), list, addSelect); render(); wrap.getValues = () => [...current]; return wrap;
}

function planForm(contentType, plan, onPreview, draftKey = contentType) {
  const savedDraft = state.planDrafts[draftKey] || {};
  const effective = { ...plan, ...savedDraft };
  const form = node("div", undefined, "plan-editor");
  const enabled = document.createElement("input"); enabled.type = "checkbox"; enabled.checked = Boolean(effective.enabled);
  const time = textInput(effective.time || "08:00", "time");
  const mode = selectControl([["random", "随机"], ["fixed", "固定"], ["rotation", "轮换"]], effective.selection_mode || "random");
  const fixedSubject = selectControl([["", "请选择方向"], ...SUBJECTS], effective.fixed_subject || "");
  const startDate = textInput(effective.rotation_start_date || "", "date"); const startIndex = textInput(effective.rotation_start_index || 0, "number");
  const subjectRotation = rotationEditor("方向顺序", effective.rotation_subjects || [], SUBJECTS);
  const origin = contentType === "daily_question" ? selectControl([["random", "随机来源"], ["real", "真题"], ["mock", "模拟题"]], effective.question_origin || "random") : null;
  const typeMode = contentType === "daily_question" ? selectControl([["random", "随机题型"], ["fixed", "固定题型"], ["rotation", "题型轮换"]], effective.question_type_selection_mode || "random") : null;
  const fixedType = contentType === "daily_question" ? selectControl([["", "请选择题型"], ...QUESTION_TYPES], effective.fixed_question_type || effective.question_type || "") : null;
  const typeStartDate = contentType === "daily_question" ? textInput(effective.question_type_rotation_start_date || "", "date") : null;
  const typeStartIndex = contentType === "daily_question" ? textInput(effective.question_type_rotation_start_index || 0, "number") : null;
  const typeRotation = contentType === "daily_question" ? rotationEditor("题型顺序", effective.rotation_question_types || [], QUESTION_TYPES) : null;
  const remember = () => {
    state.planDrafts[draftKey] = {
      enabled: enabled.checked,
      time: time.value,
      selection_mode: mode.value,
      fixed_subject: fixedSubject.value || null,
      rotation_subjects: subjectRotation.getValues(),
      rotation_start_date: startDate.value || null,
      rotation_start_index: Number(startIndex.value || 0),
      ...(origin ? {
        question_origin: origin.value,
        question_type_selection_mode: typeMode.value,
        fixed_question_type: fixedType.value || null,
        rotation_question_types: typeRotation.getValues(),
        question_type_rotation_start_date: typeStartDate.value || null,
        question_type_rotation_start_index: Number(typeStartIndex.value || 0),
      } : {}),
    };
  };
  [enabled, time, mode, fixedSubject, startDate, startIndex, origin, typeMode, fixedType, typeStartDate, typeStartIndex].filter(Boolean).forEach((control) => {
    control.addEventListener("input", remember);
    control.addEventListener("change", remember);
  });
  const fields = node("div", undefined, "form-grid");
  fields.append(labeled("启用", enabled, "checkbox-field"), labeled("发送时间", time), labeled("方向模式", mode), labeled("固定方向", fixedSubject), labeled("轮换起始日期", startDate), labeled("起始位置", startIndex), subjectRotation);
  if (origin) fields.append(labeled("题目来源", origin), labeled("题型模式", typeMode), labeled("固定题型", fixedType), labeled("题型起始日期", typeStartDate), labeled("题型起始位置", typeStartIndex), typeRotation);
  const preview = button(t("page.actions.preview", "预览修改"), () => {
    remember();
    const changes = { enabled: enabled.checked, time: time.value, selection_mode: mode.value, fixed_subject: fixedSubject.value || null, rotation_subjects: subjectRotation.getValues(), rotation_start_date: startDate.value || null, rotation_start_index: Number(startIndex.value || 0) };
    if (origin) Object.assign(changes, { question_origin: origin.value, question_type_selection_mode: typeMode.value, fixed_question_type: fixedType.value || null, rotation_question_types: typeRotation.getValues(), question_type_rotation_start_date: typeStartDate.value || null, question_type_rotation_start_index: Number(typeStartIndex.value || 0) });
    onPreview(changes);
  }, "primary");
  form.append(fields, preview); return form;
}

function planSummary(plan) { return `${plan.selection_mode || "random"} / ${plan.enabled ? "启用" : "停用"} / ${plan.time || "08:00"}`; }

function renderPlanConfirmation(container, prepared, onDone, confirmEndpoint = "plans/confirm") {
  const panel = node("article", undefined, "confirm-panel"); panel.append(node("h3", "计划修改预览"), node("p", prepared.notice || "确认前不会写入配置。"));
  (prepared.plans || []).forEach((entry) => { panel.append(node("p", `计划：${planSummary(entry.plan)}`)); panel.append(table(entry.preview || [], [["date", "日期"], ["subject", "方向", (row) => node("span", subjectLabel(row.subject))], ["question_type", "题型", (row) => node("span", questionTypeLabel(row.question_type))], ["origin", "来源"]])); });
  const confirm = button(t("page.actions.confirm", "确认应用"), async () => { confirm.disabled = true; try { await apiPost(confirmEndpoint, { token: prepared.token }); setFlash("计划已应用"); await onDone(); } catch (error) { setFlash(error.message, true); confirm.disabled = false; } });
  panel.append(confirm, button(t("page.actions.cancel", "取消"), () => panel.remove())); container.prepend(panel);
}

function renderPlanResetConfirmation(container, prepared, onDone) {
  const target = prepared.target || {};
  const targetLabel = target.label || "未命名群";
  const contentTypes = (prepared.content_types || []).join("、");
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
    panel.append(node("p", `${entry.content_type}：${planSummary(entry.plan)}`));
    panel.append(table(entry.preview || [], [["date", "日期"], ["subject", "方向", (row) => node("span", subjectLabel(row.subject))], ["question_type", "题型"], ["origin", "来源"]]));
  });
  panel.append(node("h3", t("page.plans.restored", "操作后计划（全局计划）")));
  (prepared.restored_plans || []).forEach((entry) => {
    panel.append(node("p", `${entry.content_type}：${planSummary(entry.plan)}`));
    panel.append(table(entry.preview || [], [["date", "日期"], ["subject", "方向", (row) => node("span", subjectLabel(row.subject))], ["question_type", "题型"], ["origin", "来源"]]));
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
      card.append(planForm(contentType, entry.plan, async (changes) => { try { const prepared = await apiPost("plans/prepare", { scope, target, content_type: contentType, changes }); if (!isCurrent(generation, route)) return; delete state.planDrafts[draftKey]; renderPlanConfirmation(card, prepared, renderPlans); } catch (error) { if (isCurrent(generation, route)) setFlash(error.message, true); } }, draftKey));
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
    const targets = await apiGet("targets");
    if (!isCurrentRequest(requestKey, requestId, generation, route)) return;
    if (!targets.length) { view.append(node("p", "暂无绑定群。请在目标 QQ 群中执行 /law bind <别名>。", "muted")); return; }
    view.append(table(targets, [["id", "ID"], ["label", "别名"], ["unified_msg_origin", "UMO"], ["enabled", "启用"], ["updated_at", "更新时间"]], (row) => {
      const wrap = node("div", undefined, "actions"); const input = textInput(row.label || ""); wrap.append(input, button(t("page.actions.rename", "改名"), async () => { try { await apiPost("target/rename", { target: String(row.id), label: input.value }); setFlash("群别名已更新"); await renderTargets(); } catch (error) { setFlash(error.message, true); } }));
      wrap.append(button(t("page.actions.unbind", "解绑"), async () => { try { const prepared = await apiPost("target/unbind-prepare", { target: String(row.id) }); const panel = node("article", undefined, "confirm-panel"); panel.append(node("h3", "解绑预览"), keyValueRows({ 目标: prepared.target.label, UMO: prepared.target.unified_msg_origin }), button("确认解绑", async () => { await apiPost("target/unbind-confirm", { token: prepared.token }); panel.remove(); setFlash("目标已解绑"); await renderTargets(); }, "primary"), button(t("page.actions.cancel", "取消"), () => panel.remove())); view.prepend(panel); } catch (error) { setFlash(error.message, true); } })); return wrap;
    }));
  } catch (error) { view.append(node("p", error.message, "error-text")); }
}

const historyConfig = {
  daily: ["history/daily", [["content_date", "日期"], ["target_id", "目标"], ["content_type", "类型"], ["status", "状态"], ["resolved_subject", "方向", (row) => node("span", subjectLabel(row.resolved_subject))], ["error_summary", "原因"]]],
  publications: ["history/publications", [["event_id", "活动"], ["target_id", "目标"], ["kind", "类型"], ["status", "状态"], ["error_summary", "原因"]]],
  reminders: ["history/reminders", [["event_id", "活动"], ["target_id", "目标"], ["reminder_offset", "提前天数"], ["status", "状态"], ["error_summary", "原因"]]],
  sources: ["history/sources", [["source_key", "来源"], ["source_type", "类型"], ["success", "成功"], ["discovered_count", "发现数"], ["error_summary", "原因"]]],
};

async function renderHistory() {
  const generation = state.renderGeneration;
  const route = state.route;
  const requestKey = "history";
  const view = document.getElementById("view-history"); view.replaceChildren(sectionTitle(t("page.nav.history", "运行记录"), "每日内容、活动发布、DDL 提醒和来源运行分别查看有限历史。"));
  view.append(tabBar([["daily", "每日内容"], ["publications", "活动发布"], ["reminders", "DDL 提醒"], ["sources", "来源运行"]], state.historyTab, (value) => { state.historyTab = value; renderHistory(); }));
  const result = node("div"); const refresh = button(t("page.actions.refresh", "刷新"), load, "primary"); view.append(refresh, result);
  async function load() { const requestId = beginViewRequest(requestKey); result.replaceChildren(node("p", t("page.loading", "正在加载…"), "muted")); try { const [endpoint, columns] = historyConfig[state.historyTab]; const data = await apiGet(endpoint, { limit: 50 }); if (!isCurrentRequest(requestKey, requestId, generation, route)) return; result.replaceChildren(table(data, columns)); } catch (error) { if (isCurrentRequest(requestKey, requestId, generation, route)) result.replaceChildren(node("p", error.message, "error-text")); } }
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
    renderImports,
    renderPlans,
    renderTargets,
    renderHistory,
    state,
  };
}
