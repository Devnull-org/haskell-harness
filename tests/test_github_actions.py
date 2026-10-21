"""Requested GitHub comments are pinned, selective, and safe to retry."""

import json

import pytest

from lambda_harness.github_actions import post_review_comment

from .conftest import git

FINDING = {
    "id": "DOC-002",
    "severity": "medium",
    "location": "README.md:2",
    "title": "Clarify expiry",
    "conditions": "An approved deposit is pending",
    "sequence": ["Wait for expiry", "Retry"],
    "impact": "The retry is still blocked.",
    "evidence": "The implementation retains the deposit.",
    "confidence": "confirmed",
    "recommendation": "Explain the retention window.",
    "needs_test": False,
}


def review(rig, repository):
    harness, agent, _ = rig
    base = git(repository, "rev-parse", "HEAD")
    (repository / "README.md").write_text("# Guide\nWait for expiry.\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Documentation")
    head = git(repository, "rev-parse", "HEAD")
    git(repository, "remote", "add", "origin", "git@github.com:example/project.git")
    source = {
        "base": base,
        "base_ref": "main",
        "merge_base": base,
        "head": head,
        "head_ref": "https://github.com/example/project/pull/12",
        "pull_request": {"headRefOid": head},
    }
    agent.responses["documentation_reviewer"].append(
        {"summary": "Reviewed docs", "findings": [FINDING]}
    )
    view = harness.start_review(
        "docs", "Review docs", base="main", head=head, reviewers="documentation", source=source
    )
    return source, view


def github(monkeypatch, source):
    source = dict(source)
    comments, calls = [], []

    def run(argv, cwd, **kw):
        calls.append((argv, kw))
        if argv[5] == "POST":
            data = json.loads(kw["stdin"])
            result = {**data, "id": 123, "html_url": source["head_ref"] + "#discussion_r123"}
            comments.append(result)
        elif "/comments?" in argv[6]:
            result = [comments]
        elif "/files?" in argv[6]:
            result = [[{"filename": "README.md", "patch": "@@ -0,0 +1,2 @@\n+# Guide\n+Wait."}]]
        else:
            result = {"state": "open", "head": {"sha": source["head"]}}
        return 0, json.dumps(result), ""

    monkeypatch.setattr("lambda_harness.github_actions.run_process", run)
    return comments, calls


def test_only_requested_finding_posts_and_retry_reconciles(rig, repository, monkeypatch):
    source, view = review(rig, repository)
    comments, calls = github(monkeypatch, source)
    harness, agent, executor = rig
    token = view["review_actions"]["token"]
    for _ in range(2):
        view = harness.review_action(
            "docs",
            "post_review_comment",
            token=token,
            role="documentation_reviewer",
            finding_id="DOC-002",
        )
    assert len(comments) == 1
    assert comments[0]["commit_id"] == source["head"]
    assert comments[0]["line"] == 2 and comments[0]["side"] == "RIGHT"
    assert "retention window" in comments[0]["body"]
    assert len(view["review_actions"]["posts"]) == 1
    assert len(agent.calls) == 1 and not executor.calls
    assert all(c[0][:4] == ["gh", "api", "--hostname", "github.com"] for c in calls)
    # Recording a side effect must leave LangGraph's existing question gate usable.
    view = harness.respond("docs", "question", text="Explain the finding")
    assert view["stage"] == "standalone_review" and view["review_actions"]["posts"]
    assert harness.respond("docs", "done")["stage"] == "review_complete"


@pytest.mark.parametrize("change", ["token", "finding", "worktree", "head", "line", "remote"])
def test_invalid_or_stale_publication_never_posts(rig, repository, monkeypatch, change):
    source, view = review(rig, repository)
    comments, _ = github(monkeypatch, source)
    harness, _, _ = rig
    token, finding = view["review_actions"]["token"], "DOC-002"
    if change == "token":
        token = "stale"
    elif change == "finding":
        finding = "DOC-999"
    elif change == "worktree":
        from pathlib import Path

        (Path(view["worktree"]) / "README.md").write_text("Changed")
    elif change == "remote":
        git(repository, "remote", "remove", "origin")
    elif change in {"head", "line"}:
        if change == "head":
            source["head"] = "a" * 40
        else:
            bad_finding = {**FINDING, "location": "README.md:99"}
        with pytest.raises(ValueError):
            post_review_comment(
                repository,
                source,
                "documentation_reviewer",
                bad_finding if change == "line" else FINDING,
            )
        assert not comments
        return
    with pytest.raises(ValueError):
        harness.review_action(
            "docs",
            "post_review_comment",
            token=token,
            role="documentation_reviewer",
            finding_id=finding,
        )
    assert not comments


def test_lost_post_response_is_recovered_without_a_second_comment(rig, repository, monkeypatch):
    source, view = review(rig, repository)
    comments, _ = github(monkeypatch, source)
    import lambda_harness.github_actions as actions

    run = actions.run_process

    def lose_response(argv, *args, **kw):
        result = run(argv, *args, **kw)
        if argv[5] == "POST":
            raise RuntimeError("Connection lost after GitHub accepted the comment")
        return result

    monkeypatch.setattr(actions, "run_process", lose_response)
    harness = rig[0]
    fields = dict(
        token=view["review_actions"]["token"], role="documentation_reviewer", finding_id="DOC-002"
    )
    with pytest.raises(RuntimeError, match="Connection lost"):
        harness.review_action("docs", "post_review_comment", **fields)
    result = harness.review_action("docs", "post_review_comment", **fields)
    assert len(comments) == 1
    assert result["review_actions"]["posts"][0]["url"].endswith("#discussion_r123")
