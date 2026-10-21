"""Keep large review prompts compact while retaining every patch for inspection."""

import json
from pathlib import Path

from .workspace import digest, git, save_json

INLINE_DIFF_BYTES = 120_000
INLINE_FILE_BYTES = 24_000
INLINE_INDEX_BYTES = 48_000


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


def _write(path, content):
    if path.is_symlink():
        raise ValueError("Review input files must not be symbolic links")
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.is_symlink():
        raise ValueError("Review input files must not be symbolic links")
    temporary.write_text(content + "\n")
    temporary.replace(path)


def review_diff(workspace, source, reports):
    base, head = source["merge_base"], source["head"]
    arguments = [
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--find-renames",
        base,
        head,
    ]
    full = git(workspace.path, *arguments)
    if _json_size(full) <= INLINE_DIFF_BYTES:
        return {"diff": full}

    root = Path(reports) / "review-input"
    packet = root / digest([base, head])[:16]
    for directory in (root, packet):
        if directory.is_symlink():
            raise ValueError("Review input directories must not be symbolic links")
        directory.mkdir(parents=True, exist_ok=True)
    _write(packet / "full.diff", full)
    names = git(
        workspace.path,
        "diff",
        "--name-status",
        "--find-renames",
        "-z",
        base,
        head,
    ).split("\0")
    entries, previews = [], []
    remaining = INLINE_DIFF_BYTES - 1000
    position = 0
    while position < len(names) and names[position]:
        status = names[position]
        count = 2 if status[0] in {"R", "C"} else 1
        paths = names[position + 1 : position + 1 + count]
        position += count + 1
        # Literal pathspecs handle names containing brackets, stars, or Git pathspec magic.
        patch = git(workspace.path, "--literal-pathspecs", *arguments, "--", *paths)
        path = packet / f"{len(entries) + 1:04d}.diff"
        _write(path, patch)
        size = _json_size(patch)
        inline = size <= min(INLINE_FILE_BYTES, remaining)
        if inline:
            previews.append(patch)
            remaining -= size + 4
        entries.append(
            {
                "status": status,
                "paths": paths,
                "patch": str(path),
                "patch_bytes": len(patch.encode()),
                "inline": inline,
            }
        )
    index_path = packet / "index.json"
    if index_path.is_symlink() or index_path.with_suffix(".json.tmp").is_symlink():
        raise ValueError("Review input files must not be symbolic links")
    save_json(index_path, {"base_revision": base, "head_revision": head, "files": entries})
    inline_entries, remaining = [], INLINE_INDEX_BYTES
    for entry in entries:
        size = _json_size(entry) + 2
        if size > remaining:
            break
        inline_entries.append(entry)
        remaining -= size
    return {
        "diff": "PARTIAL INLINE DIFF. Large or additional patches are in review_changes. "
        "Read the complete index and relevant patch files before concluding the review.\n\n"
        + "\n\n".join(previews),
        "review_changes": {
            "base_revision": base,
            "head_revision": head,
            "full_diff": str(packet / "full.diff"),
            "index": str(index_path),
            "file_count": len(entries),
            "files": inline_entries,
            "index_complete": len(inline_entries) == len(entries),
            "inline_diff_complete": False,
        },
    }
