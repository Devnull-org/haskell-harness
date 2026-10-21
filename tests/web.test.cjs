const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

// Exercise the shipped event handlers with a small DOM and controllable HTTP calls.
class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.scrollTop = 0;
    this.scrollHeight = 1000;
    this.clientHeight = 500;
    this.text = "";
    this.dataset = {};
  }
  set textContent(text) {
    this.text = String(text);
    this.children = [];
  }
  get textContent() {
    return this.text + this.children.map((n) => n.textContent).join("");
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.text = "";
    this.children = children;
  }
  querySelector() {
    return (
      this.children.find((n) => n.tagName === "button") || new Element("button")
    );
  }
  querySelectorAll() {
    return [];
  }
  addEventListener() {}
  focus() {}
}

function task(name = "example") {
  return {
    task_id: name,
    stage: "exploration",
    request: "Investigate the parser",
    base_ref: "main",
    base: "abc123",
    messages: [{ text: "First question" }],
    prompt: { actions: ["question", "done"], approval_token: "test" },
  };
}
function snapshot(current = task()) {
  return {
    repository: "/project",
    base: "main",
    backend: "test",
    tasks: [{ task_id: current.task_id, stage: current.stage }],
    task: current,
    job: null,
    can_stop: false,
  };
}
const response = (body) => ({ ok: true, json: async () => body });
const settle = () => new Promise(setImmediate);

async function app({ pathname = "/", fetcher: initialFetch } = {}) {
  const elements = new Map();
  const html = readFileSync("src/lambda_harness/web/index.html", "utf8");
  for (const [, id] of html.matchAll(/id="([^"]+)"/g))
    elements.set(id, new Element());
  elements.get("new-form").append(new Element("button"));
  let fetcher = initialFetch || (async () => response(snapshot()));
  const location = { pathname };
  const navigation = [];
  const listeners = {};
  const kind = { value: "explore" };
  let timerId = 0;
  const timers = new Map();
  const context = vm.createContext({
    location,
    history: {
      replaceState: (_, __, path) => {
        location.pathname = path;
      },
      pushState: (_, __, path) => {
        location.pathname = path;
        navigation.push(path);
      },
    },
    addEventListener: (name, callback) => {
      listeners[name] = callback;
    },
    document: {
      getElementById: (id) => elements.get(id),
      querySelector: (selector) =>
        selector.startsWith("meta") ? { content: "test-token" } : kind,
      createElement: (tag) => new Element(tag),
      createTextNode: (text) => {
        const node = new Element("#text");
        node.textContent = text;
        return node;
      },
    },
    fetch: (...args) => fetcher(...args),
    AbortController,
    setTimeout: (callback, delay) => {
      timers.set(++timerId, { callback, delay });
      return timerId;
    },
    clearTimeout: (id) => timers.delete(id),
  });
  vm.runInContext(
    readFileSync("src/lambda_harness/web/app.js", "utf8"),
    context,
  );
  await settle();
  vm.runInContext("selected = 'example'", context);
  return {
    elements,
    kind,
    timers,
    location,
    navigation,
    back: async (path) => {
      location.pathname = path;
      await listeners.popstate();
    },
    run: (code) => vm.runInContext(code, context),
    fetch: (fn) => {
      fetcher = fn;
    },
  };
}

test("standalone forms submit their own scope without feature-only options", async () => {
  const ui = await app();
  const submitted = [];
  ui.fetch(async (path, options) => {
    if (path === "/api/command") {
      submitted.push(JSON.parse(options.body));
      return response({ accepted: true });
    }
    return response(snapshot());
  });
  ui.kind.value = "review";
  ui.elements.get("new-form").onchange();
  assert.equal(ui.elements.get("review-options").hidden, false);
  assert.equal(ui.elements.get("test-options").hidden, true);
  assert.equal(ui.elements.get("test-criteria").required, false);
  ui.elements.get("task-name").value = "review-parser";
  ui.elements.get("reviewers").value = "both";
  ui.elements.get("review-head").value =
    "https://github.com/example/project/pull/12";
  ui.elements.get("review-head").oninput();
  assert.equal(ui.elements.get("base").disabled, true);
  assert.equal(ui.elements.get("base").required, false);
  ui.elements.get("new-form").onsubmit({ preventDefault() {} });
  await settle();
  assert.equal(submitted[0].command, "review");
  assert.equal(submitted[0].head, "https://github.com/example/project/pull/12");
  assert.equal(submitted[0].reviewers, "both");
  assert(!("walkthrough" in submitted[0]));
  assert(!("criteria" in submitted[0]));
  assert.match(submitted[0].request, /Review this change/);

  ui.kind.value = "tests";
  ui.elements.get("new-form").onchange();
  assert.equal(ui.elements.get("base").disabled, false);
  assert.equal(ui.elements.get("base").required, true);
  assert.equal(ui.elements.get("test-options").hidden, false);
  assert.equal(ui.elements.get("review-head").required, false);
  ui.elements.get("test-packages").value = "library, app";
  ui.elements.get("test-criteria").value =
    "Accept valid input\n\nReject empty input";
  ui.elements.get("test-expectation").value = "green";
  ui.elements.get("request").value = "Cover the parser";
  ui.elements.get("new-form").onsubmit({ preventDefault() {} });
  await settle();
  assert.equal(submitted[1].command, "tests");
  assert.deepEqual(submitted[1].criteria, [
    "Accept valid input",
    "Reject empty input",
  ]);
  assert.deepEqual(submitted[1].packages, ["library", "app"]);
  assert.equal(submitted[1].expectation, "green");
  assert(!("head" in submitted[1]));
  assert(!("walkthrough" in submitted[1]));
});

test("general chat submits a chat task and renders plain conversation without approval controls", async () => {
  const ui = await app();
  let posted;
  ui.fetch(async (path, options) => {
    if (path === "/api/command") {
      posted = JSON.parse(options.body);
      return response({ accepted: true });
    }
    return response(snapshot());
  });
  ui.kind.value = "chat";
  ui.elements.get("new-form").onchange();
  assert.equal(ui.elements.get("review-options").hidden, true);
  assert.equal(ui.elements.get("test-options").hidden, true);
  assert.equal(ui.elements.get("walkthrough-option").hidden, true);
  assert.equal(ui.elements.get("start-task").textContent, "Start chat →");
  ui.elements.get("task-name").value = "conversation";
  ui.elements.get("request").value = "Explain pure functions";
  ui.elements.get("new-form").onsubmit({ preventDefault() {} });
  await settle();
  assert.equal(posted.command, "chat");
  assert.equal(posted.request, "Explain pure functions");
  assert(!("walkthrough" in posted));
  ui.run(`state.task.stage = "chat"; state.task.task_kind = "chat";
    state.task.messages = [{role: "chat", answer: "A pure function has no effects", references: []}, {text: "Tell me more"}, {role: "chat", answer: "Results depend on inputs", references: ["Example.hs:3"]}];
    state.task.prompt = {actions: ["answer"], references: ["Example.hs:3"]}; render();`);
  assert.match(
    ui.elements.get("history").textContent,
    /A pure function has no effects.*Tell me more.*Results depend on inputs/s,
  );
  assert.equal(ui.elements.get("packet").hidden, true);
  assert.equal(ui.elements.get("composer").hidden, false);
  assert.equal(ui.elements.get("message-action").hidden, true);
  assert.equal(ui.elements.get("composer-label").textContent, "Message");
  assert.equal(ui.elements.get("actions").children.length, 0);
  assert.match(
    ui.elements.get("exploration-notes").textContent,
    /Example.hs:3/,
  );
});

test("standalone review history shows findings once and keeps the finish action clear", async () => {
  const ui = await app();
  ui.run(`state.task.task_kind = "review";
    state.task.stage = "standalone_review";
    state.task.prompt.reviews = {haskell_reviewer: {summary: "Checked parser boundaries", findings: [{title: "Empty input crashes", severity: "high", location: "Parser.hs:12", impact: "Requests fail", sequence: [], evidence: "Uses head", recommendation: "Handle []", conditions: "Empty input", id: "HASKELL-1", confidence: "confirmed"}]}};
    state.task.messages = [{reviews: state.task.prompt.reviews}];
    render();`);
  assert.match(ui.elements.get("history").textContent, /Empty input crashes/);
  assert.doesNotMatch(
    ui.elements.get("packet").textContent,
    /Empty input crashes/,
  );
  assert.match(ui.elements.get("actions").textContent, /Finish review/);
  assert.doesNotMatch(
    ui.elements.get("actions").textContent,
    /Approve|Fix selected/,
  );
});

test("history is visible and updates when only saved messages change", async () => {
  const ui = await app();
  const history = ui.elements.get("history");
  assert(!history.children.some((n) => n.tagName === "details" && !n.open));
  ui.run("state.task.messages.push({text: 'A new follow-up'}); render()");
  assert.match(history.textContent, /A new follow-up/);
});

test("sending shows the question immediately and prevents duplicate submissions", async () => {
  const ui = await app();
  let finishPost;
  let posts = 0;
  ui.fetch(async (path) => {
    if (path === "/api/command") {
      posts++;
      return new Promise((resolve) => {
        finishPost = resolve;
      });
    }
    const saved = snapshot();
    saved.job = { id: "job", task_id: "example", running: true, log: [] };
    return response(saved);
  });
  ui.elements.get("message").value = "What about multiline\ninput?";
  const first = ui.run("respond('question')");
  assert.match(
    ui.elements.get("history").textContent,
    /What about multiline\ninput\?/,
  );
  assert.equal(ui.elements.get("send").disabled, true);
  await ui.run("respond('question')");
  assert.equal(posts, 1);
  finishPost(response({ accepted: true }));
  await first;
  assert.match(ui.elements.get("history").textContent, /What about multiline/);
  ui.run(
    "state.task.messages.push({text: 'What about multiline\\ninput?'}); render()",
  );
  assert.equal(
    ui.elements.get("history").textContent.split("What about multiline").length,
    2,
  );
});

test("a failed poll retries and keeps the draft", async () => {
  const ui = await app();
  ui.elements.get("message").value = "Unsent draft";
  ui.fetch(async () => {
    throw new Error("Temporary connection failure");
  });
  await ui.run("refresh()");
  assert.match(ui.elements.get("error").textContent, /retry/i);
  assert.equal(ui.elements.get("message").value, "Unsent draft");
  assert([...ui.timers.values()].some(({ delay }) => delay === 2000));
  ui.fetch(async () => response(snapshot()));
  await ui.run("refresh()");
  assert.equal(ui.elements.get("error").hidden, true);
  assert.equal(ui.elements.get("send").disabled, false);
});

test("late responses cannot replace the task selected more recently", async () => {
  const ui = await app();
  let oldResponse;
  ui.fetch(async (path) =>
    path.includes("task=one")
      ? new Promise((resolve) => {
          oldResponse = resolve;
        })
      : response(snapshot(task("two"))),
  );
  const old = ui.run("selected = 'one'; refresh()");
  await ui.run("selected = 'two'; refresh()");
  oldResponse(response(snapshot(task("one"))));
  await old;
  assert.equal(ui.elements.get("title").textContent, "two");
});

test("a stalled request times out and polling recovers without discarding text", async () => {
  const ui = await app();
  ui.elements.get("message").value = "Keep my draft";
  ui.fetch(
    (path, options) =>
      new Promise((resolve, reject) => {
        options.signal.addEventListener("abort", () =>
          reject(new Error("Aborted")),
        );
      }),
  );
  const pending = ui.run("refresh()");
  const deadline = [...ui.timers.values()].find(({ delay }) => delay === 10000);
  assert(deadline);
  deadline.callback();
  await pending;
  assert.match(ui.elements.get("error").textContent, /did not respond/);
  assert.equal(ui.elements.get("message").value, "Keep my draft");
  assert([...ui.timers.values()].some(({ delay }) => delay === 2000));
});

test("an unsuccessful submission leaves the question available for correction", async () => {
  const ui = await app();
  ui.elements.get("message").value = "Keep this follow-up";
  ui.fetch(async () => ({
    ok: false,
    json: async () => ({ error: "Action is not available" }),
  }));
  await assert.rejects(
    ui.run("respond('question')"),
    /Action is not available/,
  );
  assert.equal(ui.elements.get("message").value, "Keep this follow-up");
  assert.match(
    ui.elements.get("history").textContent,
    /Delivery not confirmed/,
  );
  assert.equal(ui.elements.get("send").disabled, false);
});

function theme(saved, blocked = false, legacy = null) {
  const picker = {
    value: "",
    addEventListener: (_, fn) => {
      picker.change = fn;
    },
  };
  const root = { dataset: {} };
  let stored = saved;
  let writtenKey = null;
  vm.runInNewContext(readFileSync("src/lambda_harness/web/theme.js", "utf8"), {
    document: { documentElement: root, querySelectorAll: () => [picker] },
    localStorage: {
      getItem(key) {
        if (blocked) throw new Error("Storage unavailable");
        return key === "haskell-harness-theme" ? legacy : stored;
      },
      setItem(key, value) {
        if (blocked) throw new Error("Storage unavailable");
        writtenKey = key;
        stored = value;
      },
    },
  });
  return { root, picker, saved: () => stored, writtenKey: () => writtenKey };
}

test("appearance survives the product rename and saves under the new name", () => {
  const ui = theme(null, false, "dark");
  assert.equal(ui.root.dataset.theme, "dark");
  assert.equal(theme("light", false, "dark").root.dataset.theme, "light");
  ui.picker.value = "light";
  ui.picker.change();
  assert.equal(ui.writtenKey(), "lambda-harness-theme");
});

test("appearance restores the saved choice and remembers changes", () => {
  const ui = theme("dark");
  assert.equal(ui.root.dataset.theme, "dark");
  assert.equal(ui.picker.value, "dark");
  ui.picker.value = "light";
  ui.picker.change();
  assert.equal(ui.root.dataset.theme, "light");
  assert.equal(ui.saved(), "light");
  ui.picker.value = "system";
  ui.picker.change();
  assert.equal(ui.root.dataset.theme, "system");
  assert.equal(ui.saved(), "system");
});

test("appearance follows the system initially and works without browser storage", () => {
  assert.equal(theme("unknown").root.dataset.theme, "system");
  const ui = theme(null, true);
  assert.equal(ui.root.dataset.theme, "system");
  ui.picker.value = "dark";
  ui.picker.change();
  assert.equal(ui.root.dataset.theme, "dark");
});

function memoryFixture(ui) {
  const requests = [];
  const versions = {
    original: {
      id: "original",
      revision: "abc123",
      created: "2026-10-20",
      notes: "Original rules",
      documents: [{ path: "docs/design.md", content: "Saved design" }],
    },
    older: {
      id: "older",
      revision: "def456",
      created: "2026-10-19",
      notes: "Old rules",
      documents: [],
    },
  };
  let current = "original",
    conflict = false;
  ui.fetch(async (path, options) => {
    const body = options.body && JSON.parse(options.body);
    requests.push([path, body]);
    if (path === "/api/agents") return response(agentCatalog());
    if (path === "/api/project")
      return response({
        backend: "auto",
        backends: [],
        tools: { git: true, nix: true, flake: true },
        context: { latest: current, history: Object.values(versions) },
      });
    if (path.startsWith("/api/context?snapshot="))
      return response(versions[path.split("=")[1]]);
    if (path === "/api/context/save") {
      if (conflict)
        return {
          ok: false,
          json: async () => ({
            error: "Project memory changed in another session.",
          }),
        };
      current = "saved";
      versions.saved = {
        ...versions.original,
        id: current,
        notes: body.notes,
        documents: body.sources.map((path) => ({
          path,
          content: "Saved file",
        })),
      };
      return response(versions.saved);
    }
    if (path === "/api/context/restore") {
      current = body.snapshot;
      return response(versions[current]);
    }
    if (path.startsWith("/api/context/preview?"))
      return response({
        revision: "abc123",
        context: {
          id: current,
          notes: versions[current].notes,
          documents: [
            {
              path: "docs/design.md",
              sha256: "test",
              purpose: "Project reference",
            },
          ],
          missing_sources: [],
          usage: "Reference data, not instructions.",
        },
      });
    return response(
      snapshot({
        ...task(),
        can_use_context: true,
        project_context_id: "older",
      }),
    );
  });
  return {
    requests,
    conflict: () => {
      conflict = true;
    },
  };
}

test("memory loads existing notes and saves a multiline draft without starting an agent", async () => {
  const ui = await app(),
    fixture = memoryFixture(ui);
  await ui.elements.get("project-memory").onclick();
  assert.equal(ui.elements.get("context-notes").value, "Original rules");
  assert.equal(ui.elements.get("context-sources").value, "docs/design.md");
  assert.match(
    ui.elements.get("context-documents").textContent,
    /Saved design/,
  );
  ui.elements.get("context-notes").value = "# Decisions\nNew rule";
  ui.elements.get("context-notes").oninput();
  ui.elements.get("context-sources").value =
    "docs/design.md\n\nsrc/Example.hs\n";
  await ui.elements.get("save-context").onclick();
  assert.deepEqual(
    fixture.requests.find(([path]) => path === "/api/context/save")[1],
    {
      notes: "# Decisions\nNew rule",
      sources: ["docs/design.md", "src/Example.hs"],
      expected_current: "original",
      refresh_sources: false,
    },
  );
  assert(!fixture.requests.some(([path]) => path === "/api/command"));
  assert.equal(ui.elements.get("context-snapshot").value, "saved");
  assert.equal(ui.elements.get("send").disabled, false);
});

test("memory drafts survive polling, panel changes, and conflicting saves", async () => {
  const ui = await app(),
    fixture = memoryFixture(ui);
  await ui.elements.get("project-memory").onclick();
  ui.elements.get("context-notes").value = "Keep my draft";
  ui.elements.get("context-notes").oninput();
  await ui.run("refresh()");
  await ui.elements.get("project-tools").onclick();
  await ui.elements.get("project-memory").onclick();
  assert.equal(ui.elements.get("context-notes").value, "Keep my draft");
  ui.elements.get("context-snapshot").value = "older";
  await ui.elements.get("read-context").onclick();
  assert.match(ui.elements.get("error").textContent, /Save your memory draft/);
  assert.equal(ui.elements.get("context-notes").value, "Keep my draft");
  fixture.conflict();
  await ui.elements.get("save-context").onclick();
  assert.match(
    ui.elements.get("error").textContent,
    /changed in another session/,
  );
  assert.equal(ui.elements.get("context-notes").value, "Keep my draft");
  assert.equal(ui.elements.get("preview-context").disabled, true);
  assert.equal(ui.elements.get("use-context").disabled, true);
  await ui.elements.get("discard-context").onclick();
  assert.equal(ui.elements.get("context-notes").value, "Original rules");
});

test("earlier memory is read only until explicitly restored and previews show stale sources", async () => {
  const ui = await app(),
    fixture = memoryFixture(ui);
  await ui.elements.get("project-memory").onclick();
  ui.elements.get("context-snapshot").value = "older";
  await ui.elements.get("read-context").onclick();
  assert.equal(ui.elements.get("context-notes").value, "Old rules");
  assert.equal(ui.elements.get("context-notes").readOnly, true);
  assert.equal(ui.elements.get("save-context").disabled, true);
  assert.equal(ui.elements.get("restore-context").disabled, false);
  await ui.elements.get("restore-context").onclick();
  assert.deepEqual(
    fixture.requests.find(([path]) => path === "/api/context/restore")[1],
    { snapshot: "older", expected_current: "original" },
  );
  assert.equal(ui.elements.get("context-notes").readOnly, false);
  await ui.elements.get("preview-context").onclick();
  assert.match(ui.elements.get("context-preview").textContent, /Old rules/);
  assert.match(ui.elements.get("context-preview").textContent, /no excerpt/);
  await ui.elements.get("preview-task-context").onclick();
  assert(
    fixture.requests.some(
      ([path]) => path === "/api/context/preview?task=example",
    ),
  );
  assert(!fixture.requests.some(([path]) => path === "/api/context/use"));
});

test("a late preview cannot replace a new memory draft", async () => {
  const ui = await app();
  memoryFixture(ui);
  await ui.elements.get("project-memory").onclick();
  let finish;
  ui.fetch(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const preview = ui.elements.get("preview-context").onclick();
  ui.elements.get("context-notes").value = "New draft";
  ui.elements.get("context-notes").oninput();
  finish(
    response({
      revision: "abc123",
      context: { id: "old", notes: "Old preview", documents: [] },
    }),
  );
  await preview;
  assert.equal(ui.elements.get("context-preview").textContent, "");
  assert.equal(ui.elements.get("context-notes").value, "New draft");
});

test("chat displays the committed memory receipt and opens that exact version", async () => {
  const ui = await app();
  memoryFixture(ui);
  ui.run(
    `state.task.messages = [{role: "chat", answer: "Here is the note.", references: [], memory_save: {status: "saved", note: "Original rules", snapshot: "original"}}]; render();`,
  );
  const history = ui.elements.get("history");
  assert.match(
    history.textContent,
    /Saved to project memory.*Original rules.*Version original/s,
  );
  const receipt = history.children[1].children.at(-1);
  await receipt.children.find((item) => item.tagName === "button").onclick();
  assert.equal(ui.elements.get("memory-panel").hidden, false);
  assert.equal(ui.elements.get("context-snapshot").value, "original");
  assert.equal(ui.elements.get("context-notes").value, "Original rules");
});

test("a failed chat save displays the reason and proposed note without a saved-version link", async () => {
  const ui = await app();
  ui.run(
    `state.task.messages = [{role: "chat", answer: "The note was not saved.", references: [], memory_save: {status: "error", note: "Keep the core pure", error: "Project notes would exceed 32,000 UTF-8 bytes"}}]; render();`,
  );
  const history = ui.elements.get("history");
  assert.match(
    history.textContent,
    /Memory not saved.*Keep the core pure.*32,000/s,
  );
  assert(!history.textContent.includes("Saved to project memory"));
  const receipt = history.children[1].children.at(-1);
  assert(!receipt.children.some((item) => item.tagName === "button"));
});

test("opening a memory receipt preserves an unsaved editor draft", async () => {
  const ui = await app();
  memoryFixture(ui);
  await ui.elements.get("project-memory").onclick();
  ui.elements.get("context-notes").value = "Unfinished edit";
  ui.elements.get("context-notes").oninput();
  ui.run(
    `state.task.messages = [{role: "chat", answer: "Here is the note.", references: [], memory_save: {status: "saved", note: "Old rules", snapshot: "older"}}]; render();`,
  );
  const receipt = ui.elements.get("history").children[1].children.at(-1);
  await receipt.children.find((item) => item.tagName === "button").onclick();
  assert.equal(ui.elements.get("context-notes").value, "Unfinished edit");
  assert.match(ui.elements.get("error").textContent, /Save your memory draft/);
});

const agentProfile = {
  id: "rust-reviewer",
  version: "0123456789abcdef",
  path: "agents/rust-reviewer.json",
  definition: {
    name: "Rust reviewer",
    role: "reviewer",
    avatar: "crab",
    languages: ["Rust"],
    expertise: ["Ownership", "Async cancellation"],
    instructions: "Review lifetimes and resource cleanup.",
  },
  assignment_problem: null,
};
const agentCatalog = () => ({
  roles: { reviewer: "Code reviewer" },
  profiles: [agentProfile],
  selected: {},
  repository: "/catalog",
  writable: true,
});

test("Agents is a separate catalog page with portraits, search, and full instructions", async () => {
  const ui = await app();
  ui.fetch(async (path) =>
    response(path === "/api/agents" ? agentCatalog() : snapshot()),
  );
  await ui.run("openAgents()");
  assert.equal(ui.elements.get("agents-page").hidden, false);
  assert.equal(ui.elements.get("work").hidden, true);
  assert.equal(ui.elements.get("new-panel").hidden, true);
  assert.equal(ui.elements.get("title").textContent, "Agents");
  assert.equal(ui.elements.get("agent-grid").children.length, 1);
  assert.match(ui.elements.get("agent-grid").textContent, /Rust reviewer/);
  const card = ui.elements.get("agent-grid").children[0];
  assert.match(card.children[0].children[0].src, /^data:image\/svg\+xml,/);
  assert.equal(card.children[0].children[0].alt, "Rust reviewer avatar");
  await card.children.at(-1).children[0].onclick();
  assert.equal(ui.elements.get("agent-details").hidden, false);
  assert.match(
    ui.elements.get("agent-details").textContent,
    /Review lifetimes/,
  );
  ui.elements.get("agent-search").value = "Haskell";
  ui.elements.get("agent-search").oninput();
  assert.match(ui.elements.get("agent-grid").textContent, /No agents match/);
});

test("Add and Edit start guided chat and preserve instructions during polling", async () => {
  const ui = await app(),
    commands = [];
  ui.fetch(async (path, options) => {
    if (path === "/api/agents") return response(agentCatalog());
    if (path === "/api/command") {
      commands.push(JSON.parse(options.body));
      return response({ accepted: true });
    }
    return response(snapshot());
  });
  await ui.run("openAgents()");
  ui.elements.get("add-agent").onclick();
  ui.elements.get("agent-request").value =
    "Help me define a Rust reviewer\nI need guidance.";
  await ui.run("refresh()");
  assert.match(ui.elements.get("agent-request").value, /I need guidance/);
  ui.elements.get("agent-form").onsubmit({ preventDefault() {} });
  await settle();
  assert.equal(commands[0].command, "agent_builder");
  assert.equal(commands[0].agent_id, null);
  assert.match(commands[0].request, /\nI need guidance/);
  assert.equal(ui.elements.get("agents-page").hidden, true);
  await ui.run("openAgents()");
  await ui.elements
    .get("agent-grid")
    .children[0].children.at(-1)
    .children[1].onclick();
  assert.equal(
    ui.elements.get("agent-create-title").textContent,
    "Edit Rust reviewer",
  );
  ui.elements.get("agent-request").value = "Focus on cancellation.";
  ui.elements.get("agent-form").onsubmit({ preventDefault() {} });
  await settle();
  assert.equal(commands[1].agent_id, "rust-reviewer");
});

test("agent conversations stay in Agents and explicit save carries the approval token", async () => {
  const ui = await app(),
    commands = [];
  const builder = {
    ...task("builder"),
    task_kind: "agent_builder",
    stage: "agent_builder",
    messages: [
      { role: "agent_builder", answer: "I recommend ownership checks." },
    ],
    prompt: {
      actions: ["answer", "save_agent"],
      definition: agentProfile.definition,
      approval_token: "reviewed-draft",
    },
  };
  const data = {
    ...snapshot(builder),
    tasks: [
      {
        task_id: "builder",
        task_kind: "agent_builder",
        display_name: "Rust reviewer",
        stage: "agent_builder",
      },
    ],
  };
  ui.fetch(async (path, options) => {
    if (path === "/api/command") {
      commands.push(JSON.parse(options.body));
      return response({ accepted: true });
    }
    return response(data);
  });
  ui.run("selected = 'builder'");
  await ui.run("refresh()");
  assert.doesNotMatch(ui.elements.get("tasks").textContent, /builder/);
  assert.match(ui.elements.get("agent-drafts").textContent, /Rust reviewer/);
  assert.match(ui.elements.get("history").textContent, /I recommend ownership/);
  assert.match(ui.elements.get("packet").textContent, /Full instructions/);
  const save = ui.elements
    .get("actions")
    .children.find((node) => node.textContent === "Save to repository");
  assert(save);
  await save.onclick();
  assert.equal(commands[0].action, "save_agent");
  assert.equal(commands[0].token, "reviewed-draft");
});

test("bundled catalogs require a checkout before editing and saving source uses the explicit path", async () => {
  const ui = await app(),
    changes = [];
  ui.fetch(async (path, options) => {
    if (path === "/api/agents")
      return response({ ...agentCatalog(), writable: false, repository: null });
    if (path === "/api/agents/source") {
      changes.push(JSON.parse(options.body));
      return response({});
    }
    return response(snapshot());
  });
  await ui.run("openAgents()");
  assert.equal(ui.elements.get("add-agent").disabled, true);
  assert.equal(ui.elements.get("agent-source").open, true);
  ui.elements.get("agent-repository").value = "/team/agent-repo";
  await ui.elements.get("use-agent-repository").onclick();
  assert.deepEqual(changes, [{ repository: "/team/agent-repo" }]);
});

test("a removed agent stays visible as unavailable so its assignment can be reset", async () => {
  const ui = await app(),
    changes = [];
  ui.fetch(async (path, options) => {
    if (path === "/api/agents")
      return response({
        ...agentCatalog(),
        profiles: [],
        selected: { reviewer: "removed" },
      });
    if (path === "/api/agents/assign") {
      changes.push(JSON.parse(options.body));
      return response({ saved: true });
    }
    return response(snapshot());
  });
  await ui.run("openAgents()");
  const select = ui.elements.get("agent-assignments").children[0].children[0];
  assert.equal(select.value, "removed");
  assert.match(select.textContent, /Unavailable agent/);
  select.value = "";
  await select.onchange();
  assert.deepEqual(changes, [{ role: "reviewer", agent_id: null }]);
});

function assertOnlyPage(ui, visible, title) {
  for (const id of [
    "agents-page",
    "memory-panel",
    "project-panel",
    "work",
    "new-panel",
  ])
    assert.equal(
      ui.elements.get(id).hidden,
      id !== visible,
      `${id} on ${title}`,
    );
  assert.equal(ui.elements.get("title").textContent, title);
  for (const id of ["activity", "selection", "choose-backend"])
    assert.equal(
      ui.elements.get(id).hidden,
      true,
      `${id} should not appear on ${title}`,
    );
}

test("project pages replace chat and remain separate while work polls in the background", async () => {
  const ui = await app();
  memoryFixture(ui);
  ui.elements.get("message").value = "Keep my unsent question";
  ui.run(
    "state.job = {id:'running', task_id:'example', running:true, log:['Working…'], selection:{available_backends:[], backend_checks:[]}}; render()",
  );
  await ui.elements.get("project-memory").onclick();
  assertOnlyPage(ui, "memory-panel", "Project memory");
  assert.equal(ui.location.pathname, "/project-memory");
  ui.elements.get("context-notes").value = "Unsaved project notes";
  ui.elements.get("context-notes").oninput();
  await ui.run("refresh()");
  assertOnlyPage(ui, "memory-panel", "Project memory");
  await ui.elements.get("project-tools").onclick();
  assertOnlyPage(ui, "project-panel", "Project settings");
  assert.equal(ui.location.pathname, "/project-settings");
  await ui.run("openAgents()");
  assertOnlyPage(ui, "agents-page", "Agents");
  await ui.run("refresh()");
  assertOnlyPage(ui, "agents-page", "Agents");
  await ui.back("/project-memory");
  assertOnlyPage(ui, "memory-panel", "Project memory");
  assert.equal(ui.elements.get("context-notes").value, "Unsaved project notes");
  await ui.elements.get("close-memory").onclick();
  assert.equal(ui.location.pathname, "/");
  assert.equal(ui.elements.get("work").hidden, false);
  assert.equal(ui.elements.get("memory-panel").hidden, true);
  assert.equal(ui.elements.get("message").value, "Keep my unsent question");
  await ui.elements.get("project-tools").onclick();
  await ui.elements.get("tasks").children[0].onclick();
  assert.equal(ui.elements.get("project-panel").hidden, true);
  assert.equal(ui.elements.get("work").hidden, false);
  assert.equal(ui.elements.get("message").value, "Keep my unsent question");
});

test("all project page URLs load directly and browser history loads the matching page", async () => {
  const project = {
    backend: "auto",
    backends: [],
    tools: { git: true, nix: true, flake: true },
    context: { latest: null, history: [] },
  };
  const fetcher = async (path) =>
    response(
      path === "/api/agents"
        ? agentCatalog()
        : path === "/api/project"
          ? project
          : { ...snapshot(), tasks: [], task: null },
    );
  for (const [path, id, title] of [
    ["/agents", "agents-page", "Agents"],
    ["/project-memory", "memory-panel", "Project memory"],
    ["/project-settings", "project-panel", "Project settings"],
  ]) {
    const ui = await app({ pathname: path, fetcher });
    assertOnlyPage(ui, id, title);
    assert.equal(ui.elements.get("error").hidden, true);
    await ui.back("/project-settings");
    assertOnlyPage(ui, "project-panel", "Project settings");
    await ui.back("/agents");
    assertOnlyPage(ui, "agents-page", "Agents");
    assert.equal(ui.elements.get("agent-grid").children.length, 1);
    await ui.back("/project-memory");
    assertOnlyPage(ui, "memory-panel", "Project memory");
    assert.equal(ui.elements.get("context-notes").value, "");
  }
});

test("project memory and settings identify their owner and use project-specific links", async () => {
  const identity = {
    id: "project-a",
    name: "example-app",
    repository: "/code/example-app",
  };
  const project = {
    project: identity,
    backend: "auto",
    backend_origin: "project",
    backends: [],
    tools: { git: true, nix: true, flake: true },
    context: { latest: null, history: [] },
  };
  const ui = await app({
    fetcher: async (path) =>
      response(
        path === "/api/project"
          ? project
          : path === "/api/agents"
            ? { ...agentCatalog(), project: identity }
            : {
                ...snapshot(),
                project: identity,
                repository: identity.repository,
              },
      ),
  });
  assert.equal(ui.elements.get("project-name").textContent, "example-app");
  assert.equal(
    ui.elements.get("project-memory").href,
    "/projects/project-a/memory",
  );
  assert.equal(
    ui.elements.get("project-tools").href,
    "/projects/project-a/settings",
  );
  await ui.elements.get("project-memory").onclick();
  assert.equal(ui.location.pathname, "/projects/project-a/memory");
  assert.match(ui.elements.get("title").textContent, /example-app.*Memory/);
  assert.match(
    ui.elements.get("memory-project").textContent,
    /\/code\/example-app/,
  );
  await ui.elements.get("project-tools").onclick();
  assert.equal(ui.location.pathname, "/projects/project-a/settings");
  assert.match(ui.elements.get("title").textContent, /example-app.*Settings/);
  assert.match(
    ui.elements.get("settings-project").textContent,
    /\/code\/example-app/,
  );
  assert.match(
    ui.elements.get("backend-preference-status").textContent,
    /Saved for this project/,
  );
  await ui.back("/projects/project-a/memory");
  assert.equal(ui.elements.get("memory-panel").hidden, false);
  assert.equal(ui.elements.get("project-panel").hidden, true);
  await ui.run("openAgents()");
  assert.equal(ui.location.pathname, "/agents");
  assert.equal(ui.elements.get("title").textContent, "Agents");
  assert.match(
    ui.elements.get("agent-project-title").textContent,
    /example-app/,
  );
});

test("project bookmarks load directly and reject another project's identity", async () => {
  const identity = {
    id: "project-a",
    name: "example-app",
    repository: "/code/example-app",
  };
  const project = {
    project: identity,
    backend: "auto",
    backend_origin: "project",
    backends: [],
    tools: { git: true, nix: true, flake: true },
    context: { latest: null, history: [] },
  };
  const requests = [];
  const fetcher = async (path) => {
    requests.push(path);
    return response(
      path === "/api/project" ? project : { ...snapshot(), project: identity },
    );
  };
  const ui = await app({ pathname: "/projects/project-a/memory", fetcher });
  assert.equal(ui.elements.get("memory-panel").hidden, false);
  assert.match(ui.elements.get("title").textContent, /example-app.*Memory/);
  requests.length = 0;
  await ui.back("/projects/project-b/settings");
  assert.equal(ui.elements.get("project-panel").hidden, true);
  assert.match(ui.elements.get("error").textContent, /another project/);
  assert.deepEqual(requests, []);
  await ui.elements.get("project-tools").onclick();
  assert.equal(ui.location.pathname, "/projects/project-a/settings");
  assert.equal(ui.elements.get("project-panel").hidden, false);
  assert.equal(ui.elements.get("error").hidden, true);
});
