"""GoogleTasksManager — OAuth2 for Google Tasks API.

Same OAuth client as Gmail (Web Application type).
Redirect URI: https://app.opennoclaw.com/api/google-tasks/callback

After successful auth, token is saved to:
  - /data/google-tasks-token.json  (openNoClaw)
  - Gulliver container via docker exec (so courses sync keeps working)

Storage format matches google-auth-oauthlib (google-tasks-token.json):
{
  "token": "...",
  "refresh_token": "...",
  "token_uri": "https://oauth2.googleapis.com/token",
  "client_id": "...",
  "client_secret": "...",
  "scopes": ["https://www.googleapis.com/auth/tasks"]
}
"""

import json
import logging
from pathlib import Path
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# All Google scopes needed across openNoClaw skills
GOOGLE_SCOPES = " ".join([
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
])
# Keep alias for backward compat
TASKS_SCOPE = GOOGLE_SCOPES

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"

_EMPTY = {
    "client_id": "", "client_secret": "",
    "refresh_token": "", "token": "",
    "connected": False,
}


class GoogleTasksManager:
    def __init__(self, data_dir: str = "/data"):
        self._data_dir = Path(data_dir)

    def _path(self) -> Path:
        return self._data_dir / "google-tasks-token.json"

    def _load(self) -> dict:
        p = self._path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
        import copy
        return copy.deepcopy(_EMPTY)

    def _save(self, data: dict):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._path().write_text(json.dumps(data, indent=2))

    def get_status(self) -> dict:
        d = self._load()
        return {
            "connected": bool(d.get("refresh_token")),
            "client_id": d.get("client_id", ""),
            "has_secret": bool(d.get("client_secret")),
        }

    def update_credentials(self, client_id: str, client_secret: str):
        d = self._load()
        if client_id:
            d["client_id"] = client_id
        if client_secret and "••••" not in client_secret:
            d["client_secret"] = client_secret
        d["refresh_token"] = ""
        d["token"] = ""
        d["connected"] = False
        self._save(d)

    def get_auth_url(self, redirect_uri: str) -> str:
        d = self._load()
        if not d.get("client_id"):
            raise ValueError("No client_id configured")
        params = {
            "client_id": d["client_id"],
            "redirect_uri": redirect_uri,
            "scope": TASKS_SCOPE,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "state": "google-tasks",
        }
        return AUTH_URI + "?" + urlencode(params)

    async def exchange_code(self, code: str, redirect_uri: str) -> bool:
        d = self._load()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(TOKEN_URI, data={
                    "code": code,
                    "client_id": d["client_id"],
                    "client_secret": d["client_secret"],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                })
            if resp.status_code != 200:
                logger.error(f"Google Tasks token exchange failed: {resp.text}")
                return False
            tokens = resp.json()
            d["refresh_token"] = tokens.get("refresh_token", "")
            d["token"] = tokens.get("access_token", "")
            d["token_uri"] = TOKEN_URI
            d["scopes"] = GOOGLE_SCOPES.split()
            d["connected"] = bool(d["refresh_token"])
            self._save(d)

            return d["connected"]
        except Exception as e:
            logger.error(f"Google Tasks exchange_code error: {e}")
            return False

    def is_connected(self) -> bool:
        return bool(self._load().get("refresh_token"))

    def disconnect(self):
        d = self._load()
        d["refresh_token"] = ""
        d["token"] = ""
        d["connected"] = False
        self._save(d)
