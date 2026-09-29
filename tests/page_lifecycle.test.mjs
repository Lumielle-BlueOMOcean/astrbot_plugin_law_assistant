import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import vm from "node:vm";

class FakeElement {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.hidden = false;
    this.value = "";
    this.files = [];
    this.listeners = {};
    this.parentElement = null;
    this.classList = { toggle() {} };
  }

  append(...children) {
    const values = children.flat().filter(Boolean);
    values.forEach((child) => { child.parentElement = this; });
    this.children.push(...values);
  }
  prepend(...children) {
    const values = children.flat().filter(Boolean);
    values.forEach((child) => { child.parentElement = this; });
    this.children.unshift(...values);
  }
  replaceChildren(...children) {
    this.children.forEach((child) => { child.parentElement = null; });
    this.children = children.flat().filter(Boolean);
    this.children.forEach((child) => { child.parentElement = this; });
  }
  after(...siblings) {
    if (!this.parentElement) return;
    const parent = this.parentElement;
    const index = parent.children.indexOf(this);
    const values = siblings.flat().filter(Boolean);
    values.forEach((child) => { child.parentElement = parent; });
    parent.children.splice(index + 1, 0, ...values);
  }
  remove() {
    if (this.parentElement) {
      const siblings = this.parentElement.children;
      const index = siblings.indexOf(this);
      if (index >= 0) siblings.splice(index, 1);
      this.parentElement = null;
    }
    this.removed = true;
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute() {}
}

function textOf(value) {
  if (!value) return "";
  return `${value.textContent || ""}${(value.children || []).map(textOf).join("")}`;
}

function findElement(root, predicate) {
  if (predicate(root)) return root;
  for (const child of root.children || []) {
    const found = findElement(child, predicate);
    if (found) return found;
  }
  return null;
}

function findElements(root, predicate, found = []) {
  if (predicate(root)) found.push(root);
  for (const child of root.children || []) findElements(child, predicate, found);
  return found;
}

function labeledInput(root, label) {
  const field = findElement(
    root,
    (element) => element.tagName === "LABEL" && element.children?.[0]?.textContent === label,
  );
  return field?.children?.[1] || null;
}

async function loadPage() {
  const elements = new Map();
  for (const id of ["flash", "navigation", "context-badge", "view-overview", "view-library", "view-plans", "view-targets", "view-history", "view-radar"]) {
    const element = new FakeElement();
    element.id = id;
    elements.set(id, element);
  }
  const bridge = {
    async ready() { return new Promise(() => {}); },
    async apiGet() { return { plugin: { version: "bridge" } }; },
    async apiPost() { return { ok: true }; },
    t(key, fallback) { return fallback || key; },
  };
  const handlers = {};
  const window = {
    AstrBotPluginPage: bridge,
    location: { hash: "#overview" },
    addEventListener(name, handler) { handlers[name] = handler; },
  };
  const document = {
    documentElement: { dataset: {} },
    createElement(tag) { return new FakeElement(tag); },
    getElementById(id) { return elements.get(id) || new FakeElement(); },
    querySelectorAll(selector) { return selector === ".view" ? [...elements.values()].filter((item) => item.id?.startsWith("view-")) : []; },
  };
  const context = { window, document, Node: FakeElement, URL, console, setTimeout, clearTimeout, globalThis: null, __LAW_ASSISTANT_TEST__: true };
  context.globalThis = context;
  vm.runInNewContext(
    await readFile(new URL("../pages/law-assistant/app.js", import.meta.url), "utf8"),
    context,
    { filename: "pages/law-assistant/app.js" },
  );
  return { bridge, elements, handlers, window, page: context.__lawAssistantTest };
}

test("Page apiGet/apiPost consume the already-unwrapped Bridge payload", async () => {
  const { bridge, page } = await loadPage();
  bridge.apiGet = async () => ({ plugin: { version: "0.3.0" } });
  bridge.apiPost = async () => ({ success: false, message: "业务失败" });
  assert.deepEqual(await page.apiGet("overview"), { plugin: { version: "0.3.0" } });
  await assert.rejects(() => page.apiPost("plans/prepare"), /业务失败/);
});

test("overview renders safe active-session progress from the Bridge payload", async () => {
  const { bridge, elements, page } = await loadPage();
  bridge.apiGet = async () => ({
    plugin: { version: "0.6.0" },
    schema_version: 13,
    radar: { current: 0 },
    learning: {},
    targets: { count: 1 },
    recent: { daily: [] },
    question_sessions: [{
      target_label: "法硕一群",
      identity_label: "真题",
      subject: "刑法",
      question_type: "案例分析题",
      prompt_index: 2,
      prompt_count: 3,
      material_index: 1,
      material_count: 2,
      answer_revealed: false,
      explanation_revealed: false,
      // The overview contract must not need or render these source fields.
      question_text: "不得显示的题干秘密",
      answer_text: "不得显示的答案秘密",
    }],
  });
  page.state.route = "overview";
  page.state.renderGeneration = 1;

  await page.renderOverview();

  const rendered = textOf(elements.get("view-overview"));
  assert.match(rendered, /法硕一群/);
  assert.match(rendered, /真题/);
  assert.match(rendered, /2\/3/);
  assert.match(rendered, /1\/2/);
  assert.doesNotMatch(rendered, /不得显示的题干秘密|不得显示的答案秘密/);
});

test("question detail never renders answer, explanation, raw evidence, or metadata secrets", async () => {
  const { bridge, elements, page } = await loadPage();
  bridge.apiGet = async () => ({
    success: true,
    item: {
      id: 73,
      item_type: "question",
      identity: "mock_question",
      title: "安全边界合成题",
      subjects: ["criminal_law"],
      verification_status: "not_applicable",
      created_by: "42",
      updated_at: "2026-09-27T00:00:00Z",
      metadata: {
        body: "SECRET_METADATA_BODY",
        note: "SECRET_METADATA_NOTE",
        structured: { answer: "SECRET_NESTED_ANSWER" },
      },
    },
    question: {
      question_identity: "mock_question",
      question_type: "single_choice",
      stem: "SAFE_PAGE_STEM",
      options: ["SAFE_OPTION_A", "SAFE_OPTION_B"],
      answer: "SECRET_ANSWER",
      explanation: "SECRET_EXPLANATION",
      answer_source: "synthetic answer provenance",
    },
    sources: [{
      title: "Synthetic source title",
      source_url: "https://example.test/source",
      content_hash: "safe-source-hash",
      original_filename: "fixture.txt",
      raw_text: "SECRET_RAW_SOURCE",
      metadata: { raw_text: "SECRET_SOURCE_METADATA" },
    }],
    structured: { payload: { answer: "SECRET_STRUCTURED_PAYLOAD" } },
    subquestions: [{ answer: "SECRET_SUBQUESTION_ANSWER" }],
  });

  await page.renderLibraryDetail(elements.get("view-library"), 73);

  const rendered = textOf(elements.get("view-library"));
  assert.match(rendered, /SAFE_PAGE_STEM/);
  assert.match(rendered, /SAFE_OPTION_A/);
  assert.match(rendered, /Synthetic source title/);
  assert.match(rendered, /safe-source-hash/);
  assert.match(rendered, /synthetic answer provenance/);
  assert.match(rendered, /答案与解析通过 Question Session 显式揭晓/);
  for (const secret of [
    "SECRET_ANSWER",
    "SECRET_EXPLANATION",
    "SECRET_RAW_SOURCE",
    "SECRET_METADATA_BODY",
    "SECRET_METADATA_NOTE",
    "SECRET_NESTED_ANSWER",
    "SECRET_SOURCE_METADATA",
    "SECRET_STRUCTURED_PAYLOAD",
    "SECRET_SUBQUESTION_ANSWER",
  ]) {
    assert.doesNotMatch(rendered, new RegExp(secret));
  }
});

test("saving an operator question detail never submits a note field", async () => {
  const { bridge, elements, page } = await loadPage();
  const posts = [];
  bridge.apiGet = async () => ({
    success: true,
    item: {
      id: 73,
      item_type: "question",
      identity: "mock_question",
      title: "安全题目",
      subjects: ["criminal_law"],
      verification_status: "not_applicable",
      created_by: "42",
      updated_at: "2026-09-27T00:00:00Z",
    },
    question: { question_type: "single_choice", stem: "安全题干", options: [] },
    sources: [],
    source_links: [],
  });
  bridge.apiPost = async (endpoint, body) => {
    posts.push({ endpoint, body });
    return { success: true };
  };
  page.state.route = "library";
  page.state.renderGeneration = 1;

  await page.renderLibraryDetail(elements.get("view-library"), 73);
  labeledInput(elements.get("view-library"), "标题").value = "更新后的题目";
  labeledInput(elements.get("view-library"), "方向（可用规范名称或中文）").value = "民法";
  const save = findElement(
    elements.get("view-library"),
    (element) => element.tagName === "BUTTON" && element.textContent === "保存",
  );
  await save.listeners.click();

  assert.equal(posts.length, 1);
  assert.equal(posts[0].endpoint, "management/library-update");
  assert.equal(posts[0].body.item_id, 73);
  assert.equal(posts[0].body.changes.title, "更新后的题目");
  assert.equal(posts[0].body.changes.subjects, "民法");
  assert.deepEqual(Object.keys(posts[0].body.changes).sort(), [
    "answer", "answer_source", "exam_date", "exam_name", "exam_year",
    "explanation", "options", "paper", "question_number", "question_type",
    "stem", "subjects", "title",
  ]);
  assert.equal(posts[0].body.changes.note, undefined);
  assert.equal(labeledInput(elements.get("view-library"), "备注"), null);
});

test("saving a non-question detail continues to submit its edited note", async () => {
  const { bridge, elements, page } = await loadPage();
  const posts = [];
  bridge.apiGet = async () => ({
    success: true,
    item: {
      id: 81,
      item_type: "note",
      identity: "user_note",
      title: "学习笔记",
      subjects: ["civil_law"],
      metadata: { note: "已有备注" },
      verification_status: "not_applicable",
      created_by: "42",
      updated_at: "2026-09-27T00:00:00Z",
    },
    sources: [],
    source_links: [],
  });
  bridge.apiPost = async (endpoint, body) => {
    posts.push({ endpoint, body });
    return { success: true };
  };
  page.state.route = "library";
  page.state.renderGeneration = 1;

  await page.renderLibraryDetail(elements.get("view-library"), 81);
  labeledInput(elements.get("view-library"), "备注").value = "修改后的备注";
  const save = findElement(
    elements.get("view-library"),
    (element) => element.tagName === "BUTTON" && element.textContent === "保存",
  );
  await save.listeners.click();

  assert.equal(posts.length, 1);
  assert.equal(posts[0].endpoint, "management/library-update");
  assert.equal(posts[0].body.changes.note, "修改后的备注");
  assert.deepEqual(Object.keys(posts[0].body.changes).sort(), ["body", "note", "subjects", "title"]);
});

test("independent verified-question inventory supports edit and confirmed disable", async () => {
  const { bridge, elements, page } = await loadPage();
  const posts = [];
  let disabled = false;
  const question = {
    id: 17, source_name: "合成题库", exam_name: "合成考试", exam_year: "2098",
    exam_date: "", paper: "合成卷", question_number: "第1题",
    source_url: "", source_locator: "fixture:17", subject: "criminal_law",
    question_type: "single_choice", stem: "初始题干？", options: ["A. 甲", "B. 乙"],
    answer: "A", answer_source: "user_verified", explanation: "合成解析。",
    verification_status: "user_verified", management_mode: "independent", linked_item_ids: [],
  };
  bridge.apiGet = async (endpoint) => {
    if (endpoint === "management/real-questions") {
      return { items: [{ ...question, selectable: !disabled }] };
    }
    if (endpoint === "management/real-question") {
      return { question: { ...question, selectable: !disabled } };
    }
    if (endpoint === "overview") return { plugin: { version: "0.6.0" } };
    throw new Error(`unexpected GET ${endpoint}`);
  };
  bridge.apiPost = async (endpoint, body) => {
    posts.push({ endpoint, body });
    if (endpoint === "management/real-question-update") Object.assign(question, body.changes);
    if (endpoint === "management/real-question-status") disabled = !body.active;
    return { success: true, question: { ...question, selectable: !disabled } };
  };
  page.state.route = "library";
  page.state.libraryTab = "real-questions";
  page.state.renderGeneration = 1;
  const view = elements.get("view-library");
  await page.renderRealQuestions(view, 1, "library");
  await findElement(view, (element) => element.tagName === "BUTTON" && element.textContent === "查看 / 管理").listeners.click();
  labeledInput(view, "stem").value = "复核后的题干？";
  const save = findElement(view, (element) => element.tagName === "BUTTON" && element.textContent === "保存并重新校验");
  await save.listeners.click();
  assert.equal(posts[0].endpoint, "management/real-question-update");
  assert.equal(posts[0].body.changes.stem, "复核后的题干？");
  assert.deepEqual(Array.from(posts[0].body.changes.options), ["A. 甲", "B. 乙"]);

  await findElement(view, (element) => element.tagName === "BUTTON" && element.textContent === "查看 / 管理").listeners.click();
  await findElement(view, (element) => element.tagName === "BUTTON" && element.textContent === "预览停用真题").listeners.click();
  assert.match(textOf(view), /确认将该条目从真题检索库存停用/);
  await findElement(view, (element) => element.tagName === "BUTTON" && element.textContent === "确认").listeners.click();
  assert.equal(posts[1].endpoint, "management/real-question-status");
  assert.equal(posts[1].body.active, false);
});

test("library detail expands inline, toggles, switches rows, and saves in place", async () => {
  const { bridge, elements, page } = await loadPage();
  const updates = [];
  bridge.apiGet = async (endpoint, params) => {
    if (endpoint === "management/library-search") {
      return {
        items: [
          { id: 1, item_type: "note", title: "资料甲", subjects: [], active: true },
          { id: 2, item_type: "note", title: "资料乙", subjects: [], active: true },
        ],
        page: params.page, page_size: params.page_size, total: 2, page_count: 1,
      };
    }
    if (endpoint === "library/manage-item") {
      return { item: { id: Number(params.id), item_type: "note", title: `资料${params.id}`, subjects: [], active: true, metadata: { body: "正文" } }, sources: [] };
    }
    throw new Error(`unexpected GET ${endpoint}`);
  };
  bridge.apiPost = async (endpoint, body) => {
    updates.push({ endpoint, body });
    return { success: true };
  };
  page.state.route = "library";
  page.state.libraryTab = "materials";
  page.state.renderGeneration = 1;
  const view = elements.get("view-library");
  await page.renderMaterials(view);
  const viewButtons = findElements(view, (element) => element.tagName === "BUTTON" && element.textContent === "查看");
  assert.equal(viewButtons.length, 2);
  await viewButtons[0].listeners.click();
  let detailRows = findElements(view, (element) => element.className === "library-inline-detail-row");
  assert.equal(detailRows.length, 1);
  const tableRows = findElements(view, (element) => element.tagName === "TR");
  assert.equal(tableRows[tableRows.indexOf(detailRows[0]) - 1].children[1].textContent, "1");

  await viewButtons[0].listeners.click();
  assert.equal(findElements(view, (element) => element.className === "library-inline-detail-row").length, 0);
  await viewButtons[0].listeners.click();
  await viewButtons[1].listeners.click();
  detailRows = findElements(view, (element) => element.className === "library-inline-detail-row");
  assert.equal(detailRows.length, 1);
  assert.match(textOf(detailRows[0]), /资料2/);

  const save = findElement(detailRows[0], (element) => element.tagName === "BUTTON" && element.textContent === "保存");
  await save.listeners.click();
  await save.listeners.click();
  assert.equal(updates.length, 2);
  assert.equal(findElements(view, (element) => element.className === "library-inline-detail-row").length, 1);
  assert.equal(detailRows[0].children[0].children.filter((child) => child.className === "detail-panel").length, 1);
});

test("answer-safe question review renders only its allowlisted preview and structure", async () => {
  const { bridge, elements, page } = await loadPage();
  bridge.apiGet = async () => ({
    item: {
      id: 91,
      material_type: "real_question_candidate",
      review_reason: "需要确认题目边界",
      answer_safe: true,
      safe_preview: "REVIEW_SAFE_STEM\nA. REVIEW_SAFE_OPTION_A\nB. REVIEW_SAFE_OPTION_B",
      safe_structure: {
        stem: "REVIEW_SAFE_STEM",
        options: [
          { key: "A", text: "REVIEW_SAFE_OPTION_A", locator: "PDF第1页" },
          { key: "B", text: "REVIEW_SAFE_OPTION_B", locator: "PDF第1页" },
        ],
      },
      raw_fragment: "SECRET_REVIEW_RAW",
      proposed_structure: {
        answer: { keys: ["SECRET_REVIEW_ANSWER"] },
        explanation_blocks: [{ text: "SECRET_REVIEW_EXPLANATION" }],
      },
    },
  });

  page.state.route = "library";
  page.state.renderGeneration = 1;
  await page.renderReviewDetail(elements.get("view-library"), 91);

  const rendered = textOf(elements.get("view-library"));
  assert.match(rendered, /REVIEW_SAFE_STEM/);
  assert.match(rendered, /A\. REVIEW_SAFE_OPTION_A/);
  assert.match(rendered, /B\. REVIEW_SAFE_OPTION_B/);
  assert.match(rendered, /答案与解析通过 Question Session 显式揭晓/);
  assert.doesNotMatch(rendered, /SECRET_REVIEW_RAW|SECRET_REVIEW_ANSWER|SECRET_REVIEW_EXPLANATION/);
});

test("stale asynchronous overview render cannot overwrite the newest generation", async () => {
  const { bridge, elements, page } = await loadPage();
  let resolveFirst;
  bridge.apiGet = async () => new Promise((resolve) => { resolveFirst = resolve; });
  page.state.route = "overview";
  page.state.renderGeneration = 1;
  const first = page.renderOverview();
  bridge.apiGet = async () => ({ plugin: { version: "new" }, schema_version: 13 });
  page.state.renderGeneration = 2;
  const second = page.renderOverview();
  await second;
  resolveFirst({ plugin: { version: "stale" }, schema_version: 1 });
  await first;
  assert.match(textOf(elements.get("view-overview")), /new/);
  assert.doesNotMatch(textOf(elements.get("view-overview")), /stale/);
});

test("stale Targets and Plans responses do not duplicate visible cards", async () => {
  const { bridge, elements, page } = await loadPage();
  let resolveTargets;
  let targetsCalls = 0;
  bridge.apiGet = async (endpoint) => {
    if (endpoint === "targets") {
      targetsCalls += 1;
      if (targetsCalls === 1) return new Promise((resolve) => { resolveTargets = resolve; });
      return { items: [{ id: 1, label: "一群", unified_msg_origin: "aiocqhttp:group:1", enabled: true }], page: 1, page_size: 20, total: 1, page_count: 1 };
    }
    return {
      global: {
        daily_case: { plan: { enabled: true, selection_mode: "random", time: "08:00" } },
        daily_question: { plan: { enabled: true, selection_mode: "random", time: "09:00" } },
      },
      targets: [],
    };
  };
  page.state.route = "targets";
  page.state.renderGeneration = 3;
  const firstTargets = page.renderTargets();
  const secondTargets = page.renderTargets();
  await secondTargets;
  resolveTargets({ items: [{ id: 1, label: "旧一群", unified_msg_origin: "old", enabled: true }], page: 1, page_size: 20, total: 1, page_count: 1 });
  await firstTargets;
  assert.equal((textOf(elements.get("view-targets")).match(/一群/g) || []).length, 1);

  let resolvePlans;
  let plansCalls = 0;
  bridge.apiGet = async (endpoint) => {
    if (endpoint !== "plans") return [];
    plansCalls += 1;
    if (plansCalls === 1) return new Promise((resolve) => { resolvePlans = resolve; });
    return {
      global: {
        daily_case: { plan: { enabled: true, selection_mode: "random", time: "08:00" } },
        daily_question: { plan: { enabled: true, selection_mode: "random", time: "09:00" } },
      },
      targets: [],
    };
  };
  page.state.route = "plans";
  const firstPlans = page.renderPlans();
  const secondPlans = page.renderPlans();
  await secondPlans;
  resolvePlans({ global: {}, targets: [] });
  await firstPlans;
  // The heading and its description each contain this phrase; a stale render
  // would append a second pair and therefore produce four occurrences.
  assert.equal((textOf(elements.get("view-plans")).match(/全局默认/g) || []).length, 2);
});

test("empty last target page keeps pagination controls to recover after removal", async () => {
  const { bridge, elements, page } = await loadPage();
  const requests = [];
  bridge.apiGet = async (endpoint, params) => {
    assert.equal(endpoint, "targets");
    requests.push(params.page);
    return params.page === 2
      ? { items: [], page: 2, page_size: 20, total: 21, page_count: 2 }
      : { items: [{ id: 1, label: "剩余群", unified_msg_origin: "aiocqhttp:group:1", enabled: true }], page: 1, page_size: 20, total: 1, page_count: 1 };
  };

  page.state.route = "targets";
  page.state.renderGeneration = 4;
  page.state.pages.targets = { page: 2, page_size: 20 };
  await page.renderTargets();

  const previous = findElement(elements.get("view-targets"),
    (child) => child.tagName === "BUTTON" && child.textContent === "上一页",
  );
  assert.ok(previous, "empty result page should retain its pager");
  await previous.listeners.click();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(requests, [2, 1]);
  assert.match(textOf(elements.get("view-targets")), /剩余群/);
});

test("navigate plus hashchange performs one effective route render", async () => {
  const { bridge, handlers, window, page } = await loadPage();
  let librarySearchCalls = 0;
  bridge.apiGet = async (endpoint) => {
    if (endpoint === "management/library-search") {
      librarySearchCalls += 1;
      return { count: 0, items: [] };
    }
    return [];
  };
  page.state.route = "overview";
  page.state.renderGeneration = 0;
  await page.navigate("library");
  window.location.hash = "#library";
  await handlers.hashchange();
  assert.equal(librarySearchCalls, 1);
});

test("management batch preview locks selected page IDs and cancel never confirms", async () => {
  const { bridge, elements, page } = await loadPage();
  const posts = [];
  bridge.apiGet = async () => ({
    items: [{ id: 7, title: "合成资料", identity: "user_note", subjects: [], active: true }],
    page: 1, page_size: 20, total: 1, page_count: 1,
  });
  bridge.apiPost = async (endpoint, body) => {
    posts.push({ endpoint, body });
    if (endpoint === "management/batch-prepare") return { token: "batch-preview", count: 1, items: [{ id: 7, title: "合成资料", identity: "user_note", active: true }] };
    return { success: true };
  };
  page.state.route = "library";
  page.state.renderGeneration = 1;
  await page.renderMaterials(elements.get("view-library"));
  const checkbox = findElements(elements.get("view-library"), (element) => element.tagName === "INPUT" && element.type === "checkbox")[1];
  checkbox.checked = true;
  await checkbox.listeners.change();
  const prepare = findElement(elements.get("view-library"), (element) => element.tagName === "BUTTON" && element.textContent === "预览软删除");
  await prepare.listeners.click();
  assert.equal(posts.length, 1);
  const preview = findElement(elements.get("view-library"), (element) => element.tagName === "ARTICLE" && textOf(element).includes("批量操作预览"));
  assert.match(textOf(preview), /合成资料/);
  assert.equal(posts[0].endpoint, "management/batch-prepare");
  assert.equal(posts[0].body.action, "delete");
  assert.deepEqual(Array.from(posts[0].body.item_ids), [7]);
  assert.deepEqual(Object.keys(posts[0].body.changes), []);
  const cancel = findElement(preview, (element) => element.tagName === "BUTTON" && element.textContent === "取消");
  cancel.listeners.click();
  assert.equal(posts.some((entry) => entry.endpoint === "management/batch-confirm"), false);
});

test("full data reset requires its exact phrase and stays uncommitted when preview is canceled", async () => {
  const { bridge, elements, page } = await loadPage();
  const posts = [];
  bridge.apiPost = async (endpoint, body) => {
    posts.push({ endpoint, body });
    return { token: "clear-token", scope: "all_runtime", total: 3, counts: { events: 2, daily_plans: 1 }, retained: ["SQLite schema"] };
  };
  page.renderDataManagement(elements.get("view-library"));
  const scope = findElement(elements.get("view-library"), (element) => element.tagName === "SELECT");
  scope.value = "all_runtime";
  const prepare = findElement(elements.get("view-library"), (element) => element.tagName === "BUTTON" && element.textContent === "预览清理");
  await prepare.listeners.click();
  const phrase = findElement(elements.get("view-library"), (element) => element.tagName === "INPUT" && element.placeholder === "输入：清空全部数据");
  assert.ok(phrase);
  const confirm = findElement(elements.get("view-library"), (element) => element.tagName === "BUTTON" && element.textContent === "确认清理");
  assert.equal(confirm.disabled, true);
  phrase.value = "清空全部数据";
  phrase.listeners.input();
  assert.equal(confirm.disabled, false);
  const cancel = findElement(elements.get("view-library"), (element) => element.tagName === "BUTTON" && element.textContent === "取消");
  cancel.listeners.click();
  assert.deepEqual(posts.map((entry) => entry.endpoint), ["management/clear-prepare"]);
  assert.equal(posts[0].body.scope, "all_runtime");
});

test("Review batch selection locks exact IDs and requires explicit confirmation", async () => {
  const { bridge, elements, page } = await loadPage();
  const posts = [];
  bridge.apiGet = async (endpoint) => endpoint === "reviews"
    ? { items: [{ id: 31, material_type: "question", locator: "fixture:1", status: "pending" }], total: 1, page: 1, page_size: 20, page_count: 1 }
    : {};
  bridge.apiPost = async (endpoint, body) => {
    posts.push({ endpoint, body });
    if (endpoint.endsWith("prepare")) return { token: "review-batch-token", count: 1, items: [] };
    return { count: 1 };
  };
  page.state.route = "library";
  page.state.renderGeneration = 1;
  await page.renderReviews(elements.get("view-library"));
  const selectPage = findElement(elements.get("view-library"), (element) => element.tagName === "BUTTON" && element.textContent === "选择本页");
  await selectPage.listeners.click();
  const prepare = findElement(elements.get("view-library"), (element) => element.tagName === "BUTTON" && element.textContent === "预览批量复核状态");
  await prepare.listeners.click();
  const preview = findElement(elements.get("view-library"), (element) => element.className === "confirm-panel");
  assert.ok(preview);
  assert.equal(posts[0].endpoint, "management/moderation-batch-prepare");
  assert.equal(posts[0].body.domain, "review");
  assert.deepEqual(Array.from(posts[0].body.item_ids), [31]);
  const confirm = findElement(preview, (element) => element.tagName === "BUTTON" && element.textContent === "确认应用");
  await confirm.listeners.click();
  assert.equal(posts[1].endpoint, "management/moderation-batch-confirm");
  assert.equal(posts[1].body.token, "review-batch-token");
});

test("Radar batch selection locks exact IDs and sends the reviewed reason", async () => {
  const { bridge, elements, page } = await loadPage();
  const posts = [];
  bridge.apiGet = async () => ({ items: [{ id: 41, title: "合成活动", radar_status: "current" }], total: 1, page: 1, page_size: 20, page_count: 1 });
  bridge.apiPost = async (endpoint, body) => {
    posts.push({ endpoint, body });
    return { token: "radar-batch-token", count: 1, items: [] };
  };
  page.state.route = "radar";
  page.state.renderGeneration = 1;
  await page.renderRadar();
  const selects = findElements(elements.get("view-radar"), (element) => element.tagName === "SELECT");
  assert.ok(selects[1].children.some((option) => /删除\/移除（可恢复）/.test(option.textContent)));
  selects[1].value = "historical";
  const reason = findElement(elements.get("view-radar"), (element) => element.tagName === "INPUT" && element.placeholder.includes("批量状态变更原因"));
  reason.value = "合成复核原因";
  const selectPage = findElement(elements.get("view-radar"), (element) => element.tagName === "BUTTON" && element.textContent === "选择本页");
  await selectPage.listeners.click();
  const prepare = findElement(elements.get("view-radar"), (element) => element.tagName === "BUTTON" && element.textContent === "预览批量状态");
  await prepare.listeners.click();
  assert.equal(posts[0].endpoint, "management/moderation-batch-prepare");
  assert.equal(posts[0].body.domain, "radar");
  assert.deepEqual(Array.from(posts[0].body.item_ids), [41]);
  assert.equal(posts[0].body.changes.status, "historical");
  assert.equal(posts[0].body.changes.reason, "合成复核原因");
});

test("user-case verification is a bounded batch action and keeps the case identity", async () => {
  const { bridge, elements, page } = await loadPage();
  const posts = [];
  bridge.apiGet = async () => ({
    items: [{ id: 22, item_type: "case", identity: "user_case", title: "合成用户案例", verification_status: "pending_review" }],
    total: 1, page: 1, page_size: 20, page_count: 1,
  });
  bridge.apiPost = async (endpoint, body) => {
    posts.push({ endpoint, body });
    if (endpoint === "management/batch-prepare") {
      return {
        token: "case-verify-token", count: 1,
        items: [{ id: 22, title: "合成用户案例", identity: "user_case", verification_before: "pending_review", verification_after: "user_verified" }],
      };
    }
    return { count: 1, changed_ids: [22] };
  };
  page.state.route = "library";
  page.state.renderGeneration = 1;
  await page.renderMaterials(elements.get("view-library"));

  const selectPage = findElement(elements.get("view-library"), (element) => element.tagName === "BUTTON" && element.textContent === "选择本页");
  await selectPage.listeners.click();
  const status = labeledInput(elements.get("view-library"), "用户案例核验状态");
  assert.ok(status);
  assert.ok(status.children.some((option) => option.value === "user_verified"));
  assert.ok(status.children.some((option) => option.value === "unverified"));
  status.value = "user_verified";
  const prepare = findElement(elements.get("view-library"), (element) => element.tagName === "BUTTON" && element.textContent === "预览用户案例核验");
  await prepare.listeners.click();
  assert.equal(posts[0].body.action, "verify_case");
  assert.deepEqual(Array.from(posts[0].body.item_ids), [22]);
  assert.equal(posts[0].body.changes.verification_status, "user_verified");
  const preview = findElement(elements.get("view-library"), (element) => element.className === "confirm-panel");
  assert.match(textOf(preview), /user_case/);
  assert.match(textOf(preview), /user_verified/);
  const confirm = findElement(preview, (element) => element.tagName === "BUTTON" && element.textContent === "确认应用");
  await confirm.listeners.click();
  assert.equal(posts[1].body.token, "case-verify-token");
});

test("single-event Radar moderation can choose the ignored display status", async () => {
  const { bridge, elements, page } = await loadPage();
  bridge.apiGet = async (endpoint) => endpoint === "radar/event"
    ? {
      id: 41, title: "合成活动", radar_status: "current", dates: [],
      status_override: null, source_url: "https://example.test/event",
    }
    : { items: [{ id: 41, title: "合成活动", radar_status: "current" }], total: 1, page: 1, page_size: 20, page_count: 1 };
  page.state.route = "radar";
  page.state.renderGeneration = 1;
  await page.renderRadar();
  const view = findElement(elements.get("view-radar"), (element) => element.tagName === "BUTTON" && element.textContent === "查看");
  await view.listeners.click();

  const statusLabel = findElement(elements.get("view-radar"), (element) =>
    element.tagName === "LABEL" && element.children?.[0]?.textContent === "人工展示状态");
  assert.ok(statusLabel);
  assert.ok(statusLabel.children[1].children.some((option) => option.value === "ignored"));
});

test("daily reveal controls are question-only and enter the saved plan preview", async () => {
  const { elements, page } = await loadPage();
  const questionPlan = page.planForm("daily_question", {
    enabled: true, time: "08:00", selection_mode: "random", question_reveal_mode: "manual",
  }, (changes) => { elements.set("captured-plan", changes); });
  assert.ok(labeledInput(questionPlan, "答案延迟分钟"));
  const revealMode = labeledInput(questionPlan, "答案/解析揭晓模式");
  revealMode.value = "delayed";
  revealMode.listeners.change();
  const answerDelay = labeledInput(questionPlan, "答案延迟分钟");
  const explanationDelay = labeledInput(questionPlan, "解析延迟分钟");
  assert.equal(Number(answerDelay.value), 15);
  assert.equal(Number(explanationDelay.value), 30);
  const preview = findElement(questionPlan, (element) => element.tagName === "BUTTON" && element.textContent === "预览并保存");
  explanationDelay.value = "10";
  preview.listeners.click();
  assert.equal(elements.get("captured-plan"), undefined);
  assert.match(textOf(questionPlan), /解析延迟不能早于答案/);
  explanationDelay.value = "45";
  preview.listeners.click();
  assert.equal(elements.get("captured-plan").question_reveal_mode, "delayed");
  assert.equal(elements.get("captured-plan").answer_reveal_delay_minutes, 15);
  assert.equal(elements.get("captured-plan").explanation_reveal_delay_minutes, 45);

  const casePlan = page.planForm("daily_case", { enabled: true, selection_mode: "random" }, () => {});
  assert.equal(labeledInput(casePlan, "答案延迟分钟"), null);
});

test("daily plan UI exposes dependent modes and maps first direction/type without raw indexes", async () => {
  const { page } = await loadPage();
  let preview;
  const form = page.planForm("daily_question", {
    enabled: true,
    selection_mode: "random",
    rotation_subjects: ["civil_law", "economic_law"],
    rotation_start_index: 1,
    rotation_start_date: "2026-09-29",
    question_type_selection_mode: "rotation",
    rotation_question_types: ["single_choice", "multiple_choice"],
    question_type_rotation_start_index: 1,
    question_type_rotation_start_date: "2026-09-29",
    question_origin: "mock",
  }, (changes) => { preview = changes; });
  const labelControl = (label) => labeledInput(form, label);
  const directionMode = labelControl("方向模式");
  const questionTypeMode = labelControl("题型模式");
  const fixedSubject = labelControl("固定方向").parentElement;
  const rotationField = findElement(form, (element) => element.className === "form-field" && textOf(element).includes("方向顺序"));
  assert.equal(fixedSubject.hidden, true);
  assert.equal(rotationField.hidden, true);

  directionMode.value = "fixed";
  directionMode.listeners.change();
  assert.equal(fixedSubject.hidden, false);
  assert.equal(rotationField.hidden, true);
  directionMode.value = "rotation";
  directionMode.listeners.change();
  questionTypeMode.value = "rotation";
  questionTypeMode.listeners.change();
  assert.equal(fixedSubject.hidden, true);
  assert.equal(rotationField.hidden, false);
  assert.equal(labelControl("首日方向").value, "economic_law");
  assert.equal(labelControl("首日题型").value, "multiple_choice");
  assert.equal(labelControl("起始位置"), null);
  assert.equal(labelControl("题型轮换起始位置"), null);

  labelControl("首日方向").value = "civil_law";
  labelControl("首日方向").listeners.change();
  labelControl("首日题型").value = "single_choice";
  labelControl("首日题型").listeners.change();
  const previewButton = findElement(form, (element) => element.tagName === "BUTTON" && element.textContent === "预览并保存");
  previewButton.listeners.click();
  assert.equal(preview.rotation_start_index, 0);
  assert.deepEqual(Array.from(preview.rotation_subjects), ["civil_law", "economic_law"]);
  assert.equal(preview.question_type_rotation_start_index, 0);
  assert.deepEqual(Array.from(preview.rotation_question_types), ["single_choice", "multiple_choice"]);

  directionMode.value = "random";
  directionMode.listeners.change();
  previewButton.listeners.click();
  assert.equal(preview.fixed_subject, null);
  assert.deepEqual(Array.from(preview.rotation_subjects), []);
  assert.equal(preview.rotation_start_date, null);
});

test("daily plan confirmation renders canonical values with Chinese labels", async () => {
  const { bridge, elements, page } = await loadPage();
  bridge.apiGet = async (endpoint) => {
    assert.equal(endpoint, "plans");
    return {
      global: {
        daily_case: { plan: { enabled: false, selection_mode: "random", time: "08:00" } },
        daily_question: { plan: { enabled: false, selection_mode: "random", time: "08:00" } },
      },
      targets: [],
    };
  };
  bridge.apiPost = async (endpoint) => {
    assert.equal(endpoint, "plans/prepare");
    return {
      token: "plan-token",
      plans: [{
        content_type: "daily_question",
        plan: {
          enabled: true,
          time: "08:00",
          content_type: "daily_question",
          selection_mode: "rotation",
          rotation_subjects: ["civil_law", "economic_law"],
          question_origin: "mock",
          question_type_selection_mode: "fixed",
          fixed_question_type: "multiple_choice",
          question_reveal_mode: "delayed",
        },
        preview: [{ date: "2026-09-29", subject: "civil_law", question_type: "multiple_choice", origin: "mock" }],
      }],
    };
  };
  page.state.route = "plans";
  page.state.renderGeneration = 1;
  await page.renderPlans();
  const previewButtons = findElements(
    elements.get("view-plans"),
    (element) => element.tagName === "BUTTON" && element.textContent === "预览并保存",
  );
  await previewButtons[1].listeners.click();
  await new Promise((resolve) => setTimeout(resolve, 0));

  const rendered = textOf(elements.get("view-plans"));
  assert.match(rendered, /每日一题/);
  assert.match(rendered, /方向轮换/);
  assert.match(rendered, /模拟题/);
  assert.match(rendered, /固定题型/);
  assert.match(rendered, /定时揭晓/);
  assert.doesNotMatch(rendered, /\brotation\b|\bmock\b|\bdelayed\b|civil_law|multiple_choice/);
});

test("structured import preview renders candidate identity and review counts", async () => {
  const { elements, page } = await loadPage();
  page.renderStructuredImportPreview(elements.get("view-library"), {
    token: "structured-token",
    preview: {
      original_filename: "verified.pdf",
      schema_version: "1.0",
      counts: { questions: 1, cases: 1, materials: 1, processable: 2, entry_errors: 0, review_items: 1 },
      review: 1,
      questions: [{ id: "q-1", source_number: "2022-一", question_type: "short_answer", title: "知识产权简答题", stem_block_count: 1, subquestion_count: 1, answer_status: "provided", review_status: "pending_review" }],
      cases: [{ id: "c-1", title: "合同纠纷案例", review_status: "ready" }],
    },
  });
  const text = textOf(elements.get("view-library"));
  assert.match(text, /verified\.pdf/);
  assert.match(text, /知识产权简答题/);
  assert.match(text, /合同纠纷案例/);
  assert.match(text, /review 1/);
});

test("structured import preview exposes structured case evidence and pending identity", async () => {
  const { elements, page } = await loadPage();
  page.renderStructuredImportPreview(elements.get("view-library"), {
    token: "synthetic-token",
    preview: {
      counts: { questions: 0, cases: 1, materials: 0, processable: 1 },
      questions: [],
      cases: [{
        id: "case-fixture-1",
        title: "合成案例标题",
        authority: "合成声明机关",
        case_number: "合成案号",
        subjects: ["civil_commercial"],
        basic_facts: ["合成案情事实"],
        issues: ["合成争议焦点"],
        holding: ["合成裁判要旨"],
        result: ["合成处理结果"],
        learning_points: ["合成学习要点"],
        locators: ["第1页"],
        review_status: "pending_review",
        identity_granted: false,
      }],
    },
  });
  const rendered = textOf(elements.get("view-library"));
  for (const evidence of [
    "合成案例标题", "合成声明机关", "合成案号", "合成案情事实",
    "合成争议焦点", "合成裁判要旨", "合成处理结果", "合成学习要点",
    "pending_review",
  ]) assert.match(rendered, new RegExp(evidence));
});

test("Radar date review explicitly previews and submits human confirmation state", async () => {
  const { bridge, elements, page } = await loadPage();
  const requests = [];
  bridge.apiGet = async () => ({
    id: 17,
    title: "合成活动",
    event_type: "competition",
    radar_status: "needs_review",
    organizer: "合成主办方",
    eligibility: "合成参赛对象",
    dates: [{
      id: 81,
      kind: "registration_deadline",
      datetime: "2026-10-01T00:00:00+08:00",
      label: "报名截止",
      evidence_text: "报名截止：10月1日",
      confirmed: false,
    }],
    status_override: null,
    source_url: "https://example.test/synthetic-event",
  });
  bridge.apiPost = async (path, body) => {
    requests.push({ path, body });
    if (path === "radar/date-review-prepare") {
      return {
        token: "date-review-token",
        old_value: { datetime: "2026-10-01T00:00:00+08:00", confirmed: false },
        proposed_value: { datetime: "2026-10-02T00:00:00+08:00", confirmed: body.proposed_confirmed },
        decision: body.decision,
        reason: body.reason,
      };
    }
    return { success: true };
  };
  page.state.route = "radar";
  page.state.renderGeneration = 1;

  await page.renderRadarDetail(elements.get("view-radar"), 17, 1, "radar");
  const checkbox = labeledInput(elements.get("view-radar"), "明确确认此日期证据可用于截止与提醒");
  assert.ok(checkbox, "human confirmation checkbox must be visible");
  checkbox.checked = true;
  labeledInput(elements.get("view-radar"), "日期节点").value = "81";
  labeledInput(elements.get("view-radar"), "决定").value = "accepted";
  const previewButton = findElement(
    elements.get("view-radar"),
    (element) => element.tagName === "BUTTON" && element.textContent === "预览日期复核",
  );
  await previewButton.listeners.click();

  const prepare = requests.find((request) => request.path === "radar/date-review-prepare");
  assert.equal(prepare.body.proposed_confirmed, true);
  const rendered = textOf(elements.get("view-radar"));
  assert.match(rendered, /当前已确认/);
  assert.match(rendered, /操作后已确认/);
  assert.match(rendered, /报名截止：10月1日/);
});
