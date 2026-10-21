"""Check links in changed documentation without executing repository scripts."""

import json
import os
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit, urlunsplit

from .process import run_process
from .workspace import git, save_json

DOC_EXTENSIONS = {".md", ".mdx", ".markdown", ".html", ".htm"}


def check_documentation_links(worktree, source, reports, *, cancel=None, progress=None):
    executable = shutil.which("lychee")
    if not executable:
        raise ValueError(
            "Link checking needs lychee. The Nix launcher includes it; otherwise install lychee."
        )
    root = Path(worktree).resolve()
    names = git(
        root,
        "diff",
        "--no-ext-diff",
        "--name-only",
        "-z",
        "--diff-filter=ACMR",
        source["merge_base"],
        source["head"],
    ).split("\0")
    documents = [name for name in names if Path(name).suffix.lower() in DOC_EXTENSIONS]
    sources, skipped = {}, []
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SSL_CERT_FILE", "SSL_CERT_DIR", "NIX_SSL_CERT_FILE", "SYSTEMROOT"}
    }
    # An empty working directory and config prevent repository preprocessing hooks
    # and token/header settings from affecting a read-only check.
    with TemporaryDirectory(prefix="lambda-links-") as directory:
        config = Path(directory) / "lychee.toml"
        config.write_text("")
        common = [
            executable,
            "--config",
            str(config),
            "--root-dir",
            str(root),
            "--no-progress",
            "--exclude-all-private",
            "--scheme",
            "http",
            "--scheme",
            "https",
            "--scheme",
            "file",
        ]
        for number, name in enumerate(documents, 1):
            path = root / name
            if not path.resolve().is_relative_to(root):
                skipped.append(
                    {
                        "source": name,
                        "url": name,
                        "status": "unverified",
                        "detail": "Document resolves outside the worktree",
                    }
                )
                continue
            if progress:
                progress(f"Extracting documentation links ({number}/{len(documents)})")
            code, out, err = run_process(
                common + ["--dump", "--", str(path)], directory, env=env, timeout=30, cancel=cancel
            )
            if code:
                raise RuntimeError(f"Could not extract links from {name}: {err.strip()}")
            for url in out.splitlines():
                parsed = urlsplit(url)
                if parsed.scheme not in {"http", "https", "file"}:
                    continue
                reason = None
                if parsed.username or parsed.password:
                    reason = "Links containing credentials are not requested"
                    url = "[URL with credentials omitted]"
                if parsed.scheme == "file":
                    target = Path(unquote(parsed.path)).resolve()
                    if "{" in unquote(parsed.path):
                        reason = "Dynamic MDX expression needs a rendered-site check"
                    elif parsed.netloc or not target.is_relative_to(root):
                        reason = "Link resolves outside the worktree"
                    elif not target.exists() and not target.suffix:
                        candidates = [target.with_suffix(ext) for ext in (".md", ".mdx", ".html")]
                        target = next((p for p in candidates if p.is_file()), target).resolve()
                        if not target.is_relative_to(root):
                            reason = "Link resolves outside the worktree"
                        elif not target.exists():
                            reason = "Site route needs a rendered-site check"
                    if reason is None:
                        url = urlunsplit(
                            ("file", "", target.as_uri()[7:], parsed.query, parsed.fragment)
                        )
                if reason:
                    skipped.append(
                        {"source": name, "url": url, "status": "unverified", "detail": reason}
                    )
                else:
                    sources.setdefault(url, []).append(name)
        if progress:
            progress(f"Checking {len(sources)} unique links in {len(documents)} changed documents")
        if sources:
            code, out, err = run_process(
                common
                + [
                    "--format",
                    "json",
                    "--include-fragments",
                    "--timeout",
                    "15",
                    "--max-retries",
                    "0",
                    "--max-concurrency",
                    "8",
                    "--",
                    "-",
                ],
                directory,
                stdin="\n".join(f"<{url}>" for url in sources),
                env=env,
                timeout=600,
                cancel=cancel,
                progress=(
                    lambda elapsed: progress(f"Link check still running ({elapsed:.0f}s elapsed)")
                )
                if progress
                else None,
            )
            try:
                raw = json.loads(out)
            except ValueError as error:
                raise RuntimeError(
                    f"Link checker did not return a report: {err.strip()}"
                ) from error
            if code not in {0, 2}:
                raise RuntimeError(f"Link checker failed ({code}): {err.strip()}")
        else:
            raw = {"total": 0, "successful": 0}
    issues = list(skipped)
    for group in ("error_map", "timeout_map", "excluded_map", "unknown_map", "unsupported_map"):
        for entries in raw.get(group, {}).values():
            for entry in entries:
                status = entry.get("status", {})
                http_code = status.get("code")
                url = entry.get("url", "")
                detail = status.get("text") or status.get("details") or str(status)
                # Access restrictions, rate limits, and network failures do not prove a dead link.
                broken = http_code in {404, 410} or (
                    url.startswith("file:") and group == "error_map"
                )
                # A PR can link to a new file on its target branch before merging.
                # Keep the live failure visible without claiming the PR's file is missing.
                github_path = re.fullmatch(
                    r"https://github\.com/([^/]+/[^/]+)/(?:blob|tree)/([^/]+)/(.+)", url
                )
                pr = source.get("pull_request") or {}
                if (
                    http_code == 404
                    and github_path
                    and source.get("head_ref", "").startswith(
                        f"https://github.com/{github_path[1]}/pull/"
                    )
                    and github_path[2] == pr.get("baseRefName")
                ):
                    target = (root / unquote(github_path[3])).resolve()
                    if target.is_relative_to(root) and target.exists():
                        broken = False
                        detail = f"{detail}; target exists in this PR and may resolve after merge"
                for name in sources.get(url, ["Unknown source"]):
                    issues.append(
                        {
                            "source": name,
                            "url": url,
                            "status": "broken" if broken else "unverified",
                            "detail": detail,
                        }
                    )
    counts = Counter(item["status"] for item in issues)
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "revision": source["head"],
        "documents": documents,
        "unique_links": len(sources),
        "successful": raw.get("successful", 0),
        "issues": issues,
        "summary": f"Checked {len(sources)} unique links in {len(documents)} changed documents: "
        f"{counts['broken']} broken references, {counts['unverified']} needing verification.",
        "limitations": "Source link check only. No docs build, rendered site, or JavaScript ran. "
        "Site routes and access/network failures need separate verification. "
        "Private addresses and links outside the worktree are excluded.",
    }
    save_json(
        Path(reports) / "documentation-links.json", {**result, "raw": raw, "sources": sources}
    )
    return result
