"""Publish a developer-selected finding with the existing GitHub CLI login."""

import json
import os
import re

from .process import run_process
from .review_source import PR_URL, _github_repository
from .workspace import digest, git


def comment_body(finding):
    return (
        f"**{finding['title']}**\n\n"
        f"{finding['impact']}\n\n"
        f"{finding['evidence']}\n\n"
        f"Suggested correction: {finding['recommendation']}\n\n"
        f"_{finding['severity'].capitalize()} severity · {finding['confidence']}_"
    )


def _right_lines(patch):
    line = None
    for text in patch.splitlines():
        if match := re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", text):
            line = int(match[1])
        elif line is not None and text.startswith((" ", "+")):
            yield line
            line += 1


def post_review_comment(repository, source, role, finding, *, cancel=None, progress=None):
    """Post one inline comment, reconciling retries against GitHub before writing."""
    match = PR_URL.fullmatch(source["head_ref"])
    if not match or not source.get("pull_request"):
        raise ValueError("Posting requires a review started from a GitHub PR URL")
    owner_repo, number = match.groups()
    if not any(
        _github_repository(git(repository, "remote", "get-url", remote)) == owner_repo.lower()
        for remote in git(repository, "remote").splitlines()
    ):
        raise ValueError("The PR does not belong to a configured project remote")
    location = re.fullmatch(r"(.+):([1-9][0-9]*)(?:[-–][0-9]+)?", finding["location"])
    if not location:
        raise ValueError("The finding needs a repository-relative file:line location")
    path, line = location[1], int(location[2])
    endpoint = f"repos/{owner_repo}/pulls/{number}"
    key = digest([owner_repo.lower(), number, source["head"], role, finding])
    marker = f"<!-- lambda-harness:{key} -->"
    body = comment_body(finding) + "\n\n" + marker

    def api(path, *, payload=None, pages=False):
        argv = ["gh", "api", "--hostname", "github.com", "--method", "GET", path]
        if pages:
            argv += ["--paginate", "--slurp"]
        if payload is not None:
            argv[5] = "POST"
            argv += ["--input", "-"]
        code, out, err = run_process(
            argv,
            repository,
            stdin=json.dumps(payload) if payload is not None else None,
            timeout=120,
            cancel=cancel,
            env={**os.environ, "GH_PROMPT_DISABLED": "1"},
        )
        if code:
            raise RuntimeError(f"GitHub action failed: {(err or out).strip()}")
        return json.loads(out)

    if progress:
        progress(f"Checking GitHub PR #{number} before posting {finding['id']}")
    current = api(endpoint)
    if current.get("state") != "open" or current.get("head", {}).get("sha") != source["head"]:
        raise ValueError("The PR is closed or its head changed. Review the current revision first.")
    for page in api(endpoint + "/comments?per_page=100", pages=True):
        for comment in page:
            if (
                comment.get("commit_id") == source["head"]
                and comment.get("path") == path
                and (marker in comment.get("body", "") or comment.get("body") == body)
            ):
                result = comment
                break
        else:
            continue
        break
    else:
        files = api(endpoint + "/files?per_page=100", pages=True)
        changed = next((f for page in files for f in page if f["filename"] == path), None)
        if not changed or line not in set(_right_lines(changed.get("patch", ""))):
            raise ValueError("The finding's line is not in the current PR diff; it was not posted")
        result = api(
            endpoint + "/comments",
            payload={
                "body": body,
                "commit_id": source["head"],
                "path": path,
                "line": line,
                "side": "RIGHT",
            },
        )
    url = result.get("html_url", "")
    if not re.fullmatch(
        rf"https://github\.com/{re.escape(owner_repo)}/pull/{number}#discussion_r[0-9]+",
        url,
        re.IGNORECASE,
    ):
        raise RuntimeError(
            "GitHub did not return a review comment URL. Check the PR before retrying."
        )
    return {"role": role, "finding_id": finding["id"], "key": key, "url": url}
