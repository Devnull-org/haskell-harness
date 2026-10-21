"""The local project list; removing an entry leaves source and saved work intact."""

import sqlite3
from contextlib import closing
from pathlib import Path

from .project_context import state_directory
from .project_settings import project_identity
from .workspace import git


def validate_repository(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("Enter the path to a Git checkout")
    repository = Path(value).expanduser().resolve()
    if not repository.is_dir():
        raise ValueError(f"Checkout does not exist: {repository}")
    if Path(git(repository, "rev-parse", "--show-toplevel")).resolve() != repository:
        raise ValueError("Project path must be the root of a Git checkout")
    return repository


class ProjectRegistry:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "projects.sqlite"
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS projects "
                "(id TEXT PRIMARY KEY, repository TEXT UNIQUE NOT NULL)"
            )

    def list(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            rows = db.execute("SELECT id, repository FROM projects ORDER BY repository").fetchall()
        return [
            {"id": key, "name": Path(path).name or path, "repository": path} for key, path in rows
        ]

    def get(self, project_id):
        for project in self.list():
            if project["id"] == project_id:
                return project
        raise ValueError(
            "This project is no longer registered. Open Projects to add or choose a checkout."
        )

    def add(self, value):
        repository = validate_repository(value)
        state_directory(repository, self.root)
        project = project_identity(repository)
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO projects VALUES (?, ?)",
                (project["id"], project["repository"]),
            )
        return project

    def remove(self, project_id):
        self.get(project_id)
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
