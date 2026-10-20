"""Check configured backends without sharing the target project's context."""

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

from .backends import CommandAgent
from .failures import DESCRIPTIONS, AgentInterrupted, BackendError, failure_kind
from .process import ProcessError
from .workspace import git


class SelectionRequired(Exception):
    """Return available choices to scripts without guessing on the user's behalf."""

    def __init__(self, checks):
        self.checks = checks
        self.available = [check["name"] for check in checks if check["available"]]
        super().__init__(
            "Several backends are usable; choose one with --backend NAME"
            if self.available
            else "No backend completed the availability check; inspect the results and try again"
        )

    def view(self):
        return {
            "stage": "backend_selection",
            "error": str(self),
            "available_backends": self.available,
            "backend_checks": self.checks,
        }


class SelectionCancelled(Exception):
    """Stop selection before any project request is sent."""


def discover(agent, cwd):
    """Try login checks and one small generation request per configured backend."""
    checks = []
    progress = agent.progress or (lambda message: None)
    for name, backend in sorted(agent.settings.backends.items()):
        candidate = CommandAgent(
            backend,
            name=name,
            model=agent.model,
            timeout=min(agent.timeout or backend.timeout, 60),
            cancellation=agent.cancellation,
        )
        progress(f"Checking backend {name!r} ({backend.command[0]})")
        check = {"name": name, "executable": backend.command[0], "available": False}
        indicator = (
            agent.activity(f"Checking backend {name!r}") if agent.activity else nullcontext()
        )
        try:
            with indicator:
                executable = candidate.executable(cwd)
                if not executable:
                    raise BackendError("Executable not found", backend=name, kind="unavailable")
                passed, message = candidate.doctor(cwd)
                if not passed:
                    raise BackendError(message, backend=name, kind=failure_kind(message))
                # Relative commands still refer to the configured checkout, not the scratch repo.
                candidate.backend = backend.model_copy(
                    update={"command": [executable, *backend.command[1:]]}
                )
                candidate.progress = None if agent.activity else agent.progress
                with TemporaryDirectory(prefix="haskell-harness-backend-") as temporary:
                    scratch = Path(temporary)
                    git(scratch, "-c", "init.templateDir=", "init", "--quiet")
                    candidate.check(scratch)
            check.update(available=True, message="Generation check passed")
        except AgentInterrupted as error:
            raise SelectionCancelled from error
        except (BackendError, ProcessError) as error:
            if error.kind == "cancelled":
                raise SelectionCancelled from error
            check.update(kind=error.kind, message=DESCRIPTIONS[error.kind])
        except (ValueError, RuntimeError, OSError) as error:
            check.update(kind="provider", message=str(error)[-1000:])
        checks.append(check)
        progress(f"Backend {name!r}: {check['message']}")
    return checks


def select_backend(agent, cwd, *, interactive):
    """Select the only usable backend or ask the developer to pick one."""
    if not agent.automatic:
        return agent.backend or agent.settings.default_backend
    checks = discover(agent, cwd)
    choice = SelectionRequired(checks)
    if not choice.available or (len(choice.available) > 1 and not interactive):
        raise choice
    if len(choice.available) == 1:
        selected = choice.available[0]
    else:
        print("\nSeveral backends are usable. Choose one for this session:")
        for index, name in enumerate(choice.available, 1):
            print(f"  {index}. {name}")
        while True:
            try:
                answer = input("Backend number or name (quit to cancel): ").strip()
            except (EOFError, KeyboardInterrupt) as error:
                raise SelectionCancelled from error
            if answer in {"quit", "exit"}:
                raise SelectionCancelled
            if answer in choice.available:
                selected = answer
                break
            if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(choice.available):
                selected = choice.available[int(answer) - 1]
                break
            print("Choose one of the listed backend numbers or names.")
    agent.backend = selected
    return selected
