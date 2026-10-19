"""Terminal interface for local developer conversations and explicit review gates."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .config import RoutedAgent, load_settings
from .validation import LocalExecutor
from .workflow import Harness


def parser():
    result = argparse.ArgumentParser(description="Local, human-guided TDD workflows for Hydra")
    result.add_argument(
        "--repo", type=Path, help="Target Git checkout (required for task commands)"
    )
    result.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "hydra-harness",
    )
    result.add_argument("--nix-flake", type=Path, help="Hydra dependency flake (default: --repo)")
    result.add_argument("--config", type=Path, help="User-owned backend configuration (TOML)")
    result.add_argument("--backend", help="Backend profile for all agents")
    result.add_argument("--model", help="Model override for all agents")
    result.add_argument("--agent-timeout", type=float, help="Override backend timeout in seconds")
    result.add_argument("--check-timeout", type=float, default=3600)
    result.add_argument(
        "--unsigned-commits",
        action="store_true",
        help="Explicitly disable signing for harness commits only",
    )
    result.add_argument(
        "--json", action="store_true", help="Print the current gate as JSON and exit"
    )
    sub = result.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Start a task from this checkout's current HEAD")
    start.add_argument("task_id")
    start.add_argument(
        "request", nargs="?", help="Describe the feature or bug (prompted if omitted)"
    )
    start.add_argument("--interview-only", action="store_true")
    start.add_argument("--walkthrough", action="store_true")
    for command in ("resume", "status"):
        child = sub.add_parser(command)
        child.add_argument("task_id")
    reply = sub.add_parser("respond", help="Answer a gate without entering the conversation")
    reply.add_argument("task_id")
    reply.add_argument("action")
    reply.add_argument("--text", default="")
    reply.add_argument("--token", help="Approval token from status --json")
    reply.add_argument("--finding", action="append", default=[])
    sub.add_parser("doctor", help="Check tools and backend configuration; --repo is optional")
    sub.add_parser("backends", help="List configured backend profiles")
    return result


def render(view):
    print(f"\n{view['task_id']}: {view['stage'].replace('_', ' ')}")
    if view.get("worktree"):
        print(f"Worktree: {view['worktree']}")
    print(f"Reports: {view['report_directory']}")
    prompt = view.get("prompt", {})
    for key in ("error", "explanation", "summary", "answer", "feedback"):
        if prompt.get(key):
            print(f"\n{prompt[key]}")
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


def conversation(harness, view):
    while True:
        render(view)
        actions = view.get("prompt", {}).get("actions", [])
        if not actions:
            return view
        print(f"\nActions: {', '.join(actions)}, quit (resume later)")
        line = input("> ").strip()
        action, _, text = line.partition(" ")
        if action in {"quit", "exit"}:
            return view
        if action not in actions:
            print("Choose one of the listed actions. Questions and answers never approve a gate.")
            continue
        fields = {}
        if action in {"approve", "accept_red"}:
            fields["approval_token"] = view["prompt"]["approval_token"]
        if action in {"fix", "dismiss"}:
            fields["finding_ids"] = (text or input("Finding IDs, separated by spaces: ")).split()
            text = ""
        if action in {"answer", "question", "revise", "dismiss", "accept_red"}:
            fields["text"] = text or input("Your response: ")
        try:
            view = harness.respond(view["task_id"], action, **fields)
        except (ValueError, RuntimeError) as error:
            print(f"Cannot continue: {error}")


def doctor(args, agent):
    okay = True
    for executable in ("git", "nix"):
        found = shutil.which(executable)
        print(f"{executable}: {found or 'missing'}")
        okay = okay and bool(found)
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
    for passed, message in agent.doctor(repository):
        print(message)
        okay = okay and passed
    return 0 if okay else 1


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.command not in {"doctor", "backends"} and args.repo is None:
        arguments.error("task commands require --repo with the target Git checkout")
    harness = None
    try:
        if args.repo:
            args.repo = args.repo.expanduser().resolve()
            if not args.repo.is_dir():
                raise ValueError(f"Target checkout does not exist: {args.repo}")
        settings = load_settings(args.config)
        agent = RoutedAgent(
            settings, backend=args.backend, model=args.model, timeout=args.agent_timeout
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
            LocalExecutor(nix_flake=args.nix_flake or args.repo, timeout=args.check_timeout),
            unsigned_commits=args.unsigned_commits,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
        )
        if args.command == "start":
            request = args.request or input("What do you want to build or fix?\n> ")
            view = harness.start(
                args.task_id,
                request,
                mode="interview" if args.interview_only else "full",
                walkthrough=args.walkthrough,
            )
        elif args.command == "respond":
            view = harness.respond(
                args.task_id,
                args.action,
                text=args.text,
                approval_token=args.token,
                finding_ids=args.finding,
            )
        elif args.command == "resume":
            view = harness.resume(args.task_id)
        else:
            view = harness.inspect(args.task_id)
        if args.json:
            print(json.dumps(view, indent=2, ensure_ascii=False))
        elif args.command in {"status", "respond"} or not sys.stdin.isatty():
            render(view)
        else:
            view = conversation(harness, view)
        return 1 if view["stage"] in {"agent_error", "validation_blocked"} else 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f"hydra-harness: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped locally. Use resume with the same task ID to continue.", file=sys.stderr)
        return 130
    finally:
        if harness:
            harness.close()


if __name__ == "__main__":
    raise SystemExit(main())
