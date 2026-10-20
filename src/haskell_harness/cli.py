"""Terminal interface for local developer conversations and explicit review gates."""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from .cancellation import ModelCancellation
from .config import RoutedAgent, load_settings
from .discovery import SelectionCancelled, SelectionRequired, select_backend
from .project_context import ContextStore, state_directory
from .terminal import read_message, spinner
from .validation import LocalExecutor
from .workflow import PHASE_NODES, Harness
from .workflow_help import WORKFLOW_HELP
from .workspace import git


def parser():
    result = argparse.ArgumentParser(
        description="Haskell Harness: local, human-guided TDD workflows",
        epilog="Run 'haskell-harness help' for a step-by-step workflow guide.",
    )
    result.add_argument(
        "--repo", type=Path, help="Target Git checkout (required for task commands)"
    )
    result.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "haskell-harness",
        help="Directory for saved tasks and worktrees (default: user state directory)",
    )
    result.add_argument(
        "--nix-flake",
        type=Path,
        help="Dependency flake (default: task worktree; --repo for doctor)",
    )
    result.add_argument("--config", type=Path, help="User-owned backend configuration (TOML)")
    result.add_argument(
        "--backend", help="Backend profile for all agents, or auto to check and choose (default)"
    )
    result.add_argument("--model", help="Model override for all agents")
    result.add_argument("--agent-timeout", type=float, help="Override backend timeout in seconds")
    result.add_argument(
        "--check-timeout",
        type=float,
        default=3600,
        help="Timeout for each validation command in seconds (default: 3600)",
    )
    result.add_argument(
        "--unsigned-commits",
        action="store_true",
        help="Explicitly disable signing for harness commits only",
    )
    result.add_argument(
        "--json", action="store_true", help="Print the current gate as JSON and exit"
    )
    sub = result.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Start a saved task with the requirements interview")
    start.add_argument("task_id", help="Unique task name: letters, digits, underscores, or hyphens")
    start.add_argument(
        "request", nargs="?", help="Describe the feature or bug (multiline prompt if omitted)"
    )
    start.add_argument(
        "--base",
        help="Base branch, tag, or commit; prompted in a terminal, required with --json or scripts",
    )
    start.add_argument(
        "--interview-only",
        action="store_true",
        help="Compatibility option: start already stops after the interview; start tests with run",
    )
    start.add_argument(
        "--walkthrough",
        action="store_true",
        help="Require an explicitly started code walkthrough before handoff",
    )
    explore = sub.add_parser(
        "explore",
        help="Inspect code and discuss options without a requirements interview or coding",
    )
    explore.add_argument("task_id", help="Unique name for the saved exploration")
    explore.add_argument(
        "request", nargs="?", help="Question to investigate (multiline prompt if omitted)"
    )
    explore.add_argument(
        "--base",
        help="Base branch, tag, or commit; prompted in a terminal, required with --json or scripts",
    )
    restart = sub.add_parser(
        "restart", help="Start a fresh interview or exploration while preserving the old task"
    )
    restart.add_argument("task_id", help="Existing task to start over from")
    restart.add_argument(
        "request", nargs="?", help="Replacement request (default: original request)"
    )
    restart.add_argument("--as", dest="new_task_id", help="New task name (default: TASK-restart-N)")
    restart.add_argument(
        "--base", help="New base branch or commit (default: original pinned commit)"
    )
    abandon = sub.add_parser(
        "abandon", help="Permanently delete a saved task, its local branch, and its worktree"
    )
    abandon.add_argument("task_id", help="Name of the task to delete")
    abandon.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion, including uncommitted task files, without prompting",
    )
    phase = sub.add_parser("run", help="Run one available phase of a saved task")
    phase.add_argument("task_id", help="Name of the saved task")
    phase.add_argument(
        "phase", choices=PHASE_NODES, help="Phase to start; status lists available phases"
    )
    for command, description in (
        ("resume", "Continue the current phase or return to its pending question or approval"),
        ("status", "Show saved progress and available phases without running an agent"),
    ):
        child = sub.add_parser(command, help=description)
        child.add_argument("task_id", help="Name of the saved task")
    reply = sub.add_parser("respond", help="Answer a gate without entering the conversation")
    reply.add_argument("task_id", help="Name of the saved task")
    reply.add_argument("action", help="One of the actions offered by the current gate")
    reply.add_argument("--text", default="", help="Answer, question, revision request, or reason")
    reply.add_argument("--token", help="Approval token from status --json")
    reply.add_argument(
        "--finding",
        action="append",
        default=[],
        help="Finding ID to fix or dismiss; repeat for multiple findings",
    )
    sub.add_parser("doctor", help="Check tools and backend configuration; --repo is optional")
    sub.add_parser("backends", help="List configured backend profiles")
    sub.add_parser("help", help="Walk through the workflow with commands and approval steps")
    browser = sub.add_parser("serve", help="Open workflows through a local browser interface")
    browser.add_argument("--port", type=int, default=8765, help="Loopback port (default: 8765)")
    context = sub.add_parser("context", help="Inspect or refresh shared project knowledge")
    context_commands = context.add_subparsers(dest="context_command", required=True)
    refresh = context_commands.add_parser(
        "refresh", help="Save reference sources from the target's committed HEAD"
    )
    refresh.add_argument(
        "--profile", choices=["hydra"], help="Import Hydra protocol and state machine sources"
    )
    refresh.add_argument(
        "--source",
        action="append",
        default=[],
        help="Additional tracked source path; repeat for multiple files",
    )
    show = context_commands.add_parser(
        "show", help="Show saved protocol details and source references"
    )
    show.add_argument(
        "--snapshot", help="Snapshot ID from context history (default: latest selection)"
    )
    show.add_argument("--source", help="Print one saved source in full")
    context_commands.add_parser("history", help="List immutable project context snapshots")
    use = context_commands.add_parser("use", help="Select context for a saved task between phases")
    use.add_argument("task_id", help="Name of the saved task")
    use.add_argument("--snapshot", help="Snapshot ID to select (default: latest selection)")
    return result


def render(view):
    print(f"\n{view['task_id']}: {view['stage'].replace('_', ' ')}")
    if view.get("selected_backend"):
        print(f"Selected backend for this session: {view['selected_backend']}")
    if view.get("restarted_from"):
        print(f"Fresh attempt from: {view['restarted_from']} (previous task preserved)")
    if view.get("base"):
        print(f"Base: {view.get('base_ref', 'saved commit')} ({view['base']})")
    if view.get("worktree"):
        print(f"Worktree: {view['worktree']}")
    print(f"Reports: {view['report_directory']}")
    if view.get("project_context_id"):
        print(f"Project context: {view['project_context_id']}")
    prompt = view.get("prompt", {})
    for key in ("error", "guidance", "explanation", "summary", "answer", "feedback"):
        if prompt.get(key):
            print(f"\n{prompt[key]}")
    if prompt.get("diagnostics"):
        print(f"\nBackend diagnostics: {prompt['diagnostics']}")
    if prompt.get("available_phases"):
        print(f"\nAvailable phases: {', '.join(prompt['available_phases'])}")
        print(f"Start one explicitly with: run {view['task_id']} PHASE")
    if prompt.get("exploration"):
        result = prompt["exploration"]
        print(f"\n{result['summary']}\n\n{result['analysis']}")
        if result["references"]:
            print("\nReferences:")
            for reference in result["references"]:
                print(f"  {reference}")
        if result["open_questions"]:
            print("\nOpen questions (optional):")
            for question in result["open_questions"]:
                print(f"  {question}")
        print(f"\nSaved analysis: {prompt['report']}")
    if "specification" in prompt:
        spec = prompt["specification"]
        print(f"\n{spec['problem']}\n\nExpected behavior: {spec['expected_behavior']}")
        for criterion in spec["acceptance_criteria"]:
            print(f"  {criterion['id']}: {criterion['description']}")
        for key in ("out_of_scope", "constraints", "packages", "assumptions", "open_questions"):
            if spec[key]:
                print(f"{key.replace('_', ' ').capitalize()}: {'; '.join(spec[key])}")
    for question in prompt.get("questions", []):
        print(f"\n{question}")
    if prompt.get("question"):
        print(f"\n{prompt['question']}\nReference: {prompt.get('reference', '')}")
    if prompt.get("diff"):
        print(f"\n{prompt['diff']}")
    if prompt.get("coverage"):
        print("\nTest coverage:")
        for criterion, tests in prompt["coverage"].items():
            print(f"  {criterion}: {', '.join(tests)}")
    if prompt.get("proposal"):
        print("\nProposed changes (not applied):")
        print(json.dumps(prompt["proposal"], indent=2, ensure_ascii=False))
    if prompt.get("validation"):
        validation = prompt["validation"]
        print(f"\nValidation: {validation['status']}")
        print(f"Full logs: {validation['log_directory']}")
        for name, command in validation["commands"].items():
            print(f"  {name}: exit {command['exit_code']}")
            if command["exit_code"]:
                print(command["stderr_tail"] or command["stdout_tail"])
    for finding in prompt.get("findings", []):
        print(
            f"\n{finding['id']} [{finding['severity']}, {finding['confidence']}]: "
            f"{finding['title']}"
        )
        for key in ("location", "conditions", "sequence", "impact", "evidence", "recommendation"):
            value = finding[key]
            print(
                f"  {key.capitalize()}: {' → '.join(value) if isinstance(value, list) else value}"
            )


def abandon_task(harness, task_id, *, yes=False, interactive=True):
    plan = harness.abandon_plan(task_id)
    if not yes:
        if not interactive:
            raise ValueError("Abandoning without a terminal requires --yes; nothing was deleted")
        print(f"\nPermanently delete task {task_id!r} ({plan['stage'].replace('_', ' ')})?")
        print(f"Task files: {plan['directory']}")
        print(f"Worktree: {plan['worktree']}")
        print(f"Local branch: {plan['branch']}")
        print("This deletes the conversation, reports, task commits, and uncommitted task files.")
        print("The source checkout, shared project knowledge, and other tasks are kept.")
        confirmation = read_message(f"Type {task_id!r} to delete it, or press Enter to cancel: ")
        if confirmation != task_id:
            print("Cancelled. The task is still saved.")
            return None
    return harness.abandon(task_id, confirmation_token=plan["confirmation_token"])


def conversation(harness, view, *, selected_backend=None):
    while True:
        if selected_backend:
            view = {**view, "selected_backend": selected_backend}
        render(view)
        actions = view.get("prompt", {}).get("actions", [])
        if not actions:
            return view
        print(f"\nActions: {', '.join(actions)}, quit (resume later), abandon (delete task)")
        default_action = next(
            (action for action in ("answer", "question") if action in actions), None
        )
        if view["stage"] == "agent_paused":
            default_action = "revise"
        if view["stage"] == "agent_paused":
            print("Type corrected instructions, or 'revise' to edit the previous message.")
        elif default_action == "answer":
            print("Type your answer here and press Enter; no command prefix is needed.")
        elif view["stage"] == "exploration":
            print("Type a follow-up question, or 'done' to finish the exploration.")
        elif default_action == "question":
            print(
                "Type a question here, or use an action above. Approval requires 'approve' alone."
            )
        line = read_message()
        if not line.strip():
            continue
        single_line = "\n" not in line and "\r" not in line
        if single_line and line.strip() in {"quit", "exit"}:
            return view
        if single_line and line.strip() == "abandon":
            try:
                result = abandon_task(harness, view["task_id"])
                if result:
                    print(f"Abandoned {view['task_id']!r}. The saved task has been deleted.")
                    return result
            except (ValueError, RuntimeError, OSError) as error:
                print(f"Cannot abandon task: {error}")
                view = harness.inspect(view["task_id"])
            continue
        command = re.fullmatch(r"(\w+)(?:[ \t]+|\n|$)(.*)", line.lstrip(" \t"), flags=re.DOTALL)
        action, text = command.groups() if command else ("", "")
        standalone = {"approve", "recheck", "retry", "reject", "skip", "done"}
        if action not in actions or (action in standalone and (not single_line or text.strip())):
            if default_action:
                action, text = default_action, line
            else:
                print(
                    "Choose one of the listed actions. Questions and answers never approve a gate."
                )
                continue
        fields = {}
        if action in {"approve", "accept_red"}:
            fields["approval_token"] = view["prompt"]["approval_token"]
        if action in {"fix", "dismiss"}:
            fields["finding_ids"] = (text or input("Finding IDs, separated by spaces: ")).split()
            text = ""
        if action in {"answer", "question", "revise", "dismiss", "accept_red"}:
            fields["text"] = (
                text
                if text.strip()
                else read_message(
                    "Your response: ",
                    default=view["prompt"].get("draft", "")
                    if view["stage"] == "agent_paused"
                    else "",
                )
            )
        try:
            view = harness.respond(view["task_id"], action, **fields)
        except (ValueError, RuntimeError, OSError) as error:
            print(f"Cannot continue: {error}")


def doctor(args, agent):
    okay = True
    tools = ["git"]
    if args.repo or args.nix_flake:
        tools.append("nix")
    for executable in tools:
        found = shutil.which(executable)
        print(f"{executable}: {found or 'missing'}")
        okay = okay and bool(found)
        if executable == "nix" and not found:
            print("Nix is required for the target's validation, not for running the harness")
    if args.repo:
        repository = args.repo.expanduser().resolve()
        if not repository.is_dir():
            raise ValueError(f"Target checkout does not exist: {repository}")
        print(f"Target checkout: {repository}")
    else:
        repository = Path.cwd()
        print("No target checkout selected; checking the harness and backends only")
    if args.nix_flake or args.repo:
        flake = (args.nix_flake or args.repo).expanduser().resolve()
        print(f"Target dependency flake: {flake}")
        if not (flake / "flake.nix").is_file():
            print("Missing flake.nix; select the target's dependency environment with --nix-flake")
            okay = False
    results = agent.doctor(repository)
    for _passed, message in results:
        print(message)
    usable = any if getattr(agent, "automatic", False) else all
    okay = okay and usable(passed for passed, _ in results)
    if getattr(agent, "automatic", False):
        print("Task commands also check generation, then select a backend or ask you to choose.")
    return 0 if okay else 1


def context_command(args):
    if Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve() != args.repo:
        raise ValueError("Repository must be the root of a Git checkout")
    if args.context_command == "use":
        with_harness = Harness(args.repo, args.state_dir, None, None)
        try:
            view = with_harness.use_context(args.task_id, args.snapshot)
            if args.json:
                print(json.dumps(view, indent=2))
            else:
                render(view)
        finally:
            with_harness.close()
        return 0
    with ContextStore(state_directory(args.repo, args.state_dir)) as store:
        if args.context_command == "history":
            result = store.history()
        elif args.context_command == "refresh":
            result = store.refresh(args.repo, profile=args.profile, sources=args.source)
        else:
            result = store.get(args.snapshot)
        if args.context_command == "show" and args.source:
            result = next((d for d in result["documents"] if d["path"] == args.source), None)
            if result is None:
                raise ValueError("That source is not in the selected context snapshot")
            print(json.dumps(result, indent=2) if args.json else result["content"])
        elif args.json:
            print(json.dumps(result, indent=2))
        elif isinstance(result, list):
            for snapshot in result:
                print(f"{snapshot['id']}  {snapshot['revision']}  {snapshot['created']}")
        else:
            print(f"Project context: {result['id']}\nSource revision: {result['revision']}")
            print("Saved committed sources; working tree edits are not imported.")
            for document in result["documents"]:
                print(f"  {document['path']}: {document['purpose']}")
            if result["missing_sources"]:
                print(f"Optional sources absent: {', '.join(result['missing_sources'])}")
            if args.context_command == "show":
                for document in result["documents"]:
                    if document["path"] == "docs/docs/protocol-overview.md":
                        print("\n" + document["content"])
                if result["state_declaration"]:
                    print("\nRecorded HeadState declaration:\n" + result["state_declaration"])
            print("Use context show --source PATH to read a saved source in full.")
    return 0


def main(argv=None):
    with ModelCancellation() as cancellation:
        return _main(argv, cancellation)


def _main(argv, cancellation):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.command == "help":
        print(json.dumps({"workflow": WORKFLOW_HELP}) if args.json else WORKFLOW_HELP)
        return 0
    if args.command not in {"doctor", "backends"} and args.repo is None:
        arguments.error("task commands require --repo with the target Git checkout")
    harness = None
    try:
        if args.repo:
            args.repo = args.repo.expanduser().resolve()
            if not args.repo.is_dir():
                raise ValueError(f"Target checkout does not exist: {args.repo}")
        if args.command == "serve":
            from .webapp import serve

            serve(args)
            return 0
        if args.command == "context":
            return context_command(args)
        if args.command == "abandon":
            harness = Harness(args.repo, args.state_dir, None, None)
            result = abandon_task(
                harness,
                args.task_id,
                yes=args.yes,
                interactive=sys.stdin.isatty() and not args.json,
            )
            if args.json:
                print(json.dumps(result))
            elif result:
                print(f"Abandoned {args.task_id!r}. The saved task has been deleted.")
            return 0
        if args.command in {"start", "explore"}:
            interactive = sys.stdin.isatty() and not args.json
            if args.base is None:
                if not interactive:
                    raise ValueError("Supply --base BRANCH_OR_COMMIT when starting without prompts")
                default_base = git(args.repo, "branch", "--show-current") or "HEAD"
                args.base = (
                    input(f"Base branch or commit [{default_base}]: ").strip() or default_base
                )
            if not args.request:
                if not interactive:
                    raise ValueError("Supply a request when starting without prompts")
                print(
                    "What do you want to explore?"
                    if args.command == "explore"
                    else "What do you want to build or fix?"
                )
                args.request = read_message()
        settings = load_settings(args.config)

        def progress(message):
            print(message, file=sys.stderr, flush=True)

        agent = RoutedAgent(
            settings,
            backend=args.backend,
            model=args.model,
            timeout=args.agent_timeout,
            progress=progress,
            cancellation=cancellation,
            activity=spinner
            if sys.stderr.isatty() and not args.json and os.environ.get("TERM") != "dumb"
            else None,
        )
        if args.command == "backends":
            names = sorted(settings.backends)
            if args.json:
                print(
                    json.dumps(
                        {"default": args.backend or settings.default_backend, "backends": names}
                    )
                )
            else:
                for name in names:
                    suffix = (
                        " (default)" if name == (args.backend or settings.default_backend) else ""
                    )
                    print(name + suffix)
            return 0
        if args.command == "doctor":
            return doctor(args, agent)
        harness = Harness(
            args.repo,
            args.state_dir,
            agent,
            LocalExecutor(nix_flake=args.nix_flake, timeout=args.check_timeout),
            unsigned_commits=args.unsigned_commits,
            progress=progress,
        )
        selected_backend = None
        if args.command != "status":
            selected_backend = select_backend(
                agent, args.repo, interactive=sys.stdin.isatty() and not args.json
            )
            progress(f"Selected backend for this session: {selected_backend}")
        if args.command == "start":
            view = harness.start(
                args.task_id,
                args.request,
                base=args.base,
                mode="interview" if args.interview_only else "phased",
                walkthrough=args.walkthrough,
            )
        elif args.command == "explore":
            view = harness.start(args.task_id, args.request, base=args.base, phase="explore")
        elif args.command == "restart":
            view = harness.restart(
                args.task_id, new_task_id=args.new_task_id, request=args.request, base=args.base
            )
        elif args.command == "run":
            view = harness.run_phase(args.task_id, args.phase)
        elif args.command == "respond":
            view = harness.respond(
                args.task_id,
                args.action,
                text=args.text,
                approval_token=args.token,
                finding_ids=args.finding,
            )
        elif args.command == "resume":
            previous = harness.inspect(args.task_id)
            if previous["stage"] == "backend_blocked":
                failed_backend = previous["prompt"]["backend_failure"]["backend"]
                progress(
                    f"Retrying saved failure from backend {failed_backend!r} "
                    f"with selected backend {selected_backend!r}"
                )
            view = harness.resume(args.task_id, retry_backend=True)
        else:
            view = harness.inspect(args.task_id)
        if selected_backend:
            view = {**view, "selected_backend": selected_backend}
        if args.json:
            print(json.dumps(view, indent=2, ensure_ascii=False))
        elif args.command in {"status", "respond"} or not sys.stdin.isatty():
            render(view)
        else:
            view = conversation(harness, view, selected_backend=selected_backend)
            return 0
        if view["stage"] == "agent_paused":
            return 130
        return 1 if view["stage"] in {"agent_error", "backend_blocked", "validation_blocked"} else 0
    except SelectionCancelled:
        print("\nBackend selection cancelled. No project request was sent.", file=sys.stderr)
        return 130
    except SelectionRequired as error:
        if args.json:
            print(json.dumps(error.view(), indent=2))
        else:
            print(f"haskell-harness: {error}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, OSError) as error:
        print(f"haskell-harness: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print(
            "\nStopped locally. Use status with the same task ID to check saved progress. "
            "Continue saved work with resume, or finish deletion with abandon.",
            file=sys.stderr,
        )
        return 130
    finally:
        if harness:
            harness.close()


if __name__ == "__main__":
    raise SystemExit(main())
