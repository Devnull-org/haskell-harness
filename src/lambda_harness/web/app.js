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
let memoryLoaded = false,
  memoryLoading = false,
  memoryDirty = false,
  memoryCurrent = null,
  memorySnapshot = null,
  memoryHistory = [],
  memoryPreviewId = 0;
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
function renderReviews(parent, reviews) {
  for (const [role, review] of Object.entries(reviews)) {
    parent.append(
      el(
        "h3",
        role === "security_reviewer" ? "Security review" : "Haskell review",
      ),
      prose(review.summary),
    );
    for (const finding of review.findings) {
      const card = el("article", undefined, "review-finding");
      card.append(
        el("strong", `${finding.severity.toUpperCase()} · ${finding.title}`),
        el("p", finding.location, "muted"),
        prose(finding.impact),
      );
      const details = el("details");
      details.append(el("summary", "Evidence and recommendation"));
      section(details, "Conditions", finding.conditions);
      section(details, "Sequence", finding.sequence.join(" → "));
      section(details, "Evidence", finding.evidence);
      section(details, "Recommendation", finding.recommendation);
      details.append(el("small", `${finding.id} · ${finding.confidence}`));
      card.append(details);
      parent.append(card);
    }
  }
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
    if (
      ["start", "explore", "review", "tests", "chat"].includes(body.command)
    ) {
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
        memoryPreviewId++;
        $("context-preview").replaceChildren();
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
  $("save-backend").disabled = busy;
  renderMemory(busy);
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
    else if (item.reviews) {
      const entry = el("div", undefined, "message");
      renderReviews(entry, item.reviews);
      history.append(entry);
    } else if (item.role === "chat")
      history.append(message("Assistant", item.answer));
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
    key = JSON.stringify([task.task_id, task.stage, prompt, task.test_scope]);
  $("task-meta").replaceChildren(
    el("strong", task.stage.replaceAll("_", " ")),
    el("div", "Base: " + task.base_ref + " · " + task.base.slice(0, 12)),
    el("div", "Worktree: " + (task.worktree || "Preparing")),
    el("div", "Reports: " + task.report_directory),
  );
  if (task.review_source) {
    const source = task.review_source;
    $("task-meta").replaceChildren(
      el("strong", task.stage.replaceAll("_", " ")),
      el("div", `Review: ${source.head_ref} → ${source.base_ref}`),
      el(
        "div",
        `Pinned head: ${source.head.slice(0, 12)} · Merge base: ${source.merge_base.slice(0, 12)}`,
      ),
      el("div", "Worktree: " + (task.worktree || "Preparing")),
      el("div", "Reports: " + task.report_directory),
    );
  }
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
    if (
      prompt.reviews &&
      !(task.messages || []).some(
        (item) =>
          JSON.stringify(item.reviews) === JSON.stringify(prompt.reviews),
      )
    )
      renderReviews(packet, prompt.reviews);
    if (prompt.exploration) {
      const a = prompt.exploration;
      if (!(task.messages || []).some((item) => item.analysis === a.analysis))
        packet.append(el("h2", a.summary), prose(a.analysis));
      section(notes, "References", a.references.join("\n"));
      section(notes, "Open questions", a.open_questions.join("\n"));
    }
    if (prompt.references)
      section(notes, "References", prompt.references.join("\n"));
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
    if (task.test_scope) {
      section(
        packet,
        "Test scope",
        task.test_scope.acceptance_criteria
          .map((item) => `${item.id}: ${item.description}`)
          .join("\n"),
      );
      packet.append(
        el(
          "p",
          `${task.test_scope.packages.join(", ")} · ${task.test_expectation === "green" ? "Passing coverage" : "Failing regression tests"}`,
          "muted",
        ),
      );
    }
    if (prompt.coverage)
      section(
        packet,
        "Coverage",
        Object.entries(prompt.coverage)
          .map(([id, tests]) => `${id}: ${tests.join(", ")}`)
          .join("\n"),
      );
    section(packet, "Test / code diff", prompt.diff);
    section(packet, "Validation", prompt.validation);
    section(packet, "Findings", prompt.findings);
    section(packet, "Feedback", prompt.feedback);
    packet.hidden = !packet.children.length;
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
  const chatting = task.stage === "chat";
  $("composer-label").textContent = chatting ? "Message" : "Your instructions";
  $("message").placeholder = chatting
    ? "Ask a question or continue the conversation."
    : "Ask a follow-up or describe what should change.";
  $("message-action").hidden = chatting;
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
        : action === "done" && task.task_kind === "review"
          ? "Finish review"
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
async function loadProject() {
  const project = await api("/api/project");
  $("setup-status").textContent = [
    project.tools.git ? "Git ready" : "Git missing",
    project.tools.nix ? "Nix ready" : "Nix needed for code validation",
    project.tools.flake
      ? "Project flake found"
      : "Project flake needed for code validation",
  ].join(" · ");
  $("backend-setting").replaceChildren();
  for (const item of [{ name: "auto", available: true }, ...project.backends]) {
    const option = el(
      "option",
      item.name === "auto"
        ? "Automatic selection"
        : item.name + (item.available ? "" : " (executable missing)"),
    );
    option.value = item.name;
    $("backend-setting").append(option);
  }
  $("backend-setting").value = project.backend;
}
async function changeProject(path, body) {
  if (submitting || state?.job?.running) return;
  submitting = true;
  render();
  try {
    const result = await api(path, body);
    await loadProject();
    await refresh();
    return result;
  } finally {
    submitting = false;
    render();
  }
}
function renderMemory(
  busy = submitting || !!state?.job?.running || !!connectionError,
) {
  const historical = !!memorySnapshot && memorySnapshot.id !== memoryCurrent;
  const locked = busy || memoryLoading || !memoryLoaded;
  $("context-notes").readOnly = historical || locked;
  $("context-sources").readOnly = historical || locked;
  for (const id of ["save-context", "refresh-context"])
    $(id).disabled = locked || historical;
  $("read-context").disabled = locked || !memoryHistory.length;
  $("context-snapshot").disabled = locked;
  $("restore-context").disabled = locked || !historical || memoryDirty;
  $("discard-context").disabled = busy || memoryLoading;
  $("use-context").disabled =
    locked || memoryDirty || !memorySnapshot || !state?.task?.can_use_context;
  $("preview-context").disabled = locked || memoryDirty || !memorySnapshot;
  $("preview-task-context").disabled = locked || !state?.task;
  $("context-status").textContent = memoryLoading
    ? "Loading memory…"
    : !memoryLoaded
      ? ""
      : `Current project version: ${memoryCurrent?.slice(0, 12) || "none"}. ` +
        (historical
          ? `Viewing earlier version ${memorySnapshot.id.slice(0, 12)} (read only).`
          : "Viewing current memory.");
  $("memory-draft").textContent = memoryDirty
    ? "Unsaved changes. Save before previewing or applying this draft."
    : historical
      ? "Make this version current to edit it. Previous versions remain in history."
      : "Changes create a saved version. Clear the notes and file list to empty current memory; history is retained.";
  const task = state?.task;
  $("task-memory-status").textContent = task
    ? `${task.task_id}: ${task.project_context_id?.slice(0, 12) || "no memory selected"}. ` +
      (task.can_use_context
        ? "You can select a saved version now."
        : "Finish the current phase before changing memory.")
    : "Select a task to inspect or change its memory.";
}
function showContext(snapshot) {
  memorySnapshot = snapshot;
  memoryDirty = false;
  $("context-notes").value = snapshot?.notes || "";
  $("context-sources").value = (snapshot?.documents || [])
    .map((doc) => doc.path)
    .join("\n");
  $("context-snapshot").value = snapshot?.id || "";
  memoryPreviewId++;
  $("context-preview").replaceChildren();
  $("context-documents").replaceChildren();
  for (const source of snapshot?.documents || []) {
    const details = el("details");
    details.append(el("summary", source.path), el("pre", source.content));
    $("context-documents").append(details);
  }
  if (!snapshot?.documents.length)
    $("context-documents").append(
      el("p", "No reference files saved.", "muted"),
    );
  renderMemory();
}
async function loadMemory(snapshotId = null, discard = false) {
  if (memoryLoading) return;
  if (memoryDirty && !discard)
    throw new Error(
      "Save your memory draft first, or use Discard draft & load current.",
    );
  memoryLoading = true;
  renderMemory();
  try {
    const project = await api("/api/project");
    const id = snapshotId || project.context.latest;
    const saved = id
      ? await api("/api/context?snapshot=" + encodeURIComponent(id))
      : null;
    memoryCurrent = project.context.latest;
    memoryHistory = project.context.history;
    $("context-snapshot").replaceChildren();
    for (const version of memoryHistory) {
      const option = el(
        "option",
        `${version.created.slice(0, 16).replace("T", " ")} · ${version.id.slice(0, 12)}${version.id === memoryCurrent ? " · current" : ""}`,
      );
      option.value = version.id;
      $("context-snapshot").append(option);
    }
    memoryLoaded = true;
    showContext(saved);
  } finally {
    memoryLoading = false;
    renderMemory();
  }
}
async function saveMemory(refreshSources = false) {
  if (submitting || state?.job?.running || memoryLoading || !memoryLoaded)
    return;
  if (memorySnapshot && memorySnapshot.id !== memoryCurrent)
    throw new Error("Make this version current before editing it.");
  submitting = true;
  render();
  try {
    const saved = await api("/api/context/save", {
      notes: $("context-notes").value,
      sources: $("context-sources")
        .value.split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      expected_current: memoryCurrent,
      refresh_sources: refreshSources,
    });
    // Accept the saved version before reloading metadata, even if that read fails.
    memoryCurrent = saved.id;
    showContext(saved);
    await loadMemory(saved.id);
  } finally {
    submitting = false;
    render();
  }
}
async function previewMemory(forTask) {
  const requestId = ++memoryPreviewId;
  const target = $("context-preview");
  const taskId = forTask ? selected : null;
  const snapshotId = memorySnapshot?.id;
  const result = await api(
    "/api/context/preview?" +
      (forTask
        ? "task=" + encodeURIComponent(taskId)
        : "snapshot=" + encodeURIComponent(snapshotId)),
  );
  if (
    requestId !== memoryPreviewId ||
    (forTask && selected !== taskId) ||
    (!forTask && memorySnapshot?.id !== snapshotId)
  )
    return;
  target.replaceChildren(
    el(
      "p",
      `${forTask ? "Task " + taskId : "Project checkout"} · compared with ${result.revision.slice(0, 12)}`,
      "muted",
    ),
  );
  const context = result.context;
  if (!context) {
    target.append(el("p", "No project memory is attached to this task."));
    return;
  }
  target.append(el("p", "Memory version: " + context.id.slice(0, 12), "muted"));
  section(target, "Notes", context.notes || "No notes saved.");
  for (const source of context.documents) {
    const details = el("details");
    details.append(el("summary", source.path));
    if (source.excerpt !== undefined) {
      details.append(el("pre", source.excerpt));
      if (source.truncated)
        details.append(
          el("p", "Excerpt limited to the first 4,000 characters.", "muted"),
        );
      section(details, "Symbols", source.symbols?.join(", "));
    } else
      details.append(
        el(
          "p",
          "Changed or unavailable in this checkout. Agents receive the path and a changed-source flag, with no excerpt.",
        ),
      );
    details.append(el("small", "SHA-256: " + source.sha256));
    target.append(details);
  }
  section(target, "Missing sources", context.missing_sources?.join("\n"));
  section(target, "Memory guidance", context.usage);
  if (!forTask)
    target.append(
      el(
        "p",
        "A task's base and worktree may differ. Use the task preview to see its memory.",
        "muted",
      ),
    );
}
$("project-tools").onclick = () =>
  guard(async () => {
    $("project-panel").hidden = false;
    $("memory-panel").hidden = true;
    await loadProject();
  });
$("close-project").onclick = () => {
  $("project-panel").hidden = true;
};
$("save-backend").onclick = () =>
  guard(() =>
    changeProject("/api/backend", { backend: $("backend-setting").value }),
  );
$("project-memory").onclick = () =>
  guard(async () => {
    $("memory-panel").hidden = false;
    $("project-panel").hidden = true;
    if (!memoryDirty) await loadMemory();
  });
$("close-memory").onclick = () => {
  $("memory-panel").hidden = true;
};
for (const id of ["context-notes", "context-sources"])
  $(id).oninput = () => {
    memoryDirty = true;
    memoryPreviewId++;
    $("context-preview").replaceChildren();
    renderMemory();
  };
$("save-context").onclick = () => guard(() => saveMemory());
$("refresh-context").onclick = () => guard(() => saveMemory(true));
$("discard-context").onclick = () => guard(() => loadMemory(null, true));
$("read-context").onclick = () =>
  guard(() => loadMemory($("context-snapshot").value));
$("restore-context").onclick = () =>
  guard(async () => {
    if (submitting || state?.job?.running || memoryDirty || !memorySnapshot)
      return;
    submitting = true;
    render();
    try {
      const restored = await api("/api/context/restore", {
        snapshot: memorySnapshot.id,
        expected_current: memoryCurrent,
      });
      memoryCurrent = restored.id;
      showContext(restored);
      await loadMemory(restored.id);
    } finally {
      submitting = false;
      render();
    }
  });
$("use-context").onclick = () =>
  guard(async () => {
    if (memoryDirty)
      throw new Error("Save your memory draft before applying it to a task.");
    await changeProject("/api/context/use", {
      task_id: selected,
      snapshot: memorySnapshot.id,
    });
    memoryPreviewId++;
    $("context-preview").replaceChildren();
  });
$("preview-context").onclick = () => guard(() => previewMemory(false));
$("preview-task-context").onclick = () => guard(() => previewMemory(true));
function newTaskKind() {
  return document.querySelector('input[name="kind"]:checked').value;
}
function updateNewTask() {
  const kind = newTaskKind(),
    review = kind === "review",
    tests = kind === "tests";
  $("review-options").hidden = !review;
  $("test-options").hidden = !tests;
  $("walkthrough-option").hidden = kind !== "start";
  $("review-head").required = review;
  $("test-packages").required = tests;
  $("test-criteria").required = tests;
  const github =
    review && $("review-head").value.startsWith("https://github.com/");
  $("base").required = !github;
  $("base").disabled = github;
  $("base-label").textContent = github
    ? "Base comes from the PR"
    : "Base branch or commit";
  $("request").required = !review;
  $("request-label").textContent = review
    ? "Review focus (optional)"
    : tests
      ? "Test scope and context"
      : kind === "chat"
        ? "Your message"
        : "Your question or idea";
  $("request").placeholder = review
    ? "Anything the reviewer should pay particular attention to?"
    : tests
      ? "Describe the behavior, relevant code, and edge cases. Production code stays unchanged."
      : kind === "chat"
        ? "What would you like to talk about? Ask a general question, discuss code, or brainstorm an idea."
        : "Describe what you want to understand or build. Paste an issue, add context, and take as many lines as you need.";
  $("start-task").textContent = {
    explore: "Start exploration →",
    start: "Start interview →",
    review: "Start review →",
    tests: "Write tests →",
    chat: "Start chat →",
  }[kind];
}
$("new-form").onchange = updateNewTask;
$("review-head").oninput = updateNewTask;
updateNewTask();
$("new-form").onsubmit = (e) => {
  e.preventDefault();
  const kind = newTaskKind();
  const body = {
    command: kind,
    task_id: $("task-name").value,
    request: $("request").value,
    base: $("base").value,
  };
  if (kind === "start") body.walkthrough = $("require-walkthrough").checked;
  if (kind === "review")
    Object.assign(body, {
      head: $("review-head").value,
      reviewers: $("reviewers").value,
      request:
        body.request.trim() ||
        "Review this change for correctness, risks, and repository conventions.",
    });
  if (kind === "tests")
    Object.assign(body, {
      packages: $("test-packages")
        .value.split(/[,\s]+/)
        .filter(Boolean),
      criteria: $("test-criteria")
        .value.split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
      expectation: $("test-expectation").value,
    });
  guard(() => command(body));
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
