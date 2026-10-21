"""Link checking records real evidence and never executes repository configuration."""

import json
import shutil
import threading
from pathlib import Path

import pytest

from lambda_harness import link_checks
from lambda_harness.process import ProcessError

from .conftest import git


def documents(repository, tmp_path):
    (repository / "target.md").write_text("# Present\n")
    (repository / "lychee.toml").write_text('preprocess = {command = "exit 99"}\n')
    (repository / "unchanged.md").write_text("[Not in scope](https://example.invalid/outside)\n")
    outside = tmp_path / "outside.md"
    outside.write_text("# Private\n")
    (repository / "escape.md").symlink_to(outside)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Existing docs")
    base = git(repository, "rev-parse", "HEAD")
    (repository / "README.md").write_text(
        "# Guide\n[Good](target.md#present)\n[Missing](absent.md)\n"
        "[Bad anchor][anchor]\n\n[anchor]: target.md#missing\n"
        "[Site route](/generated/page)\n[Outside](escape.md#private)\n"
        "[Outside via extension fallback](escape)\n"
        '\n<center><img src={require("./image.png").default} /></center>\n\n'
        "```text\nhttps://example.invalid/code-sample\n```\n"
    )
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Document change")
    return {"merge_base": base, "head": git(repository, "rev-parse", "HEAD")}


@pytest.mark.skipif(not shutil.which("lychee"), reason="Nix checks include lychee")
def test_real_markdown_links_anchors_and_scope(repository, tmp_path):
    source = documents(repository, tmp_path)
    result = link_checks.check_documentation_links(repository, source, tmp_path / "reports")
    assert result["documents"] == ["README.md"]
    assert result["unique_links"] == 3
    assert result["successful"] == 1
    broken = [issue for issue in result["issues"] if issue["status"] == "broken"]
    assert len(broken) == 2
    assert any("absent.md" in issue["url"] for issue in broken)
    assert any("#missing" in issue["url"] for issue in broken)
    assert sum(i["status"] == "unverified" for i in result["issues"]) == 4
    assert (tmp_path / "reports/documentation-links.json").is_file()
    assert git(repository, "status", "--porcelain") == ""


def test_external_failures_distinguish_broken_from_unverified(repository, tmp_path, monkeypatch):
    source = documents(repository, tmp_path)
    source.update(
        head_ref="https://github.com/example/project/pull/12", pull_request={"baseRefName": "main"}
    )
    urls = [
        "https://example.org/missing",
        "https://example.org/blocked",
        "https://example.org/slow",
        "https://github.com/example/project/blob/main/target.md",
    ]
    monkeypatch.setattr(link_checks.shutil, "which", lambda _: "/lychee")

    def run(argv, cwd, **kwargs):
        assert Path(cwd) != repository
        assert Path(argv[argv.index("--config") + 1]).read_text() == ""
        assert "GH_TOKEN" not in kwargs["env"]
        if "--dump" in argv:
            return 0, "\n".join(urls), ""
        assert "--exclude-all-private" in argv
        return (
            2,
            json.dumps(
                {
                    "error_map": {
                        "-": [
                            {"url": urls[0], "status": {"code": 404, "text": "Not found"}},
                            {"url": urls[1], "status": {"code": 403, "text": "Forbidden"}},
                            {"url": urls[3], "status": {"code": 404, "text": "Not found"}},
                        ]
                    },
                    "timeout_map": {"-": [{"url": urls[2], "status": {"text": "Timeout"}}]},
                }
            ),
            "",
        )

    monkeypatch.setattr(link_checks, "run_process", run)
    result = link_checks.check_documentation_links(repository, source, tmp_path / "reports")
    assert [item["status"] for item in result["issues"]] == [
        "broken",
        "unverified",
        "unverified",
        "unverified",
    ]
    assert "may resolve after merge" in result["issues"][2]["detail"]
    assert {item["source"] for item in result["issues"]} == {"README.md"}


def test_cancelled_or_missing_checker_does_not_claim_a_pass(repository, tmp_path, monkeypatch):
    source = documents(repository, tmp_path)
    monkeypatch.setattr(link_checks.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="needs lychee"):
        link_checks.check_documentation_links(repository, source, tmp_path / "reports")
    monkeypatch.setattr(link_checks.shutil, "which", lambda _: "/not-started")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ProcessError, match="cancelled"):
        link_checks.check_documentation_links(
            repository, source, tmp_path / "reports", cancel=cancel
        )
    assert not (tmp_path / "reports/documentation-links.json").exists()
