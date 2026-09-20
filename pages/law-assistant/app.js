const bridge = window.AstrBotPluginPage;

const labels = {
  overview: "总览",
  library: "资料库",
  radar: "活动雷达",
  plans: "每日计划",
  targets: "群与发布",
  history: "运行记录",
};

const state = { route: "overview", context: {}, loading: false };

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = String(text);
  return element;
}

function card(title, value, detail) {
  const element = node("article", undefined, "metric-card");
  element.append(node("span", title, "metric-label"));
  element.append(node("strong", value, "metric-value"));
  if (detail) element.append(node("span", detail, "metric-detail"));
  return element;
}

function sectionTitle(title, description) {
  const wrap = node("div", undefined, "section-heading");
  wrap.append(node("h2", title));
  if (description) wrap.append(node("p", description, "muted"));
  return wrap;
}

function setFlash(message, error = false) {
  const flash = document.getElementById("flash");
  flash.textContent = message;
  flash.className = error ? "flash error" : "flash";
  flash.hidden = !message;
}

async function apiGet(endpoint, params = {}) {
  const result = await bridge.apiGet(endpoint, params);
  if (!result || result.success === false) throw new Error(result?.message || "请求失败");
  return result.data;
}

async function apiPost(endpoint, body = {}) {
  const result = await bridge.apiPost(endpoint, body);
  if (!result || result.success === false) throw new Error(result?.message || "请求失败");
  return result.data;
}

function renderNavigation() {
  const navigation = document.getElementById("navigation");
  navigation.replaceChildren();
  Object.entries(labels).forEach(([route, label]) => {
    const button = node("button", label, "nav-button");
    button.type = "button";
    button.dataset.route = route;
    button.classList.toggle("active", route === state.route);
    button.addEventListener("click", () => navigate(route));
    navigation.append(button);
  });
}

function metricGrid(values) {
  const grid = node("div", undefined, "metric-grid");
  values.forEach((value) => grid.append(card(value[0], value[1], value[2])));
  return grid;
}

function table(rows, columns) {
  const wrapper = node("div", undefined, "table-wrap");
  const tableElement = node("table");
  const head = node("tr");
  columns.forEach((column) => head.append(node("th", column[1])));
  const thead = node("thead");
  thead.append(head);
  tableElement.append(thead);
  const body = node("tbody");
  rows.forEach((row) => {
    const tr = node("tr");
    columns.forEach(([key]) => tr.append(node("td", row?.[key] ?? "—")));
    body.append(tr);
  });
  tableElement.append(body);
  wrapper.append(tableElement);
  return wrapper;
}

async function renderOverview() {
  const view = document.getElementById("view-overview");
  view.replaceChildren(sectionTitle("总览", "查看插件、来源、学习资料与最近运行状态。"));
  const loading = node("p", "正在加载…", "muted");
  view.append(loading);
  try {
    const data = await apiGet("overview");
    loading.remove();
    const plugin = data.plugin || {};
    const radar = data.radar || {};
    const learning = data.learning || {};
    view.append(metricGrid([
      ["插件版本", plugin.version || "0.3.0", `schema v${data.schema_version ?? "—"}`],
      ["当前活动", radar.current || 0, "排除历史与待复核"],
      ["官方案例", learning.official_case || 0, "可用于每日案例"],
      ["核验真题", learning.verified_real || 0, "来源身份独立保存"],
      ["绑定群", data.targets?.count || 0, "启用发布目标"],
    ]));
    view.append(sectionTitle("最近每日运行", "失败与跳过记录会保留原因，不会伪装成成功。"));
    view.append(table((data.recent?.daily || []).slice(0, 8), [
      ["content_date", "日期"], ["content_type", "内容类型"], ["status", "状态"],
      ["resolved_subject", "方向"], ["error_summary", "说明"],
    ]));
  } catch (error) {
    loading.textContent = error.message;
    setFlash(error.message, true);
  }
}

async function renderLibrary() {
  const view = document.getElementById("view-library");
  view.replaceChildren(sectionTitle("资料库", "搜索资料、查看证据与通过受控流程导入文件。"));
  const controls = node("div", undefined, "toolbar");
  const query = document.createElement("input");
  query.placeholder = "关键词";
  const type = document.createElement("select");
  [["", "全部类型"], ["case", "案例"], ["question", "题目"], ["mock_question", "模拟题"], ["real_question_candidate", "真题候选"], ["note", "笔记"]].forEach(([value, label]) => {
    const option = node("option", label); option.value = value; type.append(option);
  });
  const search = node("button", "搜索", "primary");
  search.type = "button";
  controls.append(query, type, search);
  const result = node("div");
  const runSearch = async () => {
    result.replaceChildren(node("p", "正在搜索…", "muted"));
    try {
      const data = await apiGet("library/search", { query: query.value, type: type.value, limit: 50 });
      result.replaceChildren(node("p", `找到 ${data.count || 0} 条资料。`, "muted"));
      result.append(table(data.items || [], [["id", "ID"], ["identity", "身份"], ["title", "标题"], ["verification_status", "核验"], ["updated_at", "更新时间"]]));
    } catch (error) { result.replaceChildren(node("p", error.message, "error-text")); }
  };
  search.addEventListener("click", runSearch);
  const uploadBox = node("div", undefined, "toolbar");
  const file = document.createElement("input");
  file.type = "file";
  file.accept = ".txt,.md,.docx,.pdf";
  const importKind = document.createElement("select");
  [["auto", "自动判断"], ["case", "案例"], ["mock_question", "模拟题"], ["real_question_candidate", "真题候选"]].forEach(([value, label]) => { const option = node("option", label); option.value = value; importKind.append(option); });
  const upload = node("button", "上传并预览", "primary"); upload.type = "button";
  upload.addEventListener("click", async () => {
    if (!file.files?.[0]) { setFlash("请选择文件", true); return; }
    upload.disabled = true;
    try {
      const staged = await bridge.upload("files/stage", file.files[0]);
      if (!staged?.success) throw new Error(staged?.message || "文件 staging 失败");
      const prepared = await apiPost("imports/prepare", { staged_path: staged.data.staged_path, original_filename: staged.data.original_filename, content_kind: importKind.value });
      const preview = prepared.preview || {};
      const message = `已生成导入预览：${preview.total_candidates || 0} 条候选，${preview.needs_review || 0} 条待复核。确认归档？`;
      if (window.confirm(message)) {
        await apiPost("imports/confirm", { token: prepared.token });
        setFlash("导入完成");
        await runSearch();
      } else setFlash("已保留预览，未写入资料库");
    } catch (error) { setFlash(error.message, true); } finally { upload.disabled = false; }
  });
  uploadBox.append(file, importKind, upload);
  view.append(controls, uploadBox, result);
  await runSearch();
}

async function renderRadar() {
  const view = document.getElementById("view-radar");
  view.replaceChildren(sectionTitle("活动雷达", "来源发现与自动发布策略在后端独立控制。"));
  const controls = node("div", undefined, "toolbar");
  const status = document.createElement("select");
  [["current", "当前"], ["needs_review", "待复核"], ["historical", "历史"], ["all", "全部"]].forEach(([value, label]) => { const o = node("option", label); o.value = value; status.append(o); });
  const scan = node("button", "立即扫描", "primary"); scan.type = "button";
  controls.append(status, scan);
  const result = node("div");
  const load = async () => {
    result.replaceChildren(node("p", "正在加载…", "muted"));
    try {
      const data = await apiGet("radar/events", { status: status.value, limit: 50 });
      result.replaceChildren(table(data || [], [["id", "ID"], ["title", "标题"], ["event_type", "类型"], ["radar_status", "状态"], ["organizer", "主办方"], ["source_url", "来源"]]));
    } catch (error) { result.replaceChildren(node("p", error.message, "error-text")); }
  };
  status.addEventListener("change", load);
  scan.addEventListener("click", async () => { scan.disabled = true; try { const data = await apiPost("radar/scan"); setFlash(`扫描完成：发现 ${data.discovered_count || 0} 条`); await load(); } catch (error) { setFlash(error.message, true); } finally { scan.disabled = false; } });
  view.append(controls, result);
  await load();
}

async function renderPlans() {
  const view = document.getElementById("view-plans");
  view.replaceChildren(sectionTitle("每日计划", "全局默认与群级覆盖均由后端计算未来安排；页面不自行计算轮换。"));
  const result = node("div");
  view.append(result);
  try {
    const data = await apiGet("plans", { days: 14 });
    result.append(sectionTitle("全局默认", "计划变更必须 prepare 后明确确认。"));
    Object.entries(data.global || {}).forEach(([kind, value]) => {
      const article = node("article", undefined, "plan-card");
      article.append(node("h3", kind === "daily_case" ? "每日案例" : "每日一题"));
      article.append(node("p", `模式：${value.plan.selection_mode || "random"}；启用：${value.plan.enabled ? "是" : "否"}`));
      article.append(table((value.preview || []).slice(0, 14), [["date", "日期"], ["subject", "方向"], ["question_type", "题型"], ["origin", "来源"]]));
      result.append(article);
    });
  } catch (error) { result.append(node("p", error.message, "error-text")); }
}

async function renderTargets() {
  const view = document.getElementById("view-targets");
  view.replaceChildren(sectionTitle("群与发布", "只显示已明确绑定的目标；页面不会根据群名猜测 UMO。"));
  try {
    const targets = await apiGet("targets");
    view.append(table(targets || [], [["id", "ID"], ["label", "别名"], ["unified_msg_origin", "UMO"], ["enabled", "启用"], ["updated_at", "更新时间"]]));
  } catch (error) { view.append(node("p", error.message, "error-text")); }
}

async function renderHistory() {
  const view = document.getElementById("view-history");
  view.replaceChildren(sectionTitle("运行记录", "按内容、发布、提醒和来源运行分别查看有限历史。"));
  try {
    const data = await apiGet("history/daily", { limit: 50 });
    view.append(table(data || [], [["content_date", "日期"], ["target_id", "目标"], ["content_type", "类型"], ["status", "状态"], ["error_summary", "原因"]]));
  } catch (error) { view.append(node("p", error.message, "error-text")); }
}

async function renderRoute() {
  document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== `view-${state.route}`; });
  renderNavigation();
  const renderer = { overview: renderOverview, library: renderLibrary, radar: renderRadar, plans: renderPlans, targets: renderTargets, history: renderHistory }[state.route];
  if (renderer) await renderer();
}

async function navigate(route) {
  state.route = labels[route] ? route : "overview";
  window.location.hash = state.route;
  await renderRoute();
}

async function init() {
  if (!bridge) { setFlash("AstrBot Plugin Page Bridge 不可用", true); return; }
  await bridge.ready();
  state.context = bridge.getContext?.() || {};
  bridge.onContext?.((context) => {
    state.context = context || {};
    const dark = Boolean(state.context.isDark);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.getElementById("context-badge").textContent = state.context.locale || "Dashboard";
  });
  const requested = window.location.hash.slice(1);
  state.route = labels[requested] ? requested : "overview";
  await renderRoute();
}

window.addEventListener("hashchange", () => navigate(window.location.hash.slice(1)));
init().catch((error) => setFlash(error.message, true));
