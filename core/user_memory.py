"""UserMemory — per-user persistent memory across conversations.

Stored as /data/memory_{user_id}.md — one fact per line.
Injected into every conversation's system prompt.
"""

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class UserMemory:
    def __init__(self, data_dir: str = "/data"):
        self._data_dir = Path(data_dir)

    def _path(self, user_id: str) -> Path:
        return self._data_dir / f"memory_{user_id}.md"

    def get(self, user_id: str) -> str:
        p = self._path(user_id)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        return ""

    def add_fact(self, user_id: str, fact: str) -> str:
        """Append a fact. Returns the full updated memory."""
        current = self.get(user_id)
        date = datetime.now().strftime("%Y-%m-%d")
        entry = f"- {fact}  *(mémorisé le {date})*"
        new_content = (current + "\n" + entry) if current else entry
        self._save(user_id, new_content)
        return new_content

    def forget(self, user_id: str, keyword: str) -> tuple[int, str]:
        """Remove lines containing keyword (case-insensitive).
        Returns (count_removed, new_content)."""
        current = self.get(user_id)
        if not current:
            return 0, ""
        lines = current.split("\n")
        kw = keyword.lower()
        kept = [l for l in lines if kw not in l.lower()]
        removed = len(lines) - len(kept)
        new_content = "\n".join(l for l in kept if l.strip()).strip()
        self._save(user_id, new_content)
        return removed, new_content

    def update(self, user_id: str, content: str):
        """Replace the full memory content."""
        self._save(user_id, content.strip())

    def _save(self, user_id: str, content: str):
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._path(user_id).write_text(content, encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save memory for {user_id}: {e}")
