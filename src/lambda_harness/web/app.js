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
const pages = {
  projects: { path: "/projects", title: "Projects", link: "projects-link" },
  workspace: { path: "/", title: "Workspace", link: "workspace-link" },
  agents: { path: "/agents", title: "Agents", link: "agents-link" },
  memory: {
    path: "/project-memory",
    title: "Project memory",
    link: "project-memory",
  },
  settings: {
    path: "/project-settings",
    title: "Project settings",
    link: "project-tools",
  },
};
function projectFromLocation() {
  return (
    globalThis.location?.pathname.match(
      /^\/projects\/([^/]+)(?:\/(memory|settings|agents))?$/,
    )?.[1] || null
  );
}
function pageFromLocation() {
  const match = globalThis.location?.pathname.match(
    /^\/projects\/[^/]+(?:\/(memory|settings|agents))?$/,
  );
  if (match) return match[1] || "workspace";
  return (
    Object.keys(pages).find(
      (name) => pages[name].path === globalThis.location?.pathname,
    ) || "workspace"
  );
}
let page = pageFromLocation(),
  requestedProject = projectFromLocation(),
  activeProject = null,
  projectList = [],
  projectCardsKey = "",
  projectPickerKey = "",
  projectBusy = false,
  removingProject = null,
  confirmedProjectRemoval = false,
  catalog = null,
  editingAgent = null,
  viewedAgent = null,
  catalogLoading = false;
let handoffBusy = false,
  handoffArchive = null,
  handoffPreview = null,
  handoffTasks = [];
let reviewFromPR = false,
  branchBase = "",
  prBase = "",
  baseInitialized = false;
const labels = {
  continue_handoff: "Continue from saved step",
  save_agent: "Save to repository",
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
  "review-haskell": "Code review",
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
  const deadline = setTimeout(
    () => controller.abort(),
    path.startsWith("/api/handoff/") ? 180000 : 10000,
  );
  const options = {
    headers: { "X-Harness-Token": token },
    signal: controller.signal,
  };
  const projectId = requestedProject || activeProject?.id;
  if (projectId) options.headers["X-Harness-Project"] = projectId;
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
function githubCommentLink(url) {
  const link = el("a", "View GitHub comment");
  if (
    /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/[0-9]+#discussion_r[0-9]+$/.test(
      url,
    )
  ) {
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  return link;
}
function renderReviews(parent, reviews) {
  for (const [role, review] of Object.entries(reviews)) {
    parent.append(
      el(
        "h3",
        {
          haskell_reviewer: "Code review",
          security_reviewer: "Security review",
          documentation_reviewer: "Documentation review",
        }[role] || role.replaceAll("_", " "),
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
      const tools = state?.task?.review_actions;
      const current = state?.task?.prompt?.reviews?.[role]?.findings || [];
      if (
        tools?.github &&
        current.some((f) => JSON.stringify(f) === JSON.stringify(finding))
      ) {
        const posted = tools.posts.find(
          (p) => p.role === role && p.finding_id === finding.id,
        );
        if (posted) {
          card.append(githubCommentLink(posted.url));
        } else {
          const control = button("Post comment on GitHub", () =>
            reviewAction("post_review_comment", {
              role,
              finding_id: finding.id,
            }),
          );
          control.disabled = submitting || !!state?.job?.running;
          card.append(control);
        }
      }
      parent.append(card);
    }
  }
}
async function reviewAction(action, fields = {}) {
  await command({
    command: "review_action",
    task_id: selected,
    action,
    token: state.task.review_actions.token,
    ...fields,
  });
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
      ["start", "explore", "review", "tests", "chat", "agent_builder"].includes(
        body.command,
      )
    ) {
      selected = body.task_id;
      showPage("workspace");
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
    setActiveProject(next.project);
    state = next;
    if (next.projects) updateProjects(next.projects);
    if (next.project === null) {
      activeProject = null;
      selected = null;
      showPage("projects");
    }
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
  if (!baseInitialized && state.base) {
    branchBase = branchBase || state.base;
    if (!reviewFromPR && !$("base").value) $("base").value = branchBase;
    baseInitialized = true;
  }
  const running = !!state.job?.running;
  const busy = running || submitting || handoffBusy || !!connectionError;
  $("tasks").replaceChildren();
  for (const task of state.tasks.filter(
    (item) => item.task_kind !== "agent_builder",
  )) {
    const row = el(
      "div",
      undefined,
      "saved-task" + (task.task_id === selected ? " selected" : ""),
    );
    const item = button(
      task.task_id,
      async () => {
        if (selected !== task.task_id) $("message").value = "";
        selected = task.task_id;
        showPage("workspace");
        memoryPreviewId++;
        $("context-preview").replaceChildren();
        packetKey = "";
        await refresh();
      },
      "task-open",
    );
    item.append(el("small", task.stage.replaceAll("_", " ")));
    const remove = button(
      "×",
      () => openDeleteTask(task.task_id),
      "task-delete",
    );
    remove.ariaLabel = remove.title = "Delete task " + task.task_id;
    remove.disabled = busy;
    row.append(item, remove);
    $("tasks").append(row);
  }
  if (!state.tasks.some((item) => item.task_kind !== "agent_builder"))
    $("tasks").append(
      el("small", "Your saved conversations will appear here.", "muted"),
    );
  renderPage();
  renderAgentDrafts();
  agentControls(busy);
  $("new-task").disabled = busy;
  $("choose-backend").disabled = busy;
  $("new-form").querySelector("button").disabled = busy;
  $("save-backend").disabled = busy;
  $("export-project").disabled = busy;
  $("import-project").disabled = busy;
  $("delete-confirm").disabled = busy;
  renderMemory(busy);
  if (state.task) renderTask(state.task, busy);
  $("activity").hidden = page !== "workspace" || !state.job;
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
        ? job.operation === "review_action"
          ? "■ Stop action"
          : "■ Stop model"
        : "Preparing / checking";
    if (!running && seenJob !== job.id) {
      seenJob = job.id;
      if (job.result?.stage === "agent_ready") guard(loadAgents);
      if (job.error) error(job.error);
      if (job.result?.stage === "abandoned") {
        if (selected === job.result.task_id) {
          selected = null;
          packetKey = "";
          pendingMessage = null;
          $("message").value = "";
          guard(refresh);
          return;
        }
      } else if (job.result && selected !== job.result.task_id) {
        selected = job.result.task_id;
        packetKey = "";
        guard(refresh);
        return;
      }
    }
  }
  const selection = state.job?.selection;
  $("selection").hidden = page !== "workspace" || !selection;
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
  const key = JSON.stringify([
    task.task_id,
    task.request,
    messages,
    pending,
    task.review_actions,
    submitting,
    state?.job?.running,
  ]);
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
    } else if (item.action === "check_links") {
      const latest =
        messages.findLast((m) => m.action === "check_links") === item;
      const entry = message(
        latest
          ? "Documentation link check"
          : "Documentation link check · previous run",
        item.summary,
      );
      const details = el("details");
      details.append(
        el("summary", "Results and scope"),
        prose(item.limitations),
      );
      for (const issue of item.issues)
        details.append(
          prose(
            `${issue.source}: ${issue.url}\n${issue.status} · ${issue.detail}`,
          ),
        );
      entry.append(details);
      history.append(entry);
    } else if (item.action === "post_review_comment") {
      const entry = message("GitHub", `Posted ${item.finding_id}`);
      entry.append(githubCommentLink(item.url));
      history.append(entry);
    } else if (item.analysis)
      history.append(
        message("Explorer", item.summary + "\n\n" + item.analysis),
      );
    else if (item.reviews) {
      const entry = el("div", undefined, "message");
      renderReviews(entry, item.reviews);
      history.append(entry);
    } else if (item.role === "agent_builder") {
      history.append(message("Agent builder", item.answer));
    } else if (item.role === "chat") {
      const entry = message("Assistant", item.answer);
      if (item.memory_save) {
        const saved = item.memory_save;
        const receipt = el("div", undefined, "memory-receipt");
        receipt.append(
          el(
            "strong",
            saved.status === "saved"
              ? "Saved to project memory"
              : "Memory not saved",
          ),
          prose(saved.note),
        );
        if (saved.status === "saved") {
          receipt.append(
            el("small", "Version " + saved.snapshot.slice(0, 12)),
            button(
              "View saved memory",
              () => openMemory(saved.snapshot),
              "quiet",
            ),
          );
        } else receipt.append(el("p", saved.error));
        entry.append(receipt);
      }
      history.append(entry);
    }
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
    key = JSON.stringify([
      task.task_id,
      task.stage,
      prompt,
      task.test_scope,
      task.review_actions,
      running,
    ]);
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
  if (task.task_kind === "agent_builder") {
    $("task-meta").replaceChildren(
      el("strong", task.stage.replaceAll("_", " ")),
      button("← All agents", openAgents, "quiet"),
    );
  } else if (Object.keys(task.agent_profiles || {}).length) {
    $("task-meta").append(
      el(
        "div",
        "Agents: " +
          Object.values(task.agent_profiles)
            .map((profile) => profile.definition.name)
            .join(" · "),
      ),
    );
  }
  $("restart").hidden = task.task_kind === "agent_builder";
  $("export-task").hidden = task.task_kind === "agent_builder";
  renderHistory(task);
  if (key !== packetKey) {
    packetKey = key;
    const packet = $("packet"),
      notes = $("exploration-notes");
    packet.replaceChildren();
    notes.replaceChildren();
    if (prompt.definition) {
      renderAgentDefinition(packet, prompt.definition, task.agent_id);
      packet.append(
        el(
          "p",
          "Ask for changes below, or save this definition to the agent repository.",
          "muted",
        ),
      );
    }
    if (task.saved_agent) {
      packet.append(
        el("p", task.saved_agent.path, "agent-save-note"),
        button("View agent catalog", openAgents),
      );
    }
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
      ["approve", "save_agent"].includes(action) ? "primary" : "",
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
  if (task.review_actions) {
    const control = button("Check documentation links", () =>
      reviewAction("check_links"),
    );
    control.disabled = running;
    $("actions").append(control);
    $("composer").hidden = false;
  }
  for (const id of ["resume", "restart", "abandon", "export-task"])
    $(id).disabled = running;
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
  setActiveProject(project.project);
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
  $("backend-preference-status").textContent = project.unavailable_backend
    ? `Saved backend ${project.unavailable_backend} is unavailable. Choose a replacement for this project.`
    : project.backend_origin === "project"
      ? "Saved for this project. Automatic discovery does not change this preference."
      : project.backend_origin === "launch"
        ? "Initial choice from the server launcher. Launch overrides take precedence over saved preferences on restart."
        : "Using your default configuration. Save a choice to give this project its own preference.";
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
    setActiveProject(project.project);
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
async function openSettings() {
  showPage("settings");
  await loadProject();
}
function handoffControls() {
  $("close-handoff").disabled = handoffBusy;
  $("download-handoff").disabled = handoffBusy;
  $("handoff-file").disabled = handoffBusy;
  $("confirm-handoff").disabled = handoffBusy || !handoffPreview;
  $("handoff-status").className = handoffBusy ? "muted transfer-busy" : "muted";
  for (const input of handoffTasks) input.disabled = handoffBusy;
  if (state) render();
}
async function handoffOperation(message, operation) {
  if (handoffBusy || submitting || state?.job?.running) return;
  handoffBusy = true;
  $("handoff-error").textContent = "";
  $("handoff-status").textContent = message;
  handoffControls();
  try {
    await operation();
  } catch (error) {
    $("handoff-status").textContent = "";
    $("handoff-error").textContent = error.message;
  } finally {
    handoffBusy = false;
    handoffControls();
  }
}
function openHandoff(mode, taskId = null) {
  if (handoffBusy || submitting || state?.job?.running) return;
  handoffArchive = handoffPreview = null;
  handoffTasks = [];
  $("handoff-title").textContent =
    mode === "export" ? "Export a handoff" : "Import a handoff";
  $("handoff-project").textContent = activeProject
    ? `${activeProject.name} · ${activeProject.repository}`
    : state.repository;
  $("handoff-export").hidden = mode !== "export";
  $("handoff-import").hidden = mode !== "import";
  $("handoff-status").textContent = "";
  $("handoff-error").textContent = "";
  $("handoff-file").value = "";
  $("handoff-preview").replaceChildren();
  $("handoff-tasks").replaceChildren();
  for (const task of state.tasks.filter(
    (task) => task.task_kind !== "agent_builder",
  )) {
    const label = el("label", undefined, "handoff-task");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = task.task_id;
    input.checked = taskId === null || task.task_id === taskId;
    handoffTasks.push(input);
    label.append(
      input,
      el("span", `${task.task_id} · ${task.stage.replaceAll("_", " ")}`),
    );
    $("handoff-tasks").append(label);
  }
  if (!handoffTasks.length)
    $("handoff-tasks").append(
      el(
        "p",
        "No saved tasks. You can export project memory on its own.",
        "muted",
      ),
    );
  handoffControls();
  $("handoff-dialog").showModal();
}
$("export-project").onclick = () => openHandoff("export");
$("export-task").onclick = () => openHandoff("export", selected);
$("import-project").onclick = () => openHandoff("import");
$("close-handoff").onclick = () => {
  if (!handoffBusy) $("handoff-dialog").close();
};
$("handoff-dialog").addEventListener("cancel", (event) => {
  if (handoffBusy) event.preventDefault();
});
$("download-handoff").onclick = () =>
  handoffOperation("Preparing your handoff…", async () => {
    const archive = await api("/api/handoff/export", {
      tasks: handoffTasks
        .filter((input) => input.checked)
        .map((input) => input.value),
    });
    const blob = new Blob([JSON.stringify(archive, null, 2) + "\n"], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "lambda-handoff-" + archive.created.slice(0, 10) + ".json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("handoff-status").textContent =
      "Handoff downloaded. Your local tasks are unchanged.";
    await refresh();
  });
$("handoff-file").onchange = () => {
  handoffArchive = handoffPreview = null;
  $("handoff-preview").replaceChildren();
  handoffControls();
  return handoffOperation("Checking this handoff…", async () => {
    const file = $("handoff-file").files?.[0];
    if (!file) {
      $("handoff-status").textContent = "";
      return;
    }
    if (file.size > 32 * 1024 * 1024)
      throw new Error("Choose a handoff file no larger than 32 MB.");
    let archive;
    try {
      archive = JSON.parse(await file.text());
    } catch {
      throw new Error(
        "This file is not valid JSON. Choose a Lambda Harness handoff.",
      );
    }
    const preview = await api("/api/handoff/preview", { archive });
    handoffArchive = archive;
    handoffPreview = preview;
    const container = $("handoff-preview");
    container.append(el("h3", `From ${preview.project}`));
    for (const task of preview.tasks) {
      const entry = el("div", undefined, "handoff-item");
      entry.append(
        el("strong", task.task_id),
        el("p", task.request, "handoff-request"),
        el(
          "small",
          `${task.stage.replaceAll("_", " ")} · ${task.commits} commits${task.uncommitted ? " · unfinished edits" : ""}`,
        ),
      );
      if (task.task_id !== task.original)
        entry.append(
          el(
            "p",
            `Renamed from ${task.original} to keep existing work.`,
            "muted",
          ),
        );
      container.append(entry);
    }
    container.append(
      el(
        "p",
        `${preview.memory_versions} memory versions. ` +
          (preview.keep_current_memory
            ? "Your current project memory stays selected. Imported versions remain available in Memory."
            : "Incoming project memory will become current."),
        "muted",
      ),
    );
    container.append(
      el(
        "p",
        "Each task gets its own worktree and waits for your review. Import does not run agents, builds, or tests. Your backend and agent catalog stay unchanged.",
        "muted",
      ),
    );
    $("handoff-status").textContent =
      "Ready to import into the project shown above.";
    await refresh();
  });
};
$("confirm-handoff").onclick = () =>
  handoffOperation("Restoring your work…", async () => {
    if (!handoffPreview) return;
    const result = await api("/api/handoff/import", {
      archive: handoffArchive,
      confirmation: handoffPreview.confirmation,
    });
    handoffArchive = handoffPreview = null;
    if (!memoryDirty) memoryLoaded = false;
    const openImported = result.tasks.length && !$("message").value.trim();
    if (openImported) {
      selected = result.tasks[0].task_id;
      packetKey = "";
      showPage("workspace");
    }
    await refresh();
    $("handoff-status").textContent = result.tasks.length
      ? "Import complete. Open the imported tasks in Saved work when you are ready. Any unsent message is preserved."
      : "Memory imported. Open Memory to view the saved versions.";
    if (openImported) $("handoff-dialog").close();
  });
$("project-tools").onclick = (event) => {
  event?.preventDefault();
  return guard(openSettings);
};
$("close-project").onclick = () => showPage("workspace");
$("save-backend").onclick = () =>
  guard(() =>
    changeProject("/api/backend", { backend: $("backend-setting").value }),
  );
async function openMemory(snapshotId = null) {
  showPage("memory");
  if (snapshotId || !memoryDirty) await loadMemory(snapshotId);
}
$("project-memory").onclick = (event) => {
  event?.preventDefault();
  return guard(() => openMemory());
};
$("close-memory").onclick = () => showPage("workspace");
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
    review &&
    /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]*\/?$/.test(
      $("review-head").value.trim(),
    );
  if (github !== reviewFromPR) {
    if (github) {
      branchBase = $("base").value;
      $("base").value = prBase;
    } else {
      prBase = $("base").value;
      $("base").value = branchBase;
    }
    reviewFromPR = github;
  }
  $("base").required = !github;
  $("base").disabled = false;
  $("base").placeholder = github ? "PR target branch by default" : "";
  $("base-label").textContent = github
    ? "Comparison base (optional override)"
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
    showPage("workspace");
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
async function openDeleteTask(taskId) {
  if (submitting || handoffBusy || connectionError || state?.job?.running)
    return;
  const target = {
    task_id: taskId,
    project_id: requestedProject || activeProject?.id || null,
  };
  deletePlan = target;
  const plan = await api(
    "/api/abandon-plan?task=" + encodeURIComponent(taskId),
  );
  if (deletePlan !== target) return;
  if (target.project_id !== (requestedProject || activeProject?.id || null))
    throw new Error("The project changed. Open task deletion again.");
  target.confirmation_token = plan.confirmation_token;
  $("delete-details").textContent = taskId + "\n" + plan.worktree;
  $("delete-name").value = "";
  $("delete-error").textContent = "";
  $("delete-dialog").showModal();
}
$("abandon").onclick = () => guard(() => openDeleteTask(selected));
$("delete-cancel").onclick = () => {
  $("delete-dialog").close();
  deletePlan = null;
};
$("delete-form").onsubmit = (event) => {
  event.preventDefault();
  return guard(async () => {
    if (!deletePlan || submitting) return;
    $("delete-error").textContent = "";
    try {
      if (
        deletePlan.project_id !==
        (requestedProject || activeProject?.id || null)
      )
        throw new Error("The project changed. Open task deletion again.");
      if ($("delete-name").value !== deletePlan.task_id)
        throw new Error("Type the task name exactly to confirm deletion.");
      await command({
        command: "abandon",
        task_id: deletePlan.task_id,
        confirmation: $("delete-name").value,
        token: deletePlan.confirmation_token,
      });
      $("delete-dialog").close();
      deletePlan = null;
    } catch (error) {
      $("delete-error").textContent = error.message;
      throw error;
    }
  });
};
function hasProjectDrafts() {
  return (
    memoryDirty ||
    submitting ||
    handoffBusy ||
    !!$("message").value.trim() ||
    (!selected && !!$("request").value.trim()) ||
    (!$("agent-create").hidden && !!$("agent-request").value.trim())
  );
}
function openProject(projectId) {
  if (!projectList.some((project) => project.id === projectId))
    throw new Error("Choose a registered project");
  globalThis.location.assign(`/projects/${encodeURIComponent(projectId)}`);
}
function updateProjects(projects) {
  projectList = projects;
  const picker = $("project-picker");
  const pickerKey = JSON.stringify(projects);
  if (
    document.activeElement !== picker &&
    (pickerKey !== projectPickerKey ||
      picker.value !== (activeProject?.id || ""))
  ) {
    projectPickerKey = pickerKey;
    const placeholder = el("option", "Choose a project");
    placeholder.value = "";
    picker.replaceChildren(placeholder);
    for (const project of projects) {
      const sameName =
        projects.filter((other) => other.name === project.name).length > 1;
      const option = el(
        "option",
        sameName ? `${project.name} · ${project.repository}` : project.name,
      );
      option.value = project.id;
      option.disabled = !project.available;
      picker.append(option);
    }
    picker.value = activeProject?.id || "";
  }
  const cardsKey = JSON.stringify([projects, projectBusy]);
  if (cardsKey === projectCardsKey) return;
  projectCardsKey = cardsKey;
  $("project-list").replaceChildren();
  for (const project of projects) {
    const card = el("article", undefined, "panel project-card");
    const identity = el("div");
    identity.append(
      el("h3", project.name),
      el("p", project.repository, "muted"),
    );
    if (project.running)
      identity.append(el("span", "Work is running", "badge"));
    if (!project.available)
      identity.append(
        el(
          "p",
          "Checkout unavailable. Restore its location or remove this entry.",
          "muted",
        ),
      );
    const actions = el("div", undefined, "button-row");
    const open = button("Open project", () => openProject(project.id));
    open.disabled = !project.available || projectBusy;
    const remove = button(
      "Remove",
      () => {
        removingProject = project;
        $("remove-project-error").textContent = "";
        $("remove-project-details").textContent =
          `${project.name} · ${project.repository}`;
        $("remove-project-drafts").hidden =
          project.id !== activeProject?.id || !hasProjectDrafts();
        $("remove-project-dialog").showModal();
      },
      "quiet danger",
    );
    remove.disabled = project.running || projectBusy;
    actions.append(open, remove);
    card.append(identity, actions);
    $("project-list").append(card);
  }
  if (!projects.length)
    $("project-list").append(
      el("p", "No projects yet. Add a checkout above to start.", "muted"),
    );
}
async function loadProjects() {
  const result = await api("/api/projects");
  updateProjects(result.projects);
}
$("projects-link").onclick = (event) => {
  event?.preventDefault();
  guard(async () => {
    showPage("projects");
    await loadProjects();
  });
};
$("project-picker").onchange = () =>
  guard(() => {
    const id = $("project-picker").value;
    if (id && id !== activeProject?.id) openProject(id);
  });
$("project-add-form").onsubmit = (event) => {
  event.preventDefault();
  return guard(async () => {
    if (projectBusy) return;
    projectBusy = true;
    updateProjects(projectList);
    $("add-project").disabled = true;
    try {
      const added = await api("/api/projects", {
        repository: $("project-path").value,
      });
      $("project-path").value = "";
      $("project-list-status").textContent =
        `Added ${added.name}. Open the project to start working.`;
      await loadProjects();
    } finally {
      projectBusy = false;
      $("add-project").disabled = false;
      updateProjects(projectList);
    }
  });
};
$("remove-project-confirm").onclick = () =>
  guard(async () => {
    if (projectBusy || !removingProject) return;
    projectBusy = true;
    updateProjects(projectList);
    $("remove-project-confirm").disabled = true;
    try {
      const id = removingProject.id;
      const result = await api("/api/projects/remove", {
        project_id: id,
        confirmation: id,
      });
      $("remove-project-dialog").close();
      removingProject = null;
      updateProjects(result.projects);
      if (id === activeProject?.id) {
        confirmedProjectRemoval = true;
        globalThis.location.assign("/projects");
      } else {
        $("project-list-status").textContent =
          "Project removed. Its files and saved work remain on disk.";
      }
    } catch (error) {
      $("remove-project-error").textContent = error.message;
      throw error;
    } finally {
      projectBusy = false;
      $("remove-project-confirm").disabled = false;
      updateProjects(projectList);
    }
  });
globalThis.addEventListener?.("beforeunload", (event) => {
  if (!confirmedProjectRemoval && hasProjectDrafts()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
function projectPagePath(name) {
  return activeProject && name !== "projects"
    ? `/projects/${encodeURIComponent(activeProject.id)}${name === "workspace" ? "" : "/" + name}`
    : pages[name].path;
}
function setActiveProject(project) {
  if (!project) return;
  if (
    (requestedProject && requestedProject !== project.id) ||
    (activeProject && activeProject.id !== project.id)
  )
    throw new Error(
      "The active project changed. Copy any unsaved text before reloading the page.",
    );
  activeProject = project;
  requestedProject = project.id;
  $("project-name").textContent = project.name;
  $("repository").textContent = project.repository;
  $("memory-project").textContent =
    `Memory for ${project.name} · ${project.repository}`;
  $("settings-project").textContent =
    `Settings for ${project.name} · ${project.repository}`;
  $("agent-project-title").textContent = `Agents selected for ${project.name}`;
  $("agent-drafts-project").textContent =
    `Conversations in ${project.name}. Saved agent definitions can be shared across projects.`;
  renderPage();
}
function pageMatchesProject() {
  const id = projectFromLocation();
  return !activeProject || !id || id === activeProject.id;
}
function renderPage() {
  const matches = pageMatchesProject();
  const workspace = page === "workspace";
  $("projects-page").hidden = page !== "projects";
  $("project-navigation").hidden = state?.project === null;
  $("backend").hidden = page === "projects";
  $("agents-page").hidden = page !== "agents";
  $("memory-panel").hidden = page !== "memory" || !matches;
  $("project-panel").hidden = page !== "settings" || !matches;
  if (!matches)
    error(
      "This page belongs to another project. Choose Memory or Settings under the active project in the sidebar.",
    );
  $("new-panel").hidden = !workspace || !!selected;
  $("work").hidden =
    !workspace || !selected || state?.task?.task_id !== selected;
  $("activity").hidden = !workspace || !state?.job;
  $("selection").hidden = !workspace || !state?.job?.selection;
  $("choose-backend").hidden = !workspace || !state?.task;
  for (const [name, info] of Object.entries(pages)) {
    $(info.link).className =
      "workspace-link" + (name === page ? " selected" : "");
    $(info.link).ariaCurrent = name === page ? "page" : null;
    $(info.link).href = projectPagePath(name);
  }
  $("title").textContent = workspace
    ? state?.task?.task_kind === "agent_builder"
      ? "Agent workshop"
      : selected || "What would you like to work on?"
    : activeProject && ["memory", "settings"].includes(page)
      ? `${activeProject.name} / ${page === "memory" ? "Memory" : "Settings"}`
      : pages[page].title;
  document.title = $("title").textContent + " · Lambda Harness";
  if (
    activeProject &&
    page !== "projects" &&
    globalThis.location?.pathname === pages[page].path
  )
    globalThis.history?.replaceState({}, "", projectPagePath(page));
}
function showPage(next) {
  page = next;
  const url = projectPagePath(next);
  if (globalThis.location?.pathname !== url)
    globalThis.history?.pushState({}, "", url);
  renderPage();
}
async function loadPage() {
  if (!pageMatchesProject()) return;
  if (page === "projects") await loadProjects();
  else if (state?.project === null) showPage("projects");
  else if (page === "agents") await loadAgents();
  else if (page === "memory" && !memoryDirty) await loadMemory();
  else if (page === "settings") await loadProject();
}
$("workspace-link").onclick = (event) => {
  event?.preventDefault();
  showPage("workspace");
};
async function openAgents() {
  showPage("agents");
  if (state) render();
  await loadAgents();
}
function agentControls(busy = submitting || !!state?.job?.running) {
  const locked = busy || catalogLoading;
  $("add-agent").disabled = locked || !catalog?.writable;
  $("start-agent-chat").disabled = locked || !catalog?.writable;
  $("use-agent-repository").disabled = locked;
  $("refresh-agents").disabled = catalogLoading;
  for (const control of $("agents-page").querySelectorAll(
    "[data-agent-mutation]",
  ))
    control.disabled = locked || control.dataset.unavailable === "true";
}
function agentAvatar(kind, name, agentId = name) {
  // Local vector portraits have no remote image requests or user-supplied markup.
  let seed = 2166136261;
  for (const character of agentId)
    seed = Math.imul(seed ^ character.codePointAt(0), 16777619) >>> 0;
  const hue = seed % 360,
    background = `hsl(${hue} 55% 86%)`,
    ink = `hsl(${hue} 38% 25%)`,
    accent = `hsl(${(hue + 45 + ((seed >>> 8) % 90)) % 360} 50% 56%)`,
    mark =
      { lambda: "λ", robot: "⌁", fox: "◆", owl: "◉", crab: "≋", shield: "+" }[
        kind
      ] || "⌁",
    eyeGap = 8 + ((seed >>> 12) % 5),
    eyeSize = 4 + ((seed >>> 16) % 3),
    radius = 9 + ((seed >>> 20) % 12),
    tilt = ((seed >>> 24) % 7) - 3;
  const ears =
    kind === "fox" || kind === "owl"
      ? '<path d="M19 31 17 14 32 24M64 31 67 14 51 24" fill="' + ink + '"/>'
      : kind === "crab"
        ? `<path d="M21 42 11 31 7 20M11 31 18 22M63 42 73 31 77 20M73 31 66 22" fill="none" stroke="${ink}" stroke-width="5" stroke-linecap="round"/>`
        : `<path d="M${34 + ((seed >>> 4) % 17)} 16v8M12 37v13M72 37v13" stroke="${ink}" stroke-width="4" stroke-linecap="round"/>`;
  const mouths = ["M35 52q7 6 14 0", "M36 53h12", "M36 52q6 7 12-2"];
  const pattern = Array.from({ length: 6 }, (_, i) => {
    const x = 8 + i * 13,
      y = 8 + ((seed >>> (i * 4)) % 3) * 7;
    return `<circle cx="${x}" cy="${y}" r="${3 + ((seed >>> (i * 3)) % 3)}" fill="${accent}" opacity=".45"/>`;
  }).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 84 84"><rect width="84" height="84" rx="22" fill="${background}"/>${pattern}<path d="M13 84q3-23 29-23t29 23" fill="${accent}"/><g transform="rotate(${tilt} 42 42)">${ears}<rect x="19" y="24" width="46" height="39" rx="${radius}" fill="${ink}"/><circle cx="${42 - eyeGap}" cy="39" r="${eyeSize}" fill="${background}"/><circle cx="${42 + eyeGap}" cy="39" r="${eyeSize}" fill="${background}"/><path d="${mouths[(seed >>> 18) % mouths.length]}" stroke="${background}" fill="none" stroke-width="2.5" stroke-linecap="round"/></g><circle cx="69" cy="69" r="12" fill="${background}"/><text x="69" y="76" text-anchor="middle" font-family="sans-serif" font-size="19" font-weight="bold" fill="${ink}">${mark}</text></svg>`;
  const image = el("img", undefined, "agent-avatar");
  image.src = "data:image/svg+xml," + encodeURIComponent(svg);
  image.alt = name + " avatar";
  image.width = image.height = 64;
  return image;
}
function renderAgentDefinition(parent, definition, agentId) {
  const identity = el("div", undefined, "agent-identity"),
    title = el("div");
  title.append(
    el("h3", definition.name),
    el("p", catalog?.roles[definition.role] || definition.role, "muted"),
  );
  identity.append(
    agentAvatar(definition.avatar, definition.name, agentId),
    title,
  );
  parent.append(identity);
  section(
    parent,
    "Languages",
    definition.languages.join(", ") || "Language neutral",
  );
  section(parent, "Expertise", definition.expertise.join(" · "));
  const instructions = el("details");
  instructions.append(
    el("summary", "Full instructions"),
    prose(definition.instructions),
  );
  parent.append(instructions);
}
async function loadAgents() {
  if (catalogLoading) return;
  catalogLoading = true;
  agentControls();
  try {
    catalog = await api("/api/agents");
    setActiveProject(catalog.project);
    $("agent-source-summary").textContent = catalog.repository
      ? "Repository · " + catalog.repository
      : "Choose a repository to add or edit agents";
    $("agent-source-status").textContent = catalog.error
      ? "Catalog needs attention: " + catalog.error
      : catalog.writable
        ? "Definitions and avatars are saved in agents/*.json. Conversations and project selections stay on this machine."
        : "You are viewing the bundled catalog. Select a writable Git checkout to add or edit agents.";
    $("agent-source").open = !catalog.writable;
    if (document.activeElement !== $("agent-repository"))
      $("agent-repository").value = catalog.repository || "";
    const role = $("agent-role-filter").value;
    $("agent-role-filter").replaceChildren(el("option", "All roles"));
    $("agent-role-filter").children[0].value = "";
    for (const [key, name] of Object.entries(catalog.roles)) {
      const option = el("option", name);
      option.value = key;
      $("agent-role-filter").append(option);
    }
    $("agent-role-filter").value = role;
    renderCatalog();
  } finally {
    catalogLoading = false;
    agentControls();
  }
}
function mutationButton(text, action, unavailable = false) {
  const control = button(text, action);
  control.dataset.agentMutation = "true";
  control.dataset.unavailable = String(unavailable);
  control.disabled = submitting || !!state?.job?.running || unavailable;
  return control;
}
function showAgent(profile) {
  viewedAgent = profile.id;
  const details = $("agent-details");
  details.hidden = false;
  details.replaceChildren();
  const top = el("div", undefined, "compose-top");
  top.append(
    el("div", "AGENT PROFILE", "eyebrow"),
    button(
      "Close",
      () => {
        viewedAgent = null;
        details.hidden = true;
      },
      "quiet",
    ),
  );
  details.append(top);
  renderAgentDefinition(details, profile.definition, profile.id);
  details.append(
    el(
      "p",
      profile.path + " · version " + profile.version.slice(0, 12),
      "muted",
    ),
  );
  if (profile.assignment_problem)
    details.append(el("p", profile.assignment_problem, "muted"));
  const row = el("div", undefined, "button-row");
  row.append(
    mutationButton(
      "Edit through chat",
      () => beginAgent(profile),
      !catalog.writable,
    ),
  );
  row.append(
    mutationButton(
      catalog.selected[profile.definition.role] === profile.id
        ? "Selected for this project"
        : "Use for this project",
      () => assignAgent(profile.definition.role, profile.id),
      !!profile.assignment_problem ||
        catalog.selected[profile.definition.role] === profile.id,
    ),
  );
  details.append(row);
}
function renderCatalog() {
  if (!catalog) return;
  const search = $("agent-search").value.toLowerCase().trim(),
    role = $("agent-role-filter").value;
  const profiles = catalog.profiles.filter(
    (profile) =>
      (!role || profile.definition.role === role) &&
      [
        profile.definition.name,
        ...profile.definition.languages,
        ...profile.definition.expertise,
      ]
        .join(" ")
        .toLowerCase()
        .includes(search),
  );
  $("agent-count").textContent =
    `${profiles.length} of ${catalog.profiles.length} agents`;
  $("agent-grid").replaceChildren();
  for (const profile of profiles) {
    const definition = profile.definition,
      card = el("article", undefined, "agent-card");
    const identity = el("div", undefined, "agent-identity"),
      heading = el("div");
    heading.append(
      el("div", catalog.roles[definition.role], "eyebrow"),
      el("h3", definition.name),
    );
    identity.append(
      agentAvatar(definition.avatar, definition.name, profile.id),
      heading,
    );
    const tags = el("div", undefined, "agent-tags");
    for (const name of definition.languages.length
      ? definition.languages
      : ["Language neutral"])
      tags.append(el("span", name));
    card.append(
      identity,
      tags,
      el("p", definition.expertise.join(" · "), "muted"),
    );
    if (catalog.selected[definition.role] === profile.id)
      card.append(el("span", "Selected for this project", "badge"));
    if (profile.assignment_problem)
      card.append(el("span", "Toolchain support needed", "badge"));
    const actions = el("div", undefined, "button-row agent-card-actions");
    actions.append(
      button("View agent", () => {
        showAgent(profile);
        $("agent-details").scrollIntoView?.({
          behavior: "smooth",
          block: "start",
        });
      }),
      mutationButton("Edit", () => beginAgent(profile), !catalog.writable),
    );
    card.append(actions);
    $("agent-grid").append(card);
  }
  if (!profiles.length)
    $("agent-grid").append(
      el(
        "p",
        catalog.profiles.length
          ? "No agents match this search."
          : "No agents yet. Add your first specialist through chat.",
        "muted",
      ),
    );
  if (viewedAgent) {
    const profile = catalog.profiles.find((item) => item.id === viewedAgent);
    if (profile) showAgent(profile);
    else $("agent-details").hidden = true;
  }
  $("agent-assignments").replaceChildren();
  for (const [role, name] of Object.entries(catalog.roles)) {
    const label = el("label", name),
      select = el("select");
    const fallback = el("option", "Built-in role behavior");
    fallback.value = "";
    select.append(fallback);
    for (const profile of catalog.profiles.filter(
      (item) => item.definition.role === role,
    )) {
      const option = el("option", profile.definition.name);
      option.value = profile.id;
      option.disabled = !!profile.assignment_problem;
      select.append(option);
    }
    const assigned = catalog.selected[role];
    if (
      assigned &&
      !catalog.profiles.some(
        (profile) =>
          profile.id === assigned && profile.definition.role === role,
      )
    ) {
      const unavailable = el(
        "option",
        "Unavailable agent — choose a replacement",
      );
      unavailable.value = assigned;
      unavailable.disabled = true;
      select.append(unavailable);
    }
    select.value = assigned || "";
    select.dataset.agentMutation = "true";
    select.onchange = () =>
      guard(() => assignAgent(role, select.value || null));
    label.append(select);
    $("agent-assignments").append(label);
  }
  agentControls();
}
async function assignAgent(role, agentId) {
  if (submitting || state?.job?.running) return;
  submitting = true;
  agentControls();
  try {
    await api("/api/agents/assign", { role, agent_id: agentId });
  } finally {
    submitting = false;
    await loadAgents();
  }
}
function beginAgent(profile = null) {
  editingAgent = profile?.id || null;
  $("agent-create-title").textContent = profile
    ? "Edit " + profile.definition.name
    : "Create an agent through chat";
  $("agent-create").hidden = false;
  $("agent-request").value = "";
  $("agent-request").placeholder = profile
    ? "What would you like to improve? Describe the change or ask the builder for suggestions."
    : "I need a Rust reviewer for our async services. Help me choose what it should pay attention to.";
  $("agent-create").scrollIntoView?.({ behavior: "smooth", block: "start" });
  $("agent-request").focus();
}
function renderAgentDrafts() {
  const drafts = (state?.tasks || []).filter(
    (item) => item.task_kind === "agent_builder",
  );
  $("agent-drafts-section").hidden = !drafts.length;
  $("agent-drafts").replaceChildren();
  for (const draft of drafts) {
    const control = button(
      draft.display_name || "Agent conversation",
      async () => {
        if (selected !== draft.task_id) $("message").value = "";
        selected = draft.task_id;
        showPage("workspace");
        packetKey = "";
        await refresh();
      },
    );
    control.append(el("small", draft.stage.replaceAll("_", " ")));
    $("agent-drafts").append(control);
  }
}
$("agents-link").onclick = (event) => {
  event.preventDefault();
  guard(openAgents);
};
$("refresh-agents").onclick = () => guard(loadAgents);
$("agent-search").oninput = renderCatalog;
$("agent-role-filter").onchange = renderCatalog;
$("add-agent").onclick = () => beginAgent();
$("cancel-agent-create").onclick = () => {
  $("agent-create").hidden = true;
};
$("use-agent-repository").onclick = () =>
  guard(async () => {
    if (submitting || state?.job?.running) return;
    submitting = true;
    agentControls();
    try {
      await api("/api/agents/source", {
        repository: $("agent-repository").value,
      });
      await loadAgents();
    } finally {
      submitting = false;
      agentControls();
    }
  });
$("agent-form").onsubmit = (event) => {
  event.preventDefault();
  guard(async () => {
    const request = $("agent-request").value;
    if (!request.trim())
      throw new Error(
        "Describe the agent you need or what you would like to change.",
      );
    await command({
      command: "agent_builder",
      task_id: "agent-" + Date.now().toString(36),
      request,
      agent_id: editingAgent,
    });
    $("agent-create").hidden = true;
  });
};
globalThis.addEventListener?.("popstate", () =>
  guard(async () => {
    const nextProject = projectFromLocation();
    if (nextProject && activeProject && nextProject !== activeProject.id) {
      globalThis.location.assign(globalThis.location.pathname);
      return;
    }
    page = pageFromLocation();
    renderPage();
    await loadPage();
  }),
);
renderPage();
guard(async () => {
  await refresh();
  await loadPage();
});
