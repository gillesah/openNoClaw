"""ConnexionManager — per-user connexions: Telegram, Email, GitHub, Linear, MCPs.

Each user stores: /data/connexions_{user_id}.json
Structure:
{
  "telegram": {"enabled": bool, "bot_token": str, "chat_id": str},
  "email": {"enabled": bool, "smtp_host": str, "smtp_port": int,
            "smtp_user": str, "smtp_password": str,
            "from_name": str, "from_email": str},
  "github": {"enabled": bool, "token": str, "repo_owner": str, "repo_name": str},
  "linear": {"enabled": bool, "api_key": str},
  "mcps": [{"id": str, "name": str, "type": "sse"|"stdio",
            "url": str, "command": str, "headers": dict, "env": dict, "enabled": bool}]
}
"""

import json
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

EMPTY = {"telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
         "email": {"enabled": False, "smtp_host": "smtp.gmail.com", "smtp_port": 587,
                   "smtp_user": "", "smtp_password": "", "from_name": "", "from_email": "",
                   "notify_email": ""},
         "github": {"enabled": False, "token": "", "repo_owner": "", "repo_name": ""},
         "linear": {"enabled": False, "api_key": ""},
         "social": {
             "reddit":  {"enabled": False, "username": ""},
             "bluesky": {"enabled": False, "handle": "", "app_password": ""},
             "twitter": {"enabled": False, "consumer_key": "", "consumer_secret": "", "bearer_token": "",
                         "access_token": "", "access_token_secret": ""},
         },
         "leclerc": {"enabled": False, "email": "", "password": ""},
         "notion": {"enabled": False, "api_key": "", "database_id": ""},
         "mcps": []}

MCP_CATALOG = [
    {"id": "filesystem", "name": "Filesystem", "type": "stdio",
     "command": "npx -y @modelcontextprotocol/server-filesystem /data",
     "description": "Read/write local files", "icon": "📁"},
    {"id": "github", "name": "GitHub", "type": "stdio",
     "command": "npx -y @modelcontextprotocol/server-github",
     "description": "GitHub issues, PRs, repos", "icon": "🐙",
     "env_required": ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    {"id": "postgres", "name": "PostgreSQL", "type": "stdio",
     "command": "npx -y @modelcontextprotocol/server-postgres",
     "description": "Query PostgreSQL databases", "icon": "🐘",
     "env_required": ["POSTGRES_URL"]},
    {"id": "slack", "name": "Slack", "type": "stdio",
     "command": "npx -y @modelcontextprotocol/server-slack",
     "description": "Read Slack messages and channels", "icon": "💬",
     "env_required": ["SLACK_BOT_TOKEN"]},
    {"id": "brave-search", "name": "Brave Search", "type": "stdio",
     "command": "npx -y @modelcontextprotocol/server-brave-search",
     "description": "Web search via Brave API", "icon": "🔍",
     "env_required": ["BRAVE_API_KEY"]},
    {"id": "custom-sse", "name": "Custom SSE server", "type": "sse",
     "url": "", "headers": {},
     "description": "Any SSE-based MCP server (FluenzR, etc.)", "icon": "🔌"},
]


class ConnexionManager:
    def __init__(self, data_dir: str = "/data"):
        self._data_dir = Path(data_dir)
        self._cache: dict[str, dict] = {}

    def _path(self, user_id: str) -> Path:
        return self._data_dir / f"connexions_{user_id}.json"

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
                logger.error(f"Failed to load connexions for {user_id}: {e}")
        import copy
        data = copy.deepcopy(EMPTY)
        self._cache[user_id] = data
        return data

    def _save(self, user_id: str):
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._path(user_id).write_text(
                json.dumps(self._cache[user_id], indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.error(f"Failed to save connexions for {user_id}: {e}")

    def get_all(self, user_id: str) -> dict:
        import copy
        # Merge with EMPTY to ensure all keys are present even in old files
        safe = copy.deepcopy(EMPTY)
        loaded = self._load(user_id)
        for k, v in loaded.items():
            if isinstance(v, dict) and isinstance(safe.get(k), dict):
                safe[k] = {**safe[k], **v}
            else:
                safe[k] = v
        safe = json.loads(json.dumps(safe))  # deep copy for masking
        if safe.get("email", {}).get("smtp_password"):
            safe["email"]["smtp_password"] = "••••••••"
        if safe.get("telegram", {}).get("bot_token"):
            t = safe["telegram"]["bot_token"]
            safe["telegram"]["bot_token"] = t[:8] + "••••" if len(t) > 8 else "••••"
        if safe.get("github", {}).get("token"):
            t = safe["github"]["token"]
            safe["github"]["token"] = t[:8] + "••••" if len(t) > 8 else "••••"
        if safe.get("linear", {}).get("api_key"):
            safe["linear"]["api_key"] = "••••••••"
        if safe.get("social", {}).get("reddit", {}).get("password"):
            safe["social"]["reddit"]["password"] = "••••••••"
        for key in ("consumer_secret", "bearer_token", "access_token", "access_token_secret"):
            if safe.get("social", {}).get("twitter", {}).get(key):
                safe["social"]["twitter"][key] = "••••••••"
        if safe.get("social", {}).get("bluesky", {}).get("app_password"):
            safe["social"]["bluesky"]["app_password"] = "••••••••"
        if safe.get("leclerc", {}).get("password"):
            safe["leclerc"]["password"] = "••••••••"
        if safe.get("notion", {}).get("api_key"):
            safe["notion"]["api_key"] = "••••••••"
        for mcp in safe.get("mcps", []):
            for k in list(mcp.get("headers", {}).keys()):
                mcp["headers"][k] = "••••"
        return safe

    def get_telegram(self, user_id: str) -> dict:
        return dict(self._load(user_id).get("telegram", EMPTY["telegram"]))

    def update_telegram(self, user_id: str, data: dict):
        ctx = self._load(user_id)
        # Don't overwrite with masked value
        current = ctx.get("telegram", {})
        new_token = data.get("bot_token", "")
        if "••••" in new_token:
            data["bot_token"] = current.get("bot_token", "")
        ctx["telegram"] = {**EMPTY["telegram"], **current, **data}
        self._save(user_id)

    def get_email(self, user_id: str) -> dict:
        return dict(self._load(user_id).get("email", EMPTY["email"]))

    def update_email(self, user_id: str, data: dict):
        ctx = self._load(user_id)
        current = ctx.get("email", {})
        new_pw = data.get("smtp_password", "")
        if "••••" in new_pw:
            data["smtp_password"] = current.get("smtp_password", "")
        ctx["email"] = {**EMPTY["email"], **current, **data}
        self._save(user_id)

    def get_github(self, user_id: str) -> dict:
        return dict(self._load(user_id).get("github", EMPTY["github"]))

    def update_github(self, user_id: str, data: dict):
        ctx = self._load(user_id)
        current = ctx.get("github", {})
        new_token = data.get("token", "")
        if "••••" in new_token:
            data["token"] = current.get("token", "")
        ctx["github"] = {**EMPTY["github"], **current, **data}
        self._save(user_id)

    def get_linear(self, user_id: str) -> dict:
        return dict(self._load(user_id).get("linear", EMPTY["linear"]))

    def update_linear(self, user_id: str, data: dict):
        ctx = self._load(user_id)
        current = ctx.get("linear", {})
        new_key = data.get("api_key", "")
        if "••••" in new_key:
            data["api_key"] = current.get("api_key", "")
        ctx["linear"] = {**EMPTY["linear"], **current, **data}
        self._save(user_id)

    def get_mcps(self, user_id: str) -> list[dict]:
        return list(self._load(user_id).get("mcps", []))

    def add_mcp(self, user_id: str, data: dict) -> dict:
        ctx = self._load(user_id)
        mcp = {
            "id": data.get("id") or str(uuid.uuid4())[:8],
            "name": data.get("name", "Custom MCP"),
            "type": data.get("type", "sse"),
            "url": data.get("url", ""),
            "command": data.get("command", ""),
            "headers": data.get("headers", {}),
            "env": data.get("env", {}),
            "enabled": data.get("enabled", True),
        }
        if "mcps" not in ctx:
            ctx["mcps"] = []
        ctx["mcps"].append(mcp)
        self._save(user_id)
        return mcp

    def update_mcp(self, user_id: str, mcp_id: str, data: dict) -> dict:
        ctx = self._load(user_id)
        for i, m in enumerate(ctx.get("mcps", [])):
            if m["id"] == mcp_id:
                ctx["mcps"][i] = {**m, **data, "id": mcp_id}
                self._save(user_id)
                return ctx["mcps"][i]
        raise ValueError(f"MCP not found: {mcp_id}")

    def delete_mcp(self, user_id: str, mcp_id: str):
        ctx = self._load(user_id)
        before = len(ctx.get("mcps", []))
        ctx["mcps"] = [m for m in ctx.get("mcps", []) if m["id"] != mcp_id]
        if len(ctx["mcps"]) == before:
            raise ValueError(f"MCP not found: {mcp_id}")
        self._save(user_id)

    def get_social(self, user_id: str) -> dict:
        return dict(self._load(user_id).get("social", EMPTY["social"]))

    def update_social(self, user_id: str, data: dict):
        ctx = self._load(user_id)
        current = ctx.get("social", {})
        # Preserve masked passwords
        for net, key in [("twitter", "consumer_secret"), ("twitter", "bearer_token"), ("twitter", "access_token"), ("twitter", "access_token_secret"), ("bluesky", "app_password")]:
            new_val = data.get(net, {}).get(key, "")
            if "••••" in new_val:
                data.setdefault(net, {})[key] = current.get(net, {}).get(key, "")
        import copy
        base = copy.deepcopy(EMPTY["social"])
        for net in base:
            base[net].update(current.get(net, {}))
            base[net].update(data.get(net, {}))
        ctx["social"] = base
        self._save(user_id)

    def get_notion(self, user_id: str) -> dict:
        return dict(self._load(user_id).get("notion", EMPTY["notion"]))

    def update_notion(self, user_id: str, data: dict):
        ctx = self._load(user_id)
        current = ctx.get("notion", {})
        if "••••" in data.get("api_key", ""):
            data["api_key"] = current.get("api_key", "")
        ctx["notion"] = {**EMPTY["notion"], **current, **data}
        self._save(user_id)

    def get_leclerc(self, user_id: str) -> dict:
        return dict(self._load(user_id).get("leclerc", EMPTY["leclerc"]))

    def update_leclerc(self, user_id: str, data: dict):
        ctx = self._load(user_id)
        current = ctx.get("leclerc", {})
        if "••••" in data.get("password", ""):
            data["password"] = current.get("password", "")
        ctx["leclerc"] = {**EMPTY["leclerc"], **current, **data}
        self._save(user_id)

    def get_enabled_mcps(self, user_id: str) -> list[dict]:
        """Return raw (unmasked) enabled MCPs for CLI use."""
        return [m for m in self._load(user_id).get("mcps", []) if m.get("enabled")]

    @staticmethod
    def get_catalog() -> list[dict]:
        return MCP_CATALOG
