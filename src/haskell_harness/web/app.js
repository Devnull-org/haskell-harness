"use strict";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="harness-token"]').content;
let selected = null,
  state = null,
  timer = null,
  seenJob = null,
  deletePlan = null,
  packetKey = "",
  historyKey = "",
  historyTask = null,
  pendingMessage = null,
  submitting = false,
  refreshId = 0,
  connectionError = null;
const labels = {
  answer: "Answer",
  question: "Ask a question",
  revise: "Revise instructions",
  approve: "Approve",
  retry: "Retry unchanged",
  recheck: "Run checks again",
  reject: "Reject changes",
  skip: "Skip",
  done: "Finish exploration",
  fix: "Fix selected findings",
  dismiss: "Dismiss selected findings",
  accept_red: "Accept expected test failure",
};
const phaseLabels = {
  explore: "Explore",
  interview: "Requirements interview",
  tests: "Write failing tests",
  implement: "Implement",
  refactor: "Refactor",
  "review-haskell": "Haskell review",
  "review-security": "Security review",
  walkthrough: "Code walkthrough",
  handoff: "Prepare local handoff",
};
const textActions = ["answer", "question", "revise"];
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function button(text, action, cls) {
  const node = el("button", text, cls);
  node.onclick = () => guard(action);
  return node;
}
function error(message) {
  $("error").textContent = message || "";
  $("error").hidden = !message;
}
async function guard(action) {
  try {
    error("");
    await action();
  } catch (e) {
    error(e.message);
  }
}
async function api(path, body) {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 10000);
  const options = {
    headers: { "X-Harness-Token": token },
    signal: controller.signal,
  };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  try {
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed");
    return data;
  } catch (e) {
    if (controller.signal.aborted)
      throw new Error(
        "The server did not respond. Check status before sending again.",
      );
    throw e;
  } finally {
    clearTimeout(deadline);
  }
}
function prose(text) {
  const node = el("div", undefined, "prose");
  let code = null;
  for (const line of String(text).split("\n")) {
    if (line.startsWith("```")) {
      if (code) {
        node.append(code);
        code = null;
      } else code = el("pre", "");
      continue;
    }
    if (code) {
      code.textContent += line + "\n";
      continue;
    }
    const heading = line.match(/^#{1,4}\s+(.+)/);
    if (heading) node.append(el("h3", heading[1]));
    else node.append(document.createTextNode(line + "\n"));
  }
  if (code) node.append(code);
  return node;
}
function section(parent, title, value) {
  if (
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && !value.length)
  )
    return;
  parent.append(el("h3", title));
  parent.append(
    prose(typeof value === "string" ? value : JSON.stringify(value, null, 2)),
  );
}
function message(who, text, human = false) {
  const node = el("div", undefined, "message" + (human ? " human" : ""));
  node.append(el("div", who, "eyebrow"), prose(text));
  return node;
}
async function command(body) {
  if (submitting) return;
  if (state?.job?.running)
    throw new Error(
      "An operation is still running. Stop it before sending another.",
    );
  submitting = true;
  if (state) render();
  try {
    await api("/api/command", body);
    if (body.command === "start" || body.command === "explore") {
      selected = body.task_id;
      packetKey = "";
    }
    await refresh();
  } finally {
    submitting = false;
    if (state) render();
  }
}
async function refresh() {
  const requestId = ++refreshId,
    taskId = selected;
  clearTimeout(timer);
  try {
    const next = await api(
      "/api/state" + (taskId ? "?task=" + encodeURIComponent(taskId) : ""),
    );
    if (requestId !== refreshId || taskId !== selected) return;
    state = next;
    if (connectionError) error("");
    connectionError = null;
    render();
  } catch (e) {
    if (requestId !== refreshId || taskId !== selected) return;
    connectionError = e.message;
    error("Unable to refresh: " + e.message + " Retrying automatically…");
  } finally {
    if (requestId === refreshId && taskId === selected)
      timer = setTimeout(
        refresh,
        connectionError ? 2000 : state?.job?.running ? 700 : 3000,
      );
  }
}
function render() {
  $("repository").textContent = state.repository;
  $("backend").textContent =
    state.backend === "auto" ? "Automatic backend" : state.backend;
  if (!$("base").value) $("base").value = state.base;
  const running = !!state.job?.running;
  const busy = running || submitting || !!connectionError;
  $("tasks").replaceChildren();
  for (const task of state.tasks) {
    const item = button(
      task.task_id,
      async () => {
        selected = task.task_id;
        packetKey = "";
        $("message").value = "";
        await refresh();
      },
      task.task_id === selected ? "selected" : "",
    );
    item.append(el("small", task.stage.replaceAll("_", " ")));
    $("tasks").append(item);
  }
  if (!state.tasks.length)
    $("tasks").append(
      el("small", "Your saved conversations will appear here.", "muted"),
    );
  $("new-panel").hidden = !!selected;
  $("work").hidden = !state.task;
  $("new-task").disabled = busy;
  $("choose-backend").hidden = !state.task;
  $("choose-backend").disabled = busy;
  $("new-form").querySelector("button").disabled = busy;
  $("title").textContent = selected || "What would you like to work on?";
  if (state.task) renderTask(state.task, busy);
  $("activity").hidden = !state.job;
  if (state.job) {
    const job = state.job;
    $("activity-label").textContent = running
      ? "Working on " + job.task_id
      : job.error
        ? "Operation stopped"
        : "Latest activity";
    $("activity-message").textContent = job.log.at(-1) || "Preparing…";
    $("log").textContent = job.log.join("\n");
    $("stop").hidden = !running;
    $("stop").disabled = !state.can_stop;
    $("stop").textContent = state.stopping
      ? "Stopping…"
      : state.can_stop
        ? "■ Stop model"
        : "Preparing / checking";
    if (!running && seenJob !== job.id) {
      seenJob = job.id;
      if (job.error) error(job.error);
      if (job.result?.stage === "abandoned") {
        selected = null;
        packetKey = "";
        guard(refresh);
        return;
      }
      if (job.result && selected !== job.result.task_id) {
        selected = job.result.task_id;
        packetKey = "";
        guard(refresh);
        return;
      }
    }
  }
  const selection = state.job?.selection;
  $("selection").hidden = !selection;
  if (selection) {
    $("selection-description").textContent = selection.available_backends.length
      ? "More than one backend is ready. Choose one to continue."
      : "No backend is ready. Check the results below, sign in through its CLI if needed, then try again.";
    $("backend-choices").replaceChildren();
    for (const name of selection.available_backends)
      $("backend-choices").append(
        button(
          name,
          () => command({ ...state.job.pending_command, backend: name }),
          "primary",
        ),
      );
    $("backend-checks").textContent = selection.backend_checks
      .map((c) => c.name + ": " + c.message)
      .join("\n");
  }
}
function renderHistory(task) {
  const messages = task.messages || [];
  if (
    pendingMessage?.task_id === task.task_id &&
    messages
      .slice(pendingMessage.after)
      .some((item) => (item.text || item.question) === pendingMessage.text)
  )
    pendingMessage = null;
  const pending =
    pendingMessage?.task_id === task.task_id ? pendingMessage : null;
  const key = JSON.stringify([task.task_id, task.request, messages, pending]);
  if (key === historyKey) return;
  const history = $("history");
  const atBottom =
    history.scrollTop + history.clientHeight >= history.scrollHeight - 60;
  const switched = historyTask !== task.task_id;
  historyKey = key;
  historyTask = task.task_id;
  history.replaceChildren(message("Initial request", task.request, true));
  for (const item of messages) {
    if (item.text) history.append(message("You", item.text, true));
    else if (item.question) {
      history.append(message("You", item.question, true));
      if (item.answer) history.append(message("Agent", item.answer));
    } else if (item.analysis)
      history.append(
        message("Explorer", item.summary + "\n\n" + item.analysis),
      );
  }
  if (pending) {
    const entry = message("You", pending.text, true);
    entry.append(el("small", pending.status, "message-status"));
    history.append(entry);
  }
  if (atBottom || switched || pending?.status === "Sending…")
    history.scrollTop = history.scrollHeight;
}
function renderTask(task, running) {
  const prompt = task.prompt || {},
    actions = prompt.actions || [],
    key = JSON.stringify([task.task_id, task.stage, prompt]);
  $("task-meta").replaceChildren(
    el("strong", task.stage.replaceAll("_", " ")),
    el("div", "Base: " + task.base_ref + " · " + task.base.slice(0, 12)),
    el("div", "Worktree: " + (task.worktree || "Preparing")),
    el("div", "Reports: " + task.report_directory),
  );
  renderHistory(task);
  if (key !== packetKey) {
    packetKey = key;
    const packet = $("packet"),
      notes = $("exploration-notes");
    packet.replaceChildren();
    notes.replaceChildren();
    if (prompt.summary) packet.append(el("h2", prompt.summary));
    if (prompt.error) packet.append(prose(prompt.error));
    if (prompt.guidance) packet.append(prose(prompt.guidance));
    if (prompt.questions)
      for (const q of prompt.questions)
        packet.append(prose(typeof q === "string" ? q : q.question));
    if (prompt.question) packet.append(prose(prompt.question));
    if (prompt.answer) packet.append(prose(prompt.answer));
    if (prompt.exploration) {
      const a = prompt.exploration;
      if (!(task.messages || []).some((item) => item.analysis === a.analysis))
        packet.append(el("h2", a.summary), prose(a.analysis));
      section(notes, "References", a.references.join("\n"));
      section(notes, "Open questions", a.open_questions.join("\n"));
    }
    notes.hidden = !notes.children.length;
    if (prompt.specification) {
      const spec = prompt.specification;
      packet.append(el("h2", spec.problem), prose(spec.expected_behavior));
      section(
        packet,
        "Acceptance criteria",
        spec.acceptance_criteria
          .map((c) => c.id + ": " + c.description)
          .join("\n"),
      );
      section(packet, "Out of scope", spec.out_of_scope.join("\n"));
      section(packet, "Constraints", spec.constraints.join("\n"));
    }
    section(packet, "Test / code diff", prompt.diff);
    section(packet, "Validation", prompt.validation);
    section(packet, "Findings", prompt.findings);
    section(packet, "Feedback", prompt.feedback);
    const details = el("details");
    details.append(
      el("summary", "Full review details"),
      el("pre", JSON.stringify(prompt, null, 2)),
    );
    packet.append(details);
    $("message-action").replaceChildren();
    for (const action of actions.filter((a) => textActions.includes(a))) {
      const option = el("option", labels[action]);
      option.value = action;
      $("message-action").append(option);
    }
    $("findings").replaceChildren();
    for (const finding of prompt.findings || []) {
      const label = el("label"),
        input = document.createElement("input");
      input.type = "checkbox";
      input.value = finding.id;
      label.append(
        input,
        document.createTextNode(" " + finding.id + ": " + finding.title),
      );
      $("findings").append(label);
    }
  }
  $("composer").hidden = !actions.length && !running;
  $("message").disabled = false;
  $("send").disabled = running || !actions.some((a) => textActions.includes(a));
  $("message-action").disabled = running;
  $("edit-previous").hidden = !prompt.draft;
  $("edit-previous").disabled = running;
  $("actions").replaceChildren();
  for (const action of actions.filter((a) => !textActions.includes(a))) {
    const label =
      action === "approve"
        ? task.stage === "test_review"
          ? "Approve tests"
          : "Approve specification"
        : labels[action] || action;
    const control = button(
      label,
      () => respond(action),
      action === "approve" ? "primary" : "",
    );
    control.disabled = running;
    $("actions").append(control);
  }
  $("next-phases").hidden = !(prompt.available_phases || []).length;
  $("phases").replaceChildren();
  for (const phase of prompt.available_phases || []) {
    const control = button(phaseLabels[phase] || phase, () =>
      command({ command: "run", task_id: selected, phase }),
    );
    control.disabled = running;
    $("phases").append(control);
  }
  for (const id of ["resume", "restart", "abandon"]) $(id).disabled = running;
}
async function respond(action) {
  if (submitting || state?.job?.running) return;
  const text = $("message").value;
  if (
    [...textActions, "dismiss", "accept_red"].includes(action) &&
    !text.trim()
  )
    throw new Error("Add your instructions or reason before sending.");
  const ids = [...$("findings").querySelectorAll("input:checked")].map(
    (n) => n.value,
  );
  if (textActions.includes(action) && text.trim()) {
    pendingMessage = {
      task_id: selected,
      text,
      after: (state.task.messages || []).length,
      status: "Sending…",
    };
    renderHistory(state.task);
  }
  try {
    await command({
      command: "respond",
      task_id: selected,
      action,
      text,
      token: state.task.prompt.approval_token,
      finding_ids: ids,
    });
    if (pendingMessage) pendingMessage.status = "Sent · waiting for the agent";
    if ($("message").value === text) $("message").value = "";
  } catch (e) {
    if (pendingMessage)
      pendingMessage.status =
        "Delivery not confirmed · check status before resending";
    throw e;
  } finally {
    if (state?.task) renderHistory(state.task);
  }
}
$("new-form").onsubmit = (e) => {
  e.preventDefault();
  guard(() =>
    command({
      command: document.querySelector('input[name="kind"]:checked').value,
      task_id: $("task-name").value,
      request: $("request").value,
      base: $("base").value,
    }),
  );
};
$("new-task").onclick = () =>
  guard(async () => {
    selected = null;
    packetKey = "";
    $("message").value = "";
    await refresh();
  });
$("refresh").onclick = () => guard(refresh);
$("latest-message").onclick = () => {
  $("history").scrollTop = $("history").scrollHeight;
};
$("send").onclick = () => guard(() => respond($("message-action").value));
$("message").onkeydown = (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !$("send").disabled) {
    e.preventDefault();
    $("send").click();
  }
};
$("edit-previous").onclick = () => {
  $("message").value = state.task.prompt.draft;
  $("message").focus();
};
$("stop").onclick = () =>
  guard(async () => {
    $("stop").disabled = true;
    await api("/api/stop", {});
    await refresh();
  });
$("resume").onclick = () =>
  guard(() => command({ command: "resume", task_id: selected }));
$("choose-backend").onclick = () =>
  guard(() =>
    command({ command: "resume", task_id: selected, backend: "auto" }),
  );
$("restart").onclick = () =>
  guard(() => command({ command: "restart", task_id: selected }));
$("abandon").onclick = () =>
  guard(async () => {
    deletePlan = await api(
      "/api/abandon-plan?task=" + encodeURIComponent(selected),
    );
    $("delete-details").textContent = selected + "\n" + deletePlan.worktree;
    $("delete-name").value = "";
    $("delete-dialog").showModal();
  });
$("delete-dialog").addEventListener("close", () => {
  if ($("delete-dialog").returnValue === "delete")
    guard(() =>
      command({
        command: "abandon",
        task_id: selected,
        confirmation: $("delete-name").value,
        token: deletePlan.confirmation_token,
      }),
    );
});
guard(refresh);
