"""Persistent local TDD workflow with explicit specification and test approval."""

import copy
import fcntl
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from .cleanup import cleanup_plan, remove_resources
from .failures import AgentInterrupted, BackendError, BackendResponseError
from .project_context import ContextStore, state_directory
from .reports import exploration_report, pr_description
from .schemas import validate_response
from .validation import classify_validation, test_evidence
from .workspace import Workspace, digest, git, is_test_path, resolve_base, save_json

PHASE_NODES = {
    "explore": "explore",
    "interview": "interview",
    "tests": "tests",
    "implement": "implement",
    "refactor": "refactor",
    "review-haskell": "review_haskell",
    "review-security": "review_security",
    "walkthrough": "walkthrough",
    "handoff": "finish",
}


class Harness:
    def __init__(
        self,
        repository,
        state_dir,
        agent,
        execute,
        max_repair_rounds=3,
        *,
        unsigned_commits=False,
        progress=None,
    ):
        self.repository = Path(repository).expanduser().resolve()
        if Path(git(self.repository, "rev-parse", "--show-toplevel")).resolve() != self.repository:
            raise ValueError("Repository must be the root of a Git checkout")
        self.state_dir = state_directory(self.repository, state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.context = ContextStore(self.state_dir)
        self.agent, self.execute = agent, execute
        self.max_repair_rounds = max(1, max_repair_rounds)
        self.unsigned_commits = unsigned_commits
        self.progress = progress or (lambda message: None)
        self.connection = sqlite3.connect(
            self.state_dir / "checkpoints.sqlite", check_same_thread=False
        )
        builder = StateGraph(dict)
        nodes = (
            "prepare",
            "explore",
            "interview",
            "worktree",
            "tests",
            "implement",
            "refactor",
            "validate",
            "commit",
            "review_haskell",
            "review_security",
            "review_gate",
            "walkthrough",
            "finish",
            "reply",
        )
        for name in nodes:
            builder.add_node(name, self._node(name))
        builder.add_node("pause", self._pause)
        routes = {name: name for name in (*nodes, "pause")}
        routes["end"] = END
        builder.add_conditional_edges(START, lambda state: state["next"], routes)
        for name in (*nodes, "pause"):
            builder.add_conditional_edges(name, lambda state: state["next"], routes)
        self.graph = builder.compile(checkpointer=SqliteSaver(self.connection))

    @contextmanager
    def _lock(self):
        with (self.state_dir / "lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "Another harness process is running for this repository"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _config(self, task_id):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", task_id):
            raise ValueError("Task ID must use 1–64 letters, digits, underscores, or hyphens")
        return {"configurable": {"thread_id": task_id}, "recursion_limit": 500}

    def _state(self, task_id, *, allow_abandoning=False):
        state = self.graph.get_state(self._config(task_id)).values
        if not state:
            raise ValueError(f"Unknown task: {task_id}")
        if state["stage"] == "abandoning" and not allow_abandoning:
            raise ValueError(f"Task deletion is unfinished; run abandon {task_id} again")
        return state

    def start(
        self, task_id, request, *, base="HEAD", mode="phased", walkthrough=False, phase="interview"
    ):
        with self._lock():
            return self._start(
                task_id, request, base=base, mode=mode, walkthrough=walkthrough, phase=phase
            )

    def _start(
        self,
        task_id,
        request,
        *,
        base="HEAD",
        mode="phased",
        walkthrough=False,
        restarted_from=None,
        phase="interview",
    ):
        config = self._config(task_id)
        if self.graph.get_state(config).values:
            raise ValueError(f"Task already exists: {task_id}; resume it instead")
        if mode not in {"phased", "full", "interview"} or not request.strip():
            raise ValueError("A request and a phased, full, or interview mode are required")
        if phase not in {"interview", "explore"}:
            raise ValueError("A new task must start with interview or explore")
        base_revision = resolve_base(self.repository, base)
        directory = self.state_dir / task_id
        directory.mkdir(mode=0o700)
        state = {
            "task_id": task_id,
            "request": request,
            "mode": mode,
            "initial_phase": phase,
            "walkthrough_enabled": walkthrough,
            "directory": str(directory),
            "report_directory": str(directory / "reports"),
            "base": base_revision,
            "base_ref": base,
            "stage": "preparing",
            "prompt": {"summary": "Preparing the task worktree", "actions": []},
            "next": "prepare",
            "seq": 0,
            "messages": [],
            "validations": [],
            "commits": [],
            "review_round": 0,
            "dispositions": [],
            "attempts": 0,
            "project_context_id": self.context.latest_id(),
        }
        if restarted_from:
            state["restarted_from"] = restarted_from
        self.graph.invoke(state, config)
        return self._view(self._state(task_id))

    def restart(self, task_id, *, new_task_id=None, request=None, base=None):
        """Start a new attempt without changing the previous task or its worktree."""
        with self._lock():
            original = self._state(task_id)
            if new_task_id is None:
                attempt = 1
                while True:
                    suffix = f"-restart-{attempt}"
                    candidate = task_id[: 64 - len(suffix)] + suffix
                    if (
                        not self.graph.get_state(self._config(candidate)).values
                        and not (self.state_dir / candidate).exists()
                    ):
                        new_task_id = candidate
                        break
                    attempt += 1
            return self._start(
                new_task_id,
                original["request"] if request is None else request,
                base=original["base"] if base is None else base,
                walkthrough=original["walkthrough_enabled"],
                restarted_from=task_id,
                phase=original.get("initial_phase", "interview"),
            )

    def inspect(self, task_id):
        with self._lock():
            return self._view(self._state(task_id, allow_abandoning=True))

    def _abandon_plan(self, task_id):
        state = self._state(task_id, allow_abandoning=True)
        plan = cleanup_plan(self.repository, self.state_dir, state)
        checkpoint = self.graph.get_state(self._config(task_id)).config
        return {**plan, "confirmation_token": digest([checkpoint, plan])}

    def abandon_plan(self, task_id):
        """Describe exactly which task resources a confirmation will remove."""
        with self._lock():
            return self._abandon_plan(task_id)

    def abandon(self, task_id, *, confirmation_token):
        """Delete one confirmed task, retaining shared knowledge and other tasks."""
        with self._lock():
            plan = self._abandon_plan(task_id)
            if not confirmation_token:
                raise ValueError("Task deletion requires confirmation")
            if confirmation_token != plan["confirmation_token"]:
                raise ValueError("Task changed since confirmation; inspect and confirm it again")
            state = copy.deepcopy(self._state(task_id, allow_abandoning=True))
            state.update(
                stage="abandoning",
                next="end",
                cleanup_revision=plan["revision"],
                prompt={
                    "summary": f"Deletion is unfinished. Run abandon {task_id} again to finish.",
                    "actions": [],
                },
            )
            try:
                self.graph.update_state(self._config(task_id), state, as_node="finish")
                remove_resources(self.repository, plan)
                self.graph.checkpointer.delete_thread(task_id)
            except sqlite3.Error as error:
                raise RuntimeError(
                    f"Could not finish deleting task data; run abandon {task_id} again: {error}"
                ) from error
            return {"task_id": task_id, "stage": "abandoned"}

    def resume(self, task_id, *, retry_backend=False):
        """Continue interrupted execution without answering a human gate."""
        with self._lock():
            state = self._state(task_id)
            snapshot = self.graph.get_state(self._config(task_id))
            if (
                retry_backend
                and state["stage"] == "backend_blocked"
                and "retry" in state["prompt"]["actions"]
                and any(task.interrupts for task in snapshot.tasks)
            ):
                self.graph.invoke(Command(resume={"action": "retry"}), self._config(task_id))
                state = self._state(task_id)
            elif snapshot.next and not any(task.interrupts for task in snapshot.tasks):
                self.graph.invoke(None, self._config(task_id))
                state = self._state(task_id)
            return self._view(state)

    def run_phase(self, task_id, phase):
        """Start one eligible phase from a saved boundary, never bypass a pending gate."""
        with self._lock():
            state = copy.deepcopy(self._state(task_id))
            config = self._config(task_id)
            if self.graph.get_state(config).next:
                raise ValueError("The current phase is pending; use resume and answer its gate")
            available = self._available_phases(state)
            if phase not in available:
                raise ValueError(
                    f"Phase {phase!r} is not available; "
                    f"choose from: {', '.join(available) or 'none'}"
                )
            expected = state.get("phase_fingerprint", state.get("gate_fingerprint"))
            if self._workspace(state).fingerprint() != expected:
                raise ValueError(
                    "Checkout changed since the completed phase; restore the saved revision "
                    "and files, or start a new task for the changed checkout"
                )
            state["mode"], state["selected_phase"] = "phased", phase
            if phase == "interview":
                state.pop("specification", None)
            state["next"] = "worktree" if phase == "tests" else PHASE_NODES[phase]
            state["prompt"], state["attempts"], state["patch_count"] = {}, 0, 0
            self.graph.invoke(state, config)
            return self._view(self._state(task_id))

    def use_context(self, task_id, snapshot_id=None):
        """Select shared knowledge explicitly while a task is between phases."""
        with self._lock():
            config = self._config(task_id)
            state = copy.deepcopy(self._state(task_id))
            if self.graph.get_state(config).next:
                raise ValueError("The current phase is pending; finish it before changing context")
            if state["stage"] == "ready":
                raise ValueError("This task is finished; start a new task to use different context")
            snapshot = self.context.get(snapshot_id)
            state["project_context_id"] = snapshot["id"]
            self.graph.update_state(config, state)
            save_json(Path(state["report_directory"]) / "current-review.json", self._view(state))
            return self._view(self._state(task_id))

    def respond(self, task_id, action, **fields):
        with self._lock():
            state = self._state(task_id)
            if action not in state.get("prompt", {}).get("actions", []):
                raise ValueError(f"Action {action!r} is not available at {state.get('stage')}")
            if action in {"approve", "accept_red"}:
                if fields.get("approval_token") != state["prompt"].get("approval_token"):
                    raise ValueError("Stale or missing approval token")
                if self._workspace(state).fingerprint() != state["gate_fingerprint"]:
                    raise ValueError(
                        "Worktree changed since review; approval is stale. Recheck first."
                    )
            if action in {"answer", "question", "revise", "dismiss", "accept_red"}:
                if not fields.get("text", "").strip():
                    raise ValueError(f"{action} requires text")
            if action in {"fix", "dismiss"}:
                ids = fields.get("finding_ids", [])
                if not ids or not set(ids) <= {f["id"] for f in state["findings"]}:
                    raise ValueError("Select existing finding IDs")
            self.graph.invoke(Command(resume={"action": action, **fields}), self._config(task_id))
            return self._view(self._state(task_id))

    def close(self):
        self.connection.close()
        self.context.close()

    def _workspace(self, state):
        return Workspace(state.get("worktree", self.repository))

    def _view(self, state, *, inspect_worktree=True):
        view = {
            key: state[key]
            for key in (
                "task_id",
                "stage",
                "prompt",
                "worktree",
                "base",
                "base_ref",
                "restarted_from",
                "report_directory",
                "project_context_id",
            )
            if key in state
        }
        if state["stage"] == "abandoning":
            view["revision"] = state.get("cleanup_revision")
        elif inspect_worktree:
            view["revision"] = self._workspace(state).revision
        else:
            # A browser poll reads checkpoints while another thread may remove a worktree.
            commits = state.get("commits", [])
            view["revision"] = commits[-1]["revision"] if commits else state["base"]
        if state["stage"] == "specification_ready":
            view["prompt"] = {**view["prompt"], "available_phases": self._available_phases(state)}
        return view

    def _available_phases(self, state):
        following = {
            "specification_ready": ["explore", "tests"],
            "exploration_ready": ["explore", "interview"],
            "tests_requested": ["tests"],
            "tests_ready": ["implement"],
            "implementation_ready": ["refactor"],
            "refactor_requested": ["refactor"],
            "walkthrough_ready": ["handoff"],
        }
        if state["stage"] in {"refactor_ready", "reviews_ready"}:
            missing = [
                phase
                for phase, role in (
                    ("review-haskell", "haskell_reviewer"),
                    ("review-security", "security_reviewer"),
                )
                if state.get(role, {}).get("revision") != state["review_revision"]
            ]
            if missing:
                return missing
            return ["walkthrough"] if state["walkthrough_enabled"] else ["walkthrough", "handoff"]
        return following.get(state["stage"], [])

    def _phase_complete(self, state, stage, summary):
        state["stage"], state["next"] = stage, "end"
        state["phase_fingerprint"] = self._workspace(state).fingerprint()
        state["prompt"] = {
            "summary": summary,
            "actions": [],
            "available_phases": self._available_phases(state),
        }
        reports = Path(state["report_directory"])
        if "specification" in state:
            save_json(reports / "specification.json", state["specification"])
        if stage == "exploration_ready":
            state["prompt"].update(
                exploration=state["exploration"], report=str(reports / "exploration.md")
            )
        save_json(reports / "patches.json", state["commits"])
        save_json(reports / "dispositions.json", state["dispositions"])
        save_json(reports / "walkthrough.json", state.get("walkthrough_answers", []))
        save_json(reports / "current-review.json", self._view(state))

    def _node(self, name):
        def run(original):
            state = copy.deepcopy(original)
            journal = Path(state["directory"]) / "steps" / f"{state['seq']:04d}-{name}.json"
            if journal.exists():
                return json.loads(journal.read_text())
            self.progress(f"{state['task_id']}: {name.replace('_', ' ')}")
            try:
                getattr(self, "_" + name)(state)
            except (ValueError, RuntimeError, OSError) as error:
                if name == "reply":
                    state["failed_reply"] = {
                        key: copy.deepcopy(original[key]) for key in ("response", "stage", "prompt")
                    }
                state["retry_node"] = name
                actions = (
                    ["retry", "recheck"]
                    if state.get("validation") and not state.get("agent_integrity_fingerprint")
                    else ["retry"]
                )
                if isinstance(error, AgentInterrupted):
                    self._gate(
                        state,
                        "agent_paused",
                        {
                            "summary": "Model request stopped. Your conversation is saved.",
                            "guidance": "Revise your instructions, retry this request, or quit.",
                            "draft": state["interrupted_draft"],
                        },
                        ["revise", "retry"],
                    )
                elif isinstance(error, BackendError):
                    self._gate(state, "backend_blocked", error.prompt(), ["retry"])
                else:
                    self._gate(state, "agent_error", {"error": str(error)}, actions)
            state["seq"] += 1
            save_json(journal, state)
            return state

        return run

    def _gate(self, state, stage, prompt, actions):
        state["stage"], state["next"] = stage, "pause"
        state["gate_fingerprint"] = self._workspace(state).fingerprint()
        token = digest([state["task_id"], state["seq"], stage, state["gate_fingerprint"], prompt])
        state["prompt"] = {**prompt, "actions": actions, "approval_token": token}
        save_json(Path(state["report_directory"]) / "current-review.json", self._view(state))

    def _pause(self, original):
        state = copy.deepcopy(original)
        state["response"] = interrupt(self._view(state))
        state["next"] = "reply"
        return state

    def _context(self, state, **extra):
        workspace = self._workspace(state)
        context = {
            "request": state["request"],
            "specification": state.get("specification"),
            "messages": state["messages"],
            "revision": workspace.revision,
            "base_revision": state["base"],
            "diff": workspace.diff(state["base"]),
            "approved_tests": state.get("approved_tests", {}),
            "selected_findings": state.get("selected_findings", []),
            "patches": [
                {key: patch[key] for key in ("revision", "role", "summary")}
                for patch in state["commits"]
            ],
        }
        if state.get("project_context_id"):
            context["project_context"] = self.context.for_agent(
                state["project_context_id"], workspace
            )
        if state.get("validation"):
            context["validation"] = state["validation"]
        if state.get("exploration"):
            context["exploration"] = state["exploration"]
        return {**context, **extra}

    def _ask(self, state, role, **extra):
        context = self._context(state, **extra)
        workspace = self._workspace(state)
        before = workspace.fingerprint()
        if state.get("agent_integrity_fingerprint"):
            self._check_agent_checkout(state, workspace, state["agent_integrity_fingerprint"])
            del state["agent_integrity_fingerprint"]
        cache = Path(state["directory"]) / "proposals" / f"{state['seq']:04d}-{role}.json"
        if cache.exists():
            result = json.loads(cache.read_text())
        else:
            for attempt in range(2):
                try:
                    result = self.agent(role, context, workspace.path)
                    try:
                        result = validate_response(role, context, result, notify=self.progress)
                    except ValidationError as error:
                        raise BackendResponseError(
                            str(error), json.dumps(result, ensure_ascii=False, default=str), ""
                        ) from error
                except (AgentInterrupted, KeyboardInterrupt) as error:
                    self._check_agent_checkout(state, workspace, before)
                    latest = next(
                        (
                            message["text"]
                            for message in reversed(state["messages"])
                            if message.get("text")
                        ),
                        state["request"],
                    )
                    state["interrupted_draft"] = (
                        extra.get("question") or extra.get("developer_answer") or latest
                    )
                    raise AgentInterrupted() from error
                except BackendError as error:
                    self._save_backend_failure(state, role, attempt, error)
                    self._check_agent_checkout(state, workspace, before)
                    # Repair only response formatting, once, within a bounded prompt.
                    if (
                        attempt == 0
                        and isinstance(error, BackendResponseError)
                        and len(error.stdout.encode()) <= 64_000
                    ):
                        correction = {
                            **context,
                            "response_repair": {
                                "validation_error": str(error)[:4000],
                                "previous_response": error.stdout,
                            },
                        }
                        if len(json.dumps(correction, ensure_ascii=False).encode()) <= 400_000:
                            context = correction
                            self.progress(
                                f"{state['task_id']}: invalid {role} response; "
                                "requesting one correction"
                            )
                            continue
                    raise
                except (ValueError, RuntimeError, OSError):
                    self._check_agent_checkout(state, workspace, before)
                    raise
                self._check_agent_checkout(state, workspace, before)
                break
            save_json(cache, result)
        return result

    @staticmethod
    def _check_agent_checkout(state, workspace, before):
        if workspace.fingerprint() != before:
            state["agent_integrity_fingerprint"] = before
            raise ValueError(
                "Agent changed the checkout directly; inspect and restore it before retrying"
            )

    @staticmethod
    def _save_backend_failure(state, role, attempt, error):
        directory = Path(state["directory"]) / "backend-responses" / f"{state['seq']:04d}-{role}"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = directory if attempt == 0 else directory / f"attempt-{attempt + 1}"
        destination.mkdir(exist_ok=True, mode=0o700)
        for filename, text in (("stdout.txt", error.stdout), ("stderr.txt", error.stderr)):
            path = destination / filename
            path.write_text(text)
            path.chmod(0o600)
        metadata = destination / "failure.json"
        save_json(metadata, {"backend": error.backend, "kind": error.kind, "error": str(error)})
        metadata.chmod(0o600)
        error.diagnostics = str(directory)

    def _interview(self, state):
        extra = (
            {"intent": "implementation_interview"}
            if state.get("selected_phase") == "interview" and state.get("exploration")
            else {}
        )
        result = self._ask(state, "interviewer", **extra)
        state["specification"] = result["specification"]
        questions = result["questions"] or result["specification"]["open_questions"]
        if questions:
            self._gate(state, "interview", {"questions": questions}, ["answer"])
        else:
            self._gate(
                state,
                "specification_review",
                {"specification": state["specification"]},
                ["approve", "question", "revise"],
            )

    def _explore(self, state):
        result = self._ask(state, "explorer", intent="explore")
        state["exploration"] = {**result, "revision": self._workspace(state).revision}
        state["messages"].append({"stage": "exploration", "role": "explorer", **result})
        reports = Path(state["report_directory"])
        save_json(reports / "exploration.json", state["exploration"])
        (reports / "exploration.md").write_text(exploration_report(state))
        self._gate(
            state,
            "exploration",
            {"exploration": state["exploration"], "report": str(reports / "exploration.md")},
            ["question", "done"],
        )

    def _create_worktree(self, state):
        path = Path(state["directory"]) / "worktree"
        branch = f"harness/{state['task_id']}-{digest(state['directory'])[:8]}"
        if not path.exists():
            git(self.repository, "worktree", "add", "-b", branch, str(path), state["base"])
        elif (
            Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve()
            or git(path, "branch", "--show-current") != branch
            or git(path, "rev-parse", "HEAD") != state["base"]
            or git(path, "status", "--porcelain")
        ):
            raise ValueError("Existing task worktree differs from its saved base; inspect it first")
        state["worktree"], state["branch"] = str(path), branch

    def _prepare(self, state):
        self._create_worktree(state)
        state["next"] = state.get("initial_phase", "interview")

    def _worktree(self, state):
        if "worktree" not in state:
            self._create_worktree(state)
        workspace = self._workspace(state)
        for package in state["specification"]["packages"]:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]*", package) or not any(
                Path(p).name == package + ".cabal" for p in workspace.files()
            ):
                raise ValueError(f"Unknown Cabal package: {package}")
        state["next"] = "tests"

    def _tests(self, state):
        unexpected = [
            p
            for p in self._workspace(state).changed_paths()
            if not is_test_path(p) and not p.endswith(".cabal")
        ]
        if unexpected:
            raise ValueError(
                "Uncommitted production edits cannot enter a test-first patch. "
                "Save or restore them before revising tests: " + ", ".join(unexpected)
            )
        proposal = self._ask(state, "test_author")
        criteria = {c["id"] for c in state["specification"]["acceptance_criteria"]}
        if not all(proposal["coverage"].get(key) for key in criteria):
            raise ValueError("Tests must map every acceptance criterion to named test cases")
        self._workspace(state).apply(proposal["changes"], tests_only=True)
        state["test_paths"] = sorted(
            set(state.get("test_paths", [])) | {c["path"] for c in proposal["changes"]}
        )
        state["expected_failures"], state["coverage"] = (
            proposal["expected_failures"],
            proposal["coverage"],
        )
        state["proposal"], state["role"], state["phase"] = proposal, "test_author", "red"
        state["next"] = "validate"

    def _assert_tests(self, state):
        if (
            "approved_tests" in state
            and self._workspace(state).test_manifest(state["test_paths"]) != state["approved_tests"]
        ):
            raise ValueError(
                "Approved tests changed outside the test review gate; "
                "restore them or request revised tests"
            )

    def _code(self, state, role):
        self._assert_tests(state)
        proposal = self._ask(state, role)
        state["role"] = role
        if any(
            is_test_path(c["path"])
            or c["path"] in state["approved_tests"]
            or c["path"].endswith(".cabal")
            for c in proposal["changes"]
        ):
            state["proposed_test_change"] = proposal
            self._gate(
                state,
                "test_change_review",
                {
                    "proposal": proposal,
                    "explanation": "Approved tests are unchanged. Request a test-author "
                    "revision to discuss this proposal.",
                },
                ["question", "revise", "reject"],
            )
            return
        self._workspace(state).apply(proposal["changes"])
        self._assert_tests(state)
        state["proposal"] = proposal
        state["phase"] = (
            "red" if role == "implementer" and not proposal.get("complete", True) else "green"
        )
        state["attempts"] += 1
        state["next"] = "validate"

    def _implement(self, state):
        self._code(state, "implementer")

    def _refactor(self, state):
        self._code(state, "refactorer")

    def _validate(self, state):
        workspace = self._workspace(state)
        if git(workspace.path, "ls-files", "--others", "--exclude-standard"):
            raise ValueError(
                "Untracked files are outside the review diff; inspect and git add them first"
            )
        commands = [("lint", ["just", "lint"]), ("nix", ["just", "check"])]
        commands += [(f"test:{p}", ["just", "test", p]) for p in state["specification"]["packages"]]
        results = {}
        for name, command in commands:
            self.progress(f"{state['task_id']}: {' '.join(command)}")
            results[name] = self.execute(command, workspace.path)
            save_json(
                Path(state["directory"])
                / "logs"
                / f"{state['seq']:04d}-{name.replace(':', '-')}.json",
                results[name],
            )
            if name == "lint":
                lint_fingerprint = workspace.fingerprint()
        if workspace.fingerprint() != lint_fingerprint:
            raise ValueError("Files changed while checks ran; inspect the worktree and recheck")
        if state["role"] != "test_author":
            self._assert_tests(state)
        elif any(
            not is_test_path(p) and not p.endswith(".cabal") for p in workspace.changed_paths()
        ):
            raise ValueError(
                "Production files changed during test validation; inspect and restore them"
            )
        status = classify_validation(
            results, state.get("expected_failures", []), phase=state["phase"]
        )
        state["validated_fingerprint"] = workspace.fingerprint()
        validation = {
            "status": status,
            "phase": state["phase"],
            "role": state["role"],
            "revision": workspace.revision,
            "fingerprint": state["validated_fingerprint"],
            "log_directory": str(Path(state["directory"]) / "logs"),
            "commands": {
                k: {
                    "argv": v["argv"],
                    "exit_code": v["exit_code"],
                    "stdout_tail": v["stdout"][-8000:],
                    "stderr_tail": v["stderr"][-8000:],
                }
                for k, v in results.items()
            },
        }
        try:
            validation["test_evidence"] = test_evidence(results)
        except ValueError as error:
            validation["evidence_error"] = str(error)
        state["validation"] = validation
        state["validations"].append(validation)
        save_json(Path(state["report_directory"]) / "validation.json", state["validations"])
        if status == "needs_red_review":
            self._gate(
                state,
                "red_failure_review",
                {
                    "validation": validation,
                    "explanation": "Inspect the Nix logs. Accept only if its failures come "
                    "solely from the expected red tests; record the evidence.",
                },
                ["accept_red", "recheck"],
            )
        elif status == "blocked":
            if (
                state["role"] in {"implementer", "refactorer"}
                and state["attempts"] < self.max_repair_rounds
            ):
                state["next"] = "implement" if state["role"] == "implementer" else "refactor"
            else:
                self._gate(
                    state, "validation_blocked", {"validation": validation}, ["recheck", "revise"]
                )
        else:
            self._validated(state)

    def _validated(self, state):
        if state["role"] == "test_author":
            self._gate(
                state,
                "test_review",
                {
                    "summary": state["proposal"]["summary"],
                    "diff": self._workspace(state).diff(),
                    "coverage": state["coverage"],
                    "validation": state["validation"],
                },
                ["approve", "question", "revise", "recheck"],
            )
        else:
            state["next"] = "commit"

    def _commit(self, state):
        workspace = self._workspace(state)
        revision = workspace.commit(
            state["proposal"]["commit_message"],
            expected=state["validated_fingerprint"],
            step=f"{state['task_id']}/{state['seq']}",
            unsigned=self.unsigned_commits,
        )
        if revision != state["base"] and not any(
            c["revision"] == revision for c in state["commits"]
        ):
            state["commits"].append(
                {
                    "revision": revision,
                    "role": state["role"],
                    "summary": state["proposal"]["summary"],
                    "validation": state["validation"],
                }
            )
        if state["role"] == "test_author":
            state["approved_tests"] = workspace.test_manifest(state["test_paths"])
            state["attempts"], state["patch_count"], state["next"] = 0, 0, "implement"
            if state["mode"] == "phased":
                self._phase_complete(
                    state, "tests_ready", "Approved tests committed. Tests phase complete."
                )
        elif state["role"] == "implementer":
            if state["proposal"].get("complete", True):
                state["attempts"], state["patch_count"], state["next"] = 0, 0, "refactor"
                if state["mode"] == "phased":
                    self._phase_complete(
                        state, "implementation_ready", "Implementation complete and validated."
                    )
            else:
                self._more_patches(state, "implement")
        elif not state["proposal"].get("complete", True):
            self._more_patches(state, "refactor")
        else:
            state["review_revision"] = revision
            state["review_round"] += 1
            state.pop("haskell_reviewer", None)
            state.pop("security_reviewer", None)
            state["next"] = "review_haskell"
            if state["mode"] == "phased":
                self._phase_complete(state, "refactor_ready", "Refactoring complete and validated.")

    def _more_patches(self, state, next_node):
        state["patch_count"] = state.get("patch_count", 0) + 1
        state["attempts"] = 0
        if state["patch_count"] >= 12:
            state["retry_node"] = next_node
            self._gate(
                state,
                "agent_error",
                {"error": "Twelve patches reached; inspect progress before continuing"},
                ["retry"],
            )
        else:
            state["next"] = next_node

    def _review(self, state, role, next_node):
        workspace = self._workspace(state)
        if workspace.revision != state["review_revision"] or workspace.diff():
            raise ValueError("Review revision changed; revalidate before reviewing")
        result = self._ask(state, role)
        result["revision"] = workspace.revision
        state[role] = result
        filename = "haskell-review.json" if role == "haskell_reviewer" else "security-review.json"
        save_json(Path(state["report_directory"]) / filename, result)
        save_json(
            Path(state["report_directory"]) / f"round-{state['review_round']}-{filename}", result
        )
        state["next"] = next_node

    def _review_haskell(self, state):
        following = "review_gate" if state["mode"] == "phased" else "review_security"
        self._review(state, "haskell_reviewer", following)

    def _review_security(self, state):
        self._review(state, "security_reviewer", "review_gate")

    def _review_gate(self, state):
        findings = [
            finding
            for role in ("haskell_reviewer", "security_reviewer")
            for finding in state.get(role, {}).get("findings", [])
        ]
        if len({f["id"] for f in findings}) != len(findings):
            raise ValueError("Reviewer finding IDs must be unique")
        dismissed = {
            d["id"] for d in state["dispositions"] if d["revision"] == state["review_revision"]
        }
        state["findings"] = [f for f in findings if f["id"] not in dismissed]
        if state["findings"]:
            self._gate(
                state,
                "review_findings",
                {"findings": state["findings"]},
                ["question", "fix", "dismiss"],
            )
        else:
            state["next"] = "walkthrough" if state["walkthrough_enabled"] else "finish"
            if state["mode"] == "phased":
                self._phase_complete(
                    state, "reviews_ready", "Selected review complete. Findings resolved."
                )

    def _walkthrough(self, state):
        state["questions"] = self._ask(state, "walkthrough")["questions"]
        state["question_index"], state["walkthrough_answers"] = 0, []
        self._gate(state, "walkthrough", state["questions"][0], ["answer", "skip"])

    def _finish(self, state):
        reports = Path(state["report_directory"])
        reports.mkdir(parents=True, exist_ok=True)
        save_json(reports / "specification.json", state["specification"])
        if state["stage"] == "specification_review" or "worktree" not in state:
            self._phase_complete(
                state, "specification_ready", "Specification approved. Interview complete."
            )
            return
        else:
            workspace = self._workspace(state)
            if workspace.revision != state["review_revision"] or git(
                workspace.path, "status", "--porcelain"
            ):
                raise ValueError(
                    "Reviewed worktree changed before handoff; revalidate and review again"
                )
            state["stage"] = "ready"
            save_json(reports / "patches.json", state["commits"])
            save_json(reports / "dispositions.json", state["dispositions"])
            save_json(reports / "walkthrough.json", state.get("walkthrough_answers", []))
            (reports / "pr-description.md").write_text(pr_description(state))
        state["prompt"] = {"summary": "Local work is ready for developer inspection", "actions": []}
        state["next"] = "end"

    def _reply(self, state):
        response = state.pop("response")
        action, stage = response["action"], state["stage"]
        if stage == "agent_paused" and action == "revise":
            if state["retry_node"] == "reply":
                state.update(state.pop("failed_reply"))
                state["response"]["text"] = response["text"]
            else:
                state["messages"].append(
                    {
                        "stage": stage,
                        "action": "revise",
                        "text": response["text"],
                        "replaces_interrupted_instructions": state["interrupted_draft"],
                    }
                )
            state["next"] = state["retry_node"]
        elif stage == "exploration" and action == "done":
            self._phase_complete(
                state, "exploration_ready", "Exploration saved. Start any further phase explicitly."
            )
        elif stage == "exploration" and action == "question":
            state["messages"].append({"stage": stage, "action": action, "text": response["text"]})
            state["next"] = "explore"
        elif action == "question":
            role = {
                "specification_review": "interviewer",
                "test_review": "test_author",
                "test_change_review": state.get("role", "implementer"),
                "review_findings": "security_reviewer"
                if "SEC-" in response["text"]
                else "haskell_reviewer",
            }[stage]
            answer = self._ask(
                state,
                role,
                intent="explain",
                question=response["text"],
                review_packet=state["prompt"],
            )
            state["messages"].append({"question": response["text"], **answer})
            self._gate(state, stage, {**state["prompt"], **answer}, state["prompt"]["actions"])
        elif action == "approve":
            state["next"] = (
                ("worktree" if state["mode"] == "full" else "finish")
                if stage == "specification_review"
                else "commit"
            )
        elif action == "accept_red":
            state["validation"]["red_exception"] = response["text"]
            state["validations"][-1] = copy.deepcopy(state["validation"])
            save_json(Path(state["report_directory"]) / "validation.json", state["validations"])
            state["validated_fingerprint"] = self._workspace(state).fingerprint()
            self._validated(state)
        elif action == "recheck":
            state["next"] = "validate"
        elif action == "retry":
            if state["retry_node"] == "reply":
                state.update(state.pop("failed_reply"))
            state["next"] = state["retry_node"]
        elif action == "reject":
            state["messages"].append({"rejected_test_change": state["proposed_test_change"]})
            state["attempts"] = 0
            state["next"] = "implement" if state["role"] == "implementer" else "refactor"
        elif stage == "walkthrough":
            if action == "skip":
                state["next"] = "finish"
            else:
                question = state["questions"][state["question_index"]]
                feedback = self._ask(
                    state,
                    "walkthrough_feedback",
                    question=question,
                    developer_answer=response["text"],
                )
                state["walkthrough_answers"].append(
                    {**question, "answer": response["text"], **feedback}
                )
                state["question_index"] += 1
                if state["question_index"] == len(state["questions"]):
                    state["next"] = "finish"
                else:
                    self._gate(
                        state,
                        "walkthrough",
                        {
                            **state["questions"][state["question_index"]],
                            "feedback": feedback["feedback"],
                        },
                        ["answer", "skip"],
                    )
            if state["next"] == "finish" and state["mode"] == "phased":
                self._phase_complete(
                    state, "walkthrough_ready", "Walkthrough finished. Handoff can be prepared."
                )
        elif action in {"fix", "dismiss"}:
            selected = [f for f in state["findings"] if f["id"] in response["finding_ids"]]
            if action == "dismiss":
                state["dispositions"] += [
                    {
                        "id": f["id"],
                        "reason": response["text"],
                        "revision": state["review_revision"],
                    }
                    for f in selected
                ]
                state["next"] = "review_gate"
            else:
                state["selected_findings"], state["attempts"] = selected, 0
                state["next"] = "tests" if any(f["needs_test"] for f in selected) else "refactor"
                if state["mode"] == "phased":
                    next_stage = (
                        "tests_requested" if state["next"] == "tests" else "refactor_requested"
                    )
                    self._phase_complete(
                        state, next_stage, "Fix selected. Start the indicated phase to address it."
                    )
        else:
            state["messages"].append(
                {"stage": stage, "action": action, "text": response.get("text", "")}
            )
            state["attempts"] = 0
            if stage in {"interview", "specification_review"}:
                state["next"] = "interview"
            elif (
                stage in {"test_review", "test_change_review"} or state.get("role") == "test_author"
            ):
                state["next"] = "tests"
                if stage == "test_change_review" and state["mode"] == "phased":
                    self._phase_complete(
                        state,
                        "tests_requested",
                        "Test revision requested. Start the tests phase to discuss it.",
                    )
            else:
                state["next"] = "implement" if state["role"] == "implementer" else "refactor"
