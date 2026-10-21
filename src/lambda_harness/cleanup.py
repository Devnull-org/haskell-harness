"""Remove the resources of one confirmed task without touching its source checkout."""

import shutil
from pathlib import Path

from .workspace import Workspace, git, task_branch


def cleanup_plan(repository, state_dir, state):
    """Verify ownership before offering any task files or Git references for deletion."""
    directory = state_dir / state["task_id"]
    worktree = directory / "worktree"
    branch = task_branch(state["task_id"], directory)
    if Path(state["directory"]) != directory or directory.is_symlink() or worktree.is_symlink():
        raise ValueError("Task directory or worktree is unexpected or crosses a symlink")
    if state.get("worktree", str(worktree)) != str(worktree):
        raise ValueError("Saved worktree is outside this task")
    if state.get("branch", branch) != branch:
        raise ValueError("Saved branch does not belong to this task")

    registered = None
    for block in git(repository, "worktree", "list", "--porcelain", "-z").split("\0\0"):
        fields = {}
        for entry in block.split("\0"):
            key, _, value = entry.partition(" ")
            fields[key] = value
        if "worktree" not in fields:
            continue
        path = Path(fields["worktree"]).resolve()
        if path == worktree:
            if fields.get("branch") != f"refs/heads/{branch}" or "locked" in fields:
                raise ValueError(
                    "Task worktree has a different branch or is locked; inspect it first"
                )
            registered = fields
        elif fields.get("branch") == f"refs/heads/{branch}" or path.is_relative_to(directory):
            raise ValueError(
                "Task branch or directory is used by another worktree; inspect it first"
            )

    revision = git(repository, "branch", "--list", branch, "--format=%(objectname)")
    fingerprint = None
    if worktree.exists():
        if registered is None:
            raise ValueError("Task worktree is no longer registered; inspect it before deleting")
        common = ("rev-parse", "--path-format=absolute", "--git-common-dir")
        if (
            Path(git(worktree, *common)).resolve() != Path(git(repository, *common)).resolve()
            or Path(git(worktree, "rev-parse", "--show-toplevel")).resolve() != worktree
        ):
            raise ValueError("Task worktree belongs to a different repository")
        fingerprint = Workspace(worktree).fingerprint()
    if revision and not registered and "branch" not in state:
        raise ValueError("Branch ownership was not recorded; inspect it before deleting")
    return {
        "task_id": state["task_id"],
        "stage": state["stage"],
        "directory": str(directory),
        "worktree": str(worktree),
        "branch": branch,
        "revision": revision or None,
        "fingerprint": fingerprint,
        "registered_worktree": registered is not None,
    }


def remove_resources(repository, plan):
    """Keep checkpoint deletion last so an interrupted cleanup can be retried."""
    if plan["registered_worktree"]:
        git(repository, "worktree", "remove", "--force", plan["worktree"])
    if plan["revision"]:
        git(repository, "branch", "-D", plan["branch"])
    directory = Path(plan["directory"])
    if directory.exists():
        shutil.rmtree(directory)
