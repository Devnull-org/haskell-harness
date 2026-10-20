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

async function app() {
  const elements = new Map();
  const html = readFileSync("src/haskell_harness/web/index.html", "utf8");
  for (const [, id] of html.matchAll(/id="([^"]+)"/g))
    elements.set(id, new Element());
  elements.get("new-form").append(new Element("button"));
  let fetcher = async () => response(snapshot());
  let timerId = 0;
  const timers = new Map();
  const context = vm.createContext({
    document: {
      getElementById: (id) => elements.get(id),
      querySelector: () => ({ content: "test-token", value: "explore" }),
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
    readFileSync("src/haskell_harness/web/app.js", "utf8"),
    context,
  );
  await settle();
  vm.runInContext("selected = 'example'", context);
  return {
    elements,
    timers,
    run: (code) => vm.runInContext(code, context),
    fetch: (fn) => {
      fetcher = fn;
    },
  };
}

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

function theme(saved, blocked = false) {
  const picker = {
    value: "",
    addEventListener: (_, fn) => {
      picker.change = fn;
    },
  };
  const root = { dataset: {} };
  let stored = saved;
  vm.runInNewContext(readFileSync("src/haskell_harness/web/theme.js", "utf8"), {
    document: { documentElement: root, querySelectorAll: () => [picker] },
    localStorage: {
      getItem() {
        if (blocked) throw new Error("Storage unavailable");
        return stored;
      },
      setItem(_, value) {
        if (blocked) throw new Error("Storage unavailable");
        stored = value;
      },
    },
  });
  return { root, picker, saved: () => stored };
}

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

test("project controls save multiline source paths without starting an agent", async () => {
  const ui = await app();
  const requests = [];
  ui.fetch(async (path, options) => {
    requests.push([path, options.body && JSON.parse(options.body)]);
    if (path === "/api/context")
      return response({ id: "snapshot", documents: [] });
    if (path === "/api/project")
      return response({
        backend: "auto",
        backends: [],
        tools: { git: true, nix: true, flake: true },
        context: {
          latest: "snapshot",
          history: [
            { id: "snapshot", revision: "abc123", created: "2026-10-20" },
          ],
        },
      });
    return response(snapshot());
  });
  ui.elements.get("context-sources").value =
    "docs/design.md\n\nsrc/Example.hs\n";
  await ui.elements.get("save-context").onclick();
  assert.deepEqual(requests.find(([path]) => path === "/api/context")[1], {
    sources: ["docs/design.md", "src/Example.hs"],
  });
  assert(!requests.some(([path]) => path === "/api/command"));
  assert.equal(ui.elements.get("context-snapshot").value, "snapshot");
  assert.equal(ui.elements.get("send").disabled, false);
});
