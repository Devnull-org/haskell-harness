"""Local worktree changes with explicit boundaries around tests and validation."""

import difflib
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath

from .process import run_process


def git(cwd, *args):
    code, out, err = run_process(["git", *args], cwd, timeout=120)
    if code:
        raise RuntimeError(f"git {args[0]} failed: {err.strip()}")
    return out.rstrip("\n")


def resolve_base(repository, ref):
    """Resolve a local Git reference to the commit that a task will keep using."""
    if not ref.strip() or ref.startswith("-"):
        raise ValueError("Base must be a branch, tag, or commit")
    try:
        return git(repository, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    except RuntimeError as error:
        raise ValueError(
            f"Cannot resolve base {ref!r} to a commit. Choose a local branch, tag, or commit; "
            "fetch a remote branch first if needed."
        ) from error


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_task_name(name):
    """Keep task names readable and safe as a single directory name."""
    if not isinstance(name, str) or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9 _-]{0,62}[A-Za-z0-9_-])?", name
    ):
        raise ValueError(
            "Task name must use 1–64 letters, digits, spaces, underscores, or hyphens. "
            "Start with a letter or digit and omit leading or trailing spaces."
        )


def task_branch(name, directory):
    """Git branches cannot contain spaces; the directory hash keeps names distinct."""
    validate_task_name(name)
    return f"harness/{name.replace(' ', '-')}-{digest(str(directory))[:8]}"


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def is_test_path(path):
    return bool({"test", "tests", "testlib", "golden", "goldens"} & set(PurePosixPath(path).parts))


def cabal_library_dependencies_only(before, after):
    """Allow main-library dependency edits, keeping test and shared settings exact."""
    if before is None or after is None:
        return False

    def protected_lines(content):
        # Braced layout and tabs need a Cabal parser; keep those edits under review.
        if any(character in content for character in "{}\t"):
            return None
        result = []
        library = False
        dependency_indent = None
        for line in content.splitlines(keepends=True):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if stripped and not stripped.startswith("--") and indent == 0:
                library = stripped.split("--", 1)[0].strip() == "library"
            if dependency_indent is not None:
                if not stripped or stripped.startswith("--") or indent > dependency_indent:
                    continue
                dependency_indent = None
            if library and re.match(r" +build-depends\s*:", line, re.IGNORECASE):
                dependency_indent = indent
                continue
            result.append(line)
        return result

    original = protected_lines(before)
    return original is not None and original == protected_lines(after)


def protected_configuration(path):
    path = path.lower()
    parts = PurePosixPath(path).parts
    return (
        parts[0] in {"nix", ".github", ".config", ".cursor", ".claude"}
        or parts[-1]
        in {"justfile", "makefile", "agents.md", "claude.md", "flake.nix", "flake.lock"}
        or path.startswith("cabal.project")
    )


class Workspace:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @property
    def revision(self):
        return git(self.path, "rev-parse", "HEAD")

    def files(self):
        return [
            p
            for p in git(
                self.path, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
            ).split("\0")
            if p
        ]

    def fingerprint(self):
        untracked = {}
        for name in git(self.path, "ls-files", "-z", "--others", "--exclude-standard").split("\0"):
            if name:
                path = self.path / name
                untracked[name] = hashlib.sha256(
                    os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
                ).hexdigest()
        return digest([self.revision, git(self.path, "diff", "--binary", "HEAD"), untracked])

    def test_manifest(self, additional=()):
        names = set(additional) | {p for p in self.files() if is_test_path(p)}
        return {name: self.file_hash(name) for name in sorted(names)}

    def file_hash(self, name):
        path = self.path / name
        if path.is_symlink():
            return "symlink:" + os.readlink(path)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    def safe_path(self, name):
        pure = PurePosixPath(name)
        if (
            not name
            or pure.is_absolute()
            or ".." in pure.parts
            or any(part.lower() == ".git" for part in pure.parts)
            or str(pure) != name
            or "\\" in name
            or "\x00" in name
            or pure.name.lower() == "changelog.md"
        ):
            raise ValueError(f"Disallowed file path: {name}")
        path = self.path / name
        for candidate in (path, *path.parents):
            if candidate == self.path:
                break
            if candidate.is_symlink():
                raise ValueError(f"File path crosses a symlink: {name}")
        if not path.resolve().is_relative_to(self.path):
            raise ValueError(f"File path escapes the worktree: {name}")
        return path

    def proposed_diff(self, changes):
        patches = []
        for change in changes:
            name, content = change["path"], change["content"]
            path = self.safe_path(name)
            before = path.read_text() if path.is_file() else ""
            patches.extend(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    (content or "").splitlines(keepends=True),
                    fromfile=f"a/{name}" if path.is_file() else "/dev/null",
                    tofile=f"b/{name}" if content is not None else "/dev/null",
                )
            )
        return "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in patches
        )

    def apply(self, changes, *, tests_only=False, approved_paths=()):
        planned = []
        seen = set()
        for change in changes:
            name, content = change["path"], change["content"]
            path = self.safe_path(name)
            if name in seen:
                raise ValueError(f"Duplicate file path: {name}")
            seen.add(name)
            if protected_configuration(name) and name not in approved_paths:
                raise ValueError(f"Validation/configuration path requires a manual change: {name}")
            if tests_only and not (is_test_path(name) or name.endswith(".cabal")):
                raise ValueError(f"Test author cannot change production file path: {name}")
            if content is not None and len(content.encode()) > 2_000_000:
                raise ValueError(f"File proposal is too large: {name}")
            planned.append((path, content))
        for path, content in planned:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        # Intent-to-add makes new files visible in the developer's diff and Nix source.
        for path, content in planned:
            if content is not None:
                git(self.path, "add", "--intent-to-add", "--", str(path))

    def diff(self, base="HEAD"):
        return git(self.path, "diff", "--no-ext-diff", "--no-color", base)

    def changed_paths(self):
        return [
            name for name in git(self.path, "diff", "--name-only", "-z", "HEAD").split("\0") if name
        ]

    def commit(self, message, *, expected, step, unsigned=False):
        trailer = f"Harness-Step: {step}"
        if trailer in git(self.path, "log", "-1", "--format=%B"):
            if git(self.path, "status", "--porcelain"):
                raise ValueError(
                    "Worktree changed after the recovered commit; recheck before proceeding"
                )
            parent = git(self.path, "rev-parse", "HEAD^")
            recovered = digest([parent, git(self.path, "diff", "--binary", parent), {}])
            if recovered != expected:
                raise ValueError("Recovered commit differs from validated content; recheck it")
            return self.revision
        if self.fingerprint() != expected:
            raise ValueError("Worktree changed since validation; rerun checks before committing")
        if not self.diff():
            return self.revision
        git(self.path, "add", "--all")
        expected_tree = git(self.path, "write-tree")
        options = ["-c", "commit.gpgsign=false"] if unsigned else []
        git(self.path, *options, "commit", "-m", message, "-m", trailer)
        if git(self.path, "rev-parse", "HEAD^{tree}") != expected_tree or git(
            self.path, "status", "--porcelain"
        ):
            raise ValueError("A commit hook changed validated content; revalidate this commit")
        return self.revision
