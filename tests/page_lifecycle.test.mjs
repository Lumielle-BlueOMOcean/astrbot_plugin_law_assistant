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
    this.classList = { toggle() {} };
  }

  append(...children) { this.children.push(...children.flat().filter(Boolean)); }
  prepend(...children) { this.children.unshift(...children.flat().filter(Boolean)); }
  replaceChildren(...children) { this.children = children.flat().filter(Boolean); }
  remove() { this.removed = true; }
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

function labeledInput(root, label) {
  const field = findElement(
    root,
    (element) => element.tagName === "LABEL" && element.children?.[0]?.textContent === label,
  );
  return field?.children?.[1] || null;
}

async function loadPage() {
  const elements = new Map();
  for (const id of ["flash", "navigation", "context-badge", "view-overview", "view-library", "view-plans", "view-targets", "view-history"]) {
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
    plugin: { version: "0.5.0" },
    schema_version: 12,
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

test("saving a safe question detail never submits a note field", async () => {
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
  assert.equal(posts[0].endpoint, "library/update");
  assert.equal(posts[0].body.item_id, 73);
  assert.equal(posts[0].body.changes.title, "更新后的题目");
  assert.equal(posts[0].body.changes.subjects, "民法");
  assert.deepEqual(Object.keys(posts[0].body.changes).sort(), ["subjects", "title"]);
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
  assert.equal(posts[0].endpoint, "library/update");
  assert.equal(posts[0].body.changes.note, "修改后的备注");
  assert.deepEqual(Object.keys(posts[0].body.changes).sort(), ["note", "subjects", "title"]);
});

test("answer-safe question review renders only its allowlisted preview and structure", async () => {
  const { bridge, elements, page } = await loadPage();
  bridge.apiGet = async () => ({
    item: {
      id: 91,
      material_type: "real_question_candidate",
      review_reason: "需要确认题目边界",
      answer_safe: true,
      safe_preview: "REVIEW_SAFE_STEM",
      safe_structure: { stem: "REVIEW_SAFE_STEM", answer: "SECRET_SAFE_STRUCTURE_ANSWER" },
      raw_fragment: "SECRET_REVIEW_RAW",
      proposed_structure: { answer: "SECRET_REVIEW_ANSWER" },
    },
  });

  page.state.route = "library";
  page.state.renderGeneration = 1;
  await page.renderReviewDetail(elements.get("view-library"), 91);

  const rendered = textOf(elements.get("view-library"));
  assert.match(rendered, /REVIEW_SAFE_STEM/);
  assert.match(rendered, /答案与解析通过 Question Session 显式揭晓/);
  assert.doesNotMatch(rendered, /SECRET_SAFE_STRUCTURE_ANSWER|SECRET_REVIEW_RAW|SECRET_REVIEW_ANSWER/);
});

test("stale asynchronous overview render cannot overwrite the newest generation", async () => {
  const { bridge, elements, page } = await loadPage();
  let resolveFirst;
  bridge.apiGet = async () => new Promise((resolve) => { resolveFirst = resolve; });
  page.state.route = "overview";
  page.state.renderGeneration = 1;
  const first = page.renderOverview();
  bridge.apiGet = async () => ({ plugin: { version: "new" }, schema_version: 12 });
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
      return [{ id: 1, label: "一群", unified_msg_origin: "aiocqhttp:group:1", enabled: true }];
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
  resolveTargets([{ id: 1, label: "旧一群", unified_msg_origin: "old", enabled: true }]);
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

test("navigate plus hashchange performs one effective route render", async () => {
  const { bridge, handlers, window, page } = await loadPage();
  let librarySearchCalls = 0;
  bridge.apiGet = async (endpoint) => {
    if (endpoint === "library/search") {
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
