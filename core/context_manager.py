"""ContextManager — per-user contexts (global text + project shortcuts).

Each user gets their own file: /data/contexts_{user_id}.json
Agents and Skills are shared globally; contexts are private per user.
"""

import json
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class ContextManager:
    def __init__(self, data_dir: str = "/data"):
        self._data_dir = Path(data_dir)
        self._cache: dict[str, dict] = {}  # user_id -> {global, projects}

    # ── Internal helpers ─────────────────────────────────────────

    def _path(self, user_id: str) -> Path:
        return self._data_dir / f"contexts_{user_id}.json"

    def _load(self, user_id: str) -> dict:
        if user_id in self._cache:
            return self._cache[user_id]
        path = self._path(user_id)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._cache[user_id] = data
                return data
            except Exception as e:
                logger.error(f"Failed to load contexts for {user_id}: {e}")
        data: dict = {"global": "", "projects": []}
        self._cache[user_id] = data
        return data

    def _save(self, user_id: str):
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._path(user_id).write_text(
                json.dumps(self._cache[user_id], indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.error(f"Failed to save contexts for {user_id}: {e}")

    # ── Public API ───────────────────────────────────────────────

    def get_all(self, user_id: str) -> dict:
        return dict(self._load(user_id))

    def get_global(self, user_id: str) -> str:
        return self._load(user_id).get("global", "")

    def set_global(self, user_id: str, content: str):
        data = self._load(user_id)
        data["global"] = content
        self._save(user_id)

    def list_projects(self, user_id: str) -> list[dict]:
        return list(self._load(user_id).get("projects", []))

    def get_project(self, user_id: str, shortcut_or_id: str) -> dict | None:
        return next(
            (
                p for p in self._load(user_id).get("projects", [])
                if p.get("shortcut") == shortcut_or_id or p.get("id") == shortcut_or_id
            ),
            None,
        )

    def create_project(self, user_id: str, data: dict) -> dict:
        project = {
            "id": data.get("id") or data.get("shortcut") or str(uuid.uuid4())[:8],
            "name": data.get("name", ""),
            "shortcut": data.get("shortcut", ""),
            "content": data.get("content", ""),
        }
        ctx = self._load(user_id)
        if "projects" not in ctx:
            ctx["projects"] = []
        ctx["projects"].append(project)
        self._save(user_id)
        return project

    def update_project(self, user_id: str, project_id: str, data: dict) -> dict:
        ctx = self._load(user_id)
        projects = ctx.get("projects", [])
        for i, p in enumerate(projects):
            if p["id"] == project_id:
                projects[i] = {**p, **data, "id": project_id}
                ctx["projects"] = projects
                self._save(user_id)
                return projects[i]
        raise ValueError(f"Project not found: {project_id}")

    def delete_project(self, user_id: str, project_id: str):
        ctx = self._load(user_id)
        before = len(ctx.get("projects", []))
        ctx["projects"] = [p for p in ctx.get("projects", []) if p["id"] != project_id]
        if len(ctx["projects"]) == before:
            raise ValueError(f"Project not found: {project_id}")
        self._save(user_id)

    def build_context_prompt(self, user_id: str, active_shortcuts: list[str] | None = None) -> str:
        """Build system context from global + requested (or all) project contexts for a user."""
        parts = []
        global_ctx = self.get_global(user_id)
        if global_ctx:
            parts.append(f"## User Context\n{global_ctx}")

        projects = self.list_projects(user_id)
        if active_shortcuts:
            for shortcut in active_shortcuts:
                proj = self.get_project(user_id, shortcut)
                if proj and proj.get("content"):
                    parts.append(f"## Project: {proj['name']}\n{proj['content']}")
        else:
            for proj in projects:
                if proj.get("content"):
                    parts.append(f"## Project: {proj['name']}\n{proj['content']}")

        return "\n\n".join(parts)
