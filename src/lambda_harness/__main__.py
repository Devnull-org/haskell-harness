"""Start the local website; workflows are controlled in the browser."""

import argparse
import sys
from pathlib import Path

from .paths import default_state_root
from .project_context import state_directory
from .webapp import serve
from .workspace import git


def parser():
    result = argparse.ArgumentParser(
        description="Start the Lambda Harness local website.",
        epilog="Open the printed URL to explore, plan, review, and manage saved work.",
    )
    result.add_argument("--repo", type=Path, required=True, help="Target Git checkout")
    result.add_argument(
        "--state-dir",
        type=Path,
        help="Saved tasks and worktrees; defaults to the user state directory, "
        "reusing existing project data",
    )
    result.add_argument(
        "--agents-repo", type=Path, help="Git checkout containing shared agents/*.json definitions"
    )
    result.add_argument("--port", type=int, default=8765, help="Loopback port (default: 8765)")
    result.add_argument("--config", type=Path, help="User-owned backend and validation TOML")
    result.add_argument("--nix-flake", type=Path, help="Validation flake (default: task worktree)")
    result.add_argument("--backend", help="Initial backend selection (default: configured choice)")
    result.add_argument("--model", help="Model override for all agents")
    result.add_argument("--agent-timeout", type=float, help="Agent timeout in seconds")
    result.add_argument(
        "--check-timeout", type=float, default=3600, help="Validation timeout (default: 3600s)"
    )
    result.add_argument(
        "--unsigned-commits", action="store_true", help="Disable signing for harness commits only"
    )
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        args.repo = args.repo.expanduser().resolve()
        if not args.repo.is_dir():
            raise ValueError(f"Target checkout does not exist: {args.repo}")
        if Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve() != args.repo:
            raise ValueError("Repository must be the root of a Git checkout")
        if args.state_dir is None:
            args.state_dir = default_state_root(args.repo)
        state_directory(args.repo, args.state_dir)
        if not 0 <= args.port <= 65535:
            raise ValueError("Port must be between 0 and 65535")
        if args.check_timeout <= 0 or (args.agent_timeout is not None and args.agent_timeout <= 0):
            raise ValueError("Timeouts must be positive")
        serve(args)
    except KeyboardInterrupt:
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Lambda Harness: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
