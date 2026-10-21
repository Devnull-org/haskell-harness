"""Resolve review inputs to immutable Git objects without moving local branches."""

import json
import os
import re

from .process import run_process
from .workspace import git, resolve_base

PR_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?")


def _github_repository(url):
    match = re.fullmatch(
        r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?/?",
        url,
    )
    return match[1].lower() if match else None


def resolve_review(repository, base, head, *, cancel=None, progress=None):
    """GitHub URLs use the PR's own base; local refs use the selected comparison base."""
    head = head.strip()
    match = PR_URL.fullmatch(head)
    metadata = {}
    if match:
        remote = next(
            (
                name
                for name in git(repository, "remote").splitlines()
                if _github_repository(git(repository, "remote", "get-url", name))
                == match[1].lower()
            ),
            None,
        )
        if remote is None:
            raise ValueError("The GitHub PR must belong to a configured remote of this project")

        def run(argv):
            code, stdout, stderr = run_process(
                argv,
                repository,
                timeout=120,
                cancel=cancel,
                env={**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"},
                progress=(lambda elapsed: progress(f"Resolving PR ({elapsed:.0f}s elapsed)"))
                if progress
                else None,
            )
            if code:
                raise ValueError(f"Could not load GitHub PR: {(stderr or stdout).strip()}")
            return stdout

        if progress:
            progress("Reading the GitHub PR and fetching its revisions")
        metadata = json.loads(
            run(
                [
                    "gh",
                    "pr",
                    "view",
                    head,
                    "--json",
                    "baseRefName,baseRefOid,headRefOid,url,title,body",
                ]
            )
        )
        for key in ("baseRefOid", "headRefOid"):
            if not isinstance(metadata.get(key), str) or not re.fullmatch(
                r"[0-9a-f]{40,64}", metadata[key]
            ):
                raise ValueError("GitHub did not return a valid PR revision")
        base_ref = metadata["baseRefName"]
        # Validate the ref before passing it as a fetch refspec. No branches or FETCH_HEAD move.
        git(repository, "check-ref-format", f"refs/heads/{base_ref}")
        run(
            [
                "git",
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--refmap=",
                "--",
                remote,
                f"refs/heads/{base_ref}",
                f"refs/pull/{match[2]}/head",
            ]
        )
        base_revision = resolve_base(repository, metadata["baseRefOid"])
        head_revision = resolve_base(repository, metadata["headRefOid"])
    else:
        if "://" in head:
            raise ValueError("Use a local branch, commit, or a GitHub pull request URL")
        base_ref = base
        base_revision = resolve_base(repository, base)
        head_revision = resolve_base(repository, head)
    merge_bases = git(repository, "merge-base", "--all", base_revision, head_revision).splitlines()
    if len(merge_bases) != 1:
        raise ValueError("Review needs a single common ancestor between base and head")
    merge_base = merge_bases[0]
    if not git(repository, "diff", "--no-ext-diff", "--name-only", merge_base, head_revision):
        raise ValueError("No changes to review between the merge base and selected head")
    return {
        "base_ref": base_ref,
        "base": base_revision,
        "head_ref": head,
        "head": head_revision,
        "merge_base": merge_base,
        "pull_request": metadata or None,
    }
