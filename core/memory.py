"""
Persistent conversation memory — multi-session per user, stored as JSON.

New format:
{
  "user_id": {
    "active_session": "sess_abc123",
    "sessions": [
      {
        "id": "sess_abc123",
        "title": "First 50 chars of first user message…",
        "created_at": "2026-02-23T10:00:00",
        "messages": [{"role": "user", "content": "..."}]
      }
    ]
  }
}

Migration: old format (user_id → list) is auto-converted on first load.
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path


class Memory:
    def __init__(self, path: str = "./data/memory.json", max_messages: int = 50):
        self.path = Path(path)
        self.max_messages = max_messages
        self._lock = asyncio.Lock()
        self._data: dict = {}
        self._load()

    # ── Internal helpers ────────────────────────────────────────

    def _load(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                with open(self.path) as f:
                    raw = json.load(f)
                # Migration: convert old format (user_id → list) to new format
                for user_id, val in list(raw.items()):
                    if isinstance(val, list):
                        raw[user_id] = self._migrate_user(val)
                self._data = raw
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _migrate_user(self, messages: list) -> dict:
        """Convert old flat list format to new session-based format."""
        sess_id = self._new_id()
        return {
            "active_session": sess_id,
            "sessions": [{
                "id": sess_id,
                "title": self._gen_title(messages),
                "created_at": datetime.now().isoformat(),
                "messages": messages,
            }],
        }

    @staticmethod
    def _new_id() -> str:
        return "sess_" + uuid.uuid4().hex[:8]

    @staticmethod
    def _gen_title(messages: list) -> str:
        """Generate title from first user message (max 50 chars)."""
        for m in messages:
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    text = content.strip().replace("\n", " ")
                    return text[:50] + ("…" if len(text) > 50 else "")
        return "New conversation"

    def _ensure_user(self, user_id: str):
        """Ensure user has the new sessions structure."""
        val = self._data.get(user_id)
        if val is None:
            sess_id = self._new_id()
            self._data[user_id] = {
                "active_session": sess_id,
                "sessions": [{
                    "id": sess_id,
                    "title": "New conversation",
                    "created_at": datetime.now().isoformat(),
                    "messages": [],
                }],
            }
        elif isinstance(val, list):
            self._data[user_id] = self._migrate_user(val)

    def _get_active_session(self, user_id: str) -> dict | None:
        """Return the active session dict (or last one as fallback)."""
        self._ensure_user(user_id)
        ud = self._data[user_id]
        active_id = ud.get("active_session")
        sessions = ud.get("sessions", [])
        for sess in sessions:
            if sess["id"] == active_id:
                return sess
        # Fallback: last session
        if sessions:
            ud["active_session"] = sessions[-1]["id"]
            return sessions[-1]
        return None

    async def _save(self):
        async with self._lock:
            with open(self.path, "w") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ── Public read API ─────────────────────────────────────────

    def get_history(self, user_id: str) -> list[dict]:
        """Return messages of the active session."""
        sess = self._get_active_session(user_id)
        return list(sess["messages"]) if sess else []

    def get_active_session_id(self, user_id: str) -> str:
        self._ensure_user(user_id)
        return self._data[user_id].get("active_session", "")

    def list_users(self) -> list[str]:
        return list(self._data.keys())

    # ── Public write API ─────────────────────────────────────────

    async def add_message(self, user_id: str, role: str, content: str):
        self._ensure_user(user_id)
        sess = self._get_active_session(user_id)
        if sess is None:
            return
        sess["messages"].append({"role": role, "content": content})

        # Auto-title from first user message
        if sess.get("title") in ("New conversation", "") and role == "user":
            sess["title"] = self._gen_title(sess["messages"])

        # Trim to max
        if len(sess["messages"]) > self.max_messages:
            sess["messages"] = sess["messages"][-self.max_messages:]

        await self._save()

    async def clear(self, user_id: str):
        """Clear messages in the active session (keep session, reset title)."""
        sess = self._get_active_session(user_id)
        if sess:
            sess["messages"] = []
            sess["title"] = "New conversation"
        await self._save()

    # ── Session management ───────────────────────────────────────

    async def create_session(self, user_id: str) -> str:
        """Create a new empty session, set it as active. Returns session_id."""
        self._ensure_user(user_id)
        sess_id = self._new_id()
        self._data[user_id]["sessions"].append({
            "id": sess_id,
            "title": "New conversation",
            "created_at": datetime.now().isoformat(),
            "messages": [],
        })
        self._data[user_id]["active_session"] = sess_id
        await self._save()
        return sess_id

    def list_sessions(self, user_id: str) -> list[dict]:
        """List sessions, newest first, with preview text."""
        self._ensure_user(user_id)
        result = []
        for sess in reversed(self._data[user_id].get("sessions", [])):
            preview = ""
            for m in reversed(sess.get("messages", [])):
                if m.get("role") == "assistant":
                    c = m.get("content", "")
                    if isinstance(c, str):
                        preview = c[:60].replace("\n", " ")
                    break
            result.append({
                "id": sess["id"],
                "title": sess.get("title", "New conversation"),
                "created_at": sess.get("created_at", ""),
                "preview": preview,
                "message_count": len(sess.get("messages", [])),
            })
        return result

    async def switch_session(self, user_id: str, session_id: str) -> bool:
        """Set session_id as active. Returns False if not found."""
        self._ensure_user(user_id)
        for sess in self._data[user_id].get("sessions", []):
            if sess["id"] == session_id:
                self._data[user_id]["active_session"] = session_id
                await self._save()
                return True
        return False

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        """Delete a session. If it was active, switch to the last remaining one."""
        self._ensure_user(user_id)
        sessions = self._data[user_id].get("sessions", [])
        for i, sess in enumerate(sessions):
            if sess["id"] == session_id:
                sessions.pop(i)
                # If we deleted the active session, pick a replacement
                if self._data[user_id].get("active_session") == session_id:
                    if sessions:
                        self._data[user_id]["active_session"] = sessions[-1]["id"]
                    else:
                        # No sessions left — create a fresh one
                        new_id = self._new_id()
                        sessions.append({
                            "id": new_id,
                            "title": "New conversation",
                            "created_at": datetime.now().isoformat(),
                            "messages": [],
                        })
                        self._data[user_id]["active_session"] = new_id
                await self._save()
                return True
        return False
