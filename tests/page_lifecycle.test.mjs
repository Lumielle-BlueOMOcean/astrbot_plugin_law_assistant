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
    schema_version: 11,
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

test("stale asynchronous overview render cannot overwrite the newest generation", async () => {
  const { bridge, elements, page } = await loadPage();
  let resolveFirst;
  bridge.apiGet = async () => new Promise((resolve) => { resolveFirst = resolve; });
  page.state.route = "overview";
  page.state.renderGeneration = 1;
  const first = page.renderOverview();
  bridge.apiGet = async () => ({ plugin: { version: "new" }, schema_version: 11 });
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
