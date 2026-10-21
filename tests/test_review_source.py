import json

import pytest

from haskell_harness.process import run_process
from haskell_harness.review_source import resolve_review

from .conftest import IMPLEMENTED, SOURCE, git


def test_github_pr_uses_metadata_and_fetches_without_moving_branches(repository, monkeypatch):
    base = git(repository, "rev-parse", "HEAD")
    (repository / SOURCE).write_text(IMPLEMENTED)
    git(repository, "commit", "-am", "Change")
    head = git(repository, "rev-parse", "HEAD")
    git(repository, "remote", "add", "origin", "git@github.com:Example/Project.git")
    metadata = {
        "baseRefName": "main",
        "baseRefOid": base,
        "headRefOid": head,
        "title": "Fix increment",
        "body": "Original request",
        "url": "https://github.com/Example/Project/pull/12",
    }
    calls = []

    def run(argv, cwd, **kwargs):
        calls.append(argv)
        return 0, json.dumps(metadata) if argv[0] == "gh" else "", ""

    monkeypatch.setattr("haskell_harness.review_source.run_process", run)
    source = resolve_review(repository, "unused", metadata["url"])
    assert source["base"] == base and source["head"] == head
    assert source["pull_request"]["body"] == "Original request"
    assert calls == [
        [
            "gh",
            "pr",
            "view",
            metadata["url"],
            "--json",
            "baseRefName,baseRefOid,headRefOid,url,title,body",
        ],
        [
            "git",
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--refmap=",
            "--",
            "origin",
            "refs/heads/main",
            "refs/pull/12/head",
        ],
    ]
    assert git(repository, "rev-parse", "HEAD") == head
    assert git(repository, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "head", ["https://example.com/a/pull/12", "https://github.com/Other/Project/pull/12"]
)
def test_wrong_pr_repository_is_rejected_before_network_access(repository, monkeypatch, head):
    monkeypatch.setattr(
        "haskell_harness.review_source.run_process", lambda *a, **kw: pytest.fail("Network access")
    )
    with pytest.raises(ValueError):
        resolve_review(repository, "main", head)


def test_pr_fetch_keeps_local_refs_and_fetch_head_unchanged(repository, tmp_path, monkeypatch):
    base = git(repository, "rev-parse", "HEAD")
    (repository / SOURCE).write_text(IMPLEMENTED)
    git(repository, "commit", "-am", "Change")
    head = git(repository, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    git(repository, "clone", "--bare", str(repository), str(remote))
    git(remote, "update-ref", "refs/pull/12/head", head)
    git(repository, "remote", "add", "origin", "https://github.com/example/project.git")
    git(repository, "update-ref", "refs/remotes/origin/main", base)
    refs = git(repository, "show-ref")
    fetch_head = repository / ".git/FETCH_HEAD"
    fetch_head.write_text("Existing fetch information\n")

    def run(argv, cwd, **kwargs):
        if argv[0] == "gh":
            return (
                0,
                json.dumps({"baseRefName": "main", "baseRefOid": base, "headRefOid": head}),
                "",
            )
        # Run the real fetch against a fixture remote, using the production flags.
        argv = [str(remote) if arg == "origin" else arg for arg in argv]
        return run_process(argv, cwd, **kwargs)

    monkeypatch.setattr("haskell_harness.review_source.run_process", run)
    source = resolve_review(repository, "ignored", "https://github.com/example/project/pull/12")
    assert source["head"] == head
    assert git(repository, "show-ref") == refs
    assert fetch_head.read_text() == "Existing fetch information\n"
