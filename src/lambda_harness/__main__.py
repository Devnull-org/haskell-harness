"""Start the local website; workflows are controlled in the browser."""

import argparse
import sys
from pathlib import Path

from .project_context import state_directory
from .projects import validate_repository
from .webapp import serve


def parser():
    result = argparse.ArgumentParser(
        description="Start the Lambda Harness local website.",
        epilog="Choose a persistent --state-dir outside your project checkouts and reuse it "
        "on every launch. Select backends and models in the website.",
    )
    result.add_argument(
        "--repo",
        type=Path,
        help="Open this checkout initially; projects can also be added in the website",
    )
    result.add_argument(
        "--state-dir",
        type=Path,
        metavar="PATH",
        required=True,
        help="Required directory for saved projects, conversations, memory, and worktrees. "
        "Reuse the same path to return to your work",
    )
    result.add_argument(
        "--agents-repo", type=Path, help="Git checkout containing shared agents/*.json definitions"
    )
    result.add_argument("--port", type=int, default=8765, help="Loopback port (default: 8765)")
    result.add_argument(
        "--tailscale",
        action="store_true",
        help="Enable private HTTPS phone access for your Tailscale account",
    )
    result.add_argument(
        "--tailscale-port",
        type=int,
        default=443,
        help="Tailscale HTTPS port (default: 443); requires --tailscale",
    )
    result.add_argument("--config", type=Path, help="User-owned backend and validation TOML")
    result.add_argument("--nix-flake", type=Path, help="Validation flake (default: task worktree)")
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
        if args.repo:
            args.repo = validate_repository(args.repo)
        elif args.nix_flake:
            raise ValueError("Use --repo with --nix-flake to identify the project it applies to")
        args.state_dir = args.state_dir.expanduser().resolve()
        if args.repo:
            state_directory(args.repo, args.state_dir)
        if not 0 <= args.port <= 65535:
            raise ValueError("Port must be between 0 and 65535")
        if not 1 <= args.tailscale_port <= 65535:
            raise ValueError("Tailscale port must be between 1 and 65535")
        if args.tailscale_port != 443 and not args.tailscale:
            raise ValueError("Use --tailscale with --tailscale-port")
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
