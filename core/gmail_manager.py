"""GmailManager — per-user Gmail OAuth2 access.

Stored per-user in /data/gmail_{user_id}.json
Scopes: gmail.modify (read + archive + labels) + gmail.send

OAuth flow:
1. User provides client_id + client_secret (from Google Cloud Console)
2. get_auth_url() generates the Google OAuth URL with state=user_id
3. User authorizes in browser → Google redirects to /api/gmail/callback?code=X&state=user_id
4. exchange_code() exchanges code for refresh_token + access_token
5. access token is auto-refreshed via _get_access_token()
"""

import base64
import json
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

_EMPTY = {"client_id": "", "client_secret": "", "refresh_token": "", "access_token": "", "connected": False, "email": ""}


class GmailManager:
    def __init__(self, data_dir: str = "/data"):
        self._data_dir = Path(data_dir)

    def _path(self, user_id: str) -> Path:
        return self._data_dir / f"gmail_{user_id}.json"

    def _load(self, user_id: str) -> dict:
        p = self._path(user_id)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception as e:
                logger.error(f"Failed to load Gmail config for {user_id}: {e}")
        import copy
        return copy.deepcopy(_EMPTY)

    def _save(self, user_id: str, data: dict):
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._path(user_id).write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to save Gmail config for {user_id}: {e}")

    def get_status(self, user_id: str) -> dict:
        d = self._load(user_id)
        return {
            "connected": d.get("connected", False),
            "client_id": d.get("client_id", ""),
            "has_secret": bool(d.get("client_secret")),
            "email": d.get("email", ""),
        }

    def update_credentials(self, user_id: str, client_id: str, client_secret: str):
        d = self._load(user_id)
        if client_id:
            d["client_id"] = client_id
        if client_secret and "••••" not in client_secret:
            d["client_secret"] = client_secret
        # Reset connection when credentials change
        d["connected"] = False
        d["refresh_token"] = ""
        d["access_token"] = ""
        d["email"] = ""
        self._save(user_id, d)

    def get_auth_url(self, user_id: str, redirect_uri: str) -> str:
        d = self._load(user_id)
        if not d.get("client_id"):
            raise ValueError("No client_id configured")
        params = {
            "client_id": d["client_id"],
            "redirect_uri": redirect_uri,
            "scope": GMAIL_SCOPE,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",   # force refresh_token
            "state": user_id,
        }
        return AUTH_URI + "?" + urlencode(params)

    async def exchange_code(self, user_id: str, code: str, redirect_uri: str) -> bool:
        d = self._load(user_id)
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
                logger.error(f"Gmail token exchange failed: {resp.text}")
                return False
            tokens = resp.json()
            d["refresh_token"] = tokens.get("refresh_token", "")
            d["access_token"] = tokens.get("access_token", "")
            d["connected"] = bool(d["refresh_token"])

            # Fetch user email address
            if d.get("access_token"):
                try:
                    async with httpx.AsyncClient(timeout=10) as c:
                        r = await c.get(f"{GMAIL_API}/profile",
                                        headers={"Authorization": f"Bearer {d['access_token']}"})
                        if r.status_code == 200:
                            d["email"] = r.json().get("emailAddress", "")
                except Exception:
                    pass
            self._save(user_id, d)
            return d["connected"]
        except Exception as e:
            logger.error(f"Gmail exchange_code error: {e}")
            return False

    async def _get_access_token(self, user_id: str) -> str:
        d = self._load(user_id)
        if not d.get("refresh_token"):
            raise ValueError("Gmail not connected")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(TOKEN_URI, data={
                    "refresh_token": d["refresh_token"],
                    "client_id": d["client_id"],
                    "client_secret": d["client_secret"],
                    "grant_type": "refresh_token",
                })
            resp.raise_for_status()
            token = resp.json()["access_token"]
            d["access_token"] = token
            self._save(user_id, d)
            return token
        except Exception as e:
            logger.error(f"Gmail token refresh error: {e}")
            raise

    async def list_messages(self, user_id: str, query: str = "in:inbox is:unread", max_results: int = 10) -> list:
        token = await self._get_access_token(user_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{GMAIL_API}/messages",
                                    headers={"Authorization": f"Bearer {token}"},
                                    params={"q": query, "maxResults": max_results})
        resp.raise_for_status()
        return resp.json().get("messages", [])

    async def get_message(self, user_id: str, msg_id: str) -> dict:
        token = await self._get_access_token(user_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{GMAIL_API}/messages/{msg_id}",
                                    headers={"Authorization": f"Bearer {token}"},
                                    params={"format": "full"})
        resp.raise_for_status()
        return resp.json()

    async def get_message_summary(self, user_id: str, msg_id: str) -> dict:
        """Return a simplified message dict (headers + snippet)."""
        raw = await self.get_message(user_id, msg_id)
        headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
        return {
            "id": raw["id"],
            "from": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": raw.get("snippet", ""),
            "labels": raw.get("labelIds", []),
        }

    async def archive_message(self, user_id: str, msg_id: str):
        token = await self._get_access_token(user_id)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{GMAIL_API}/messages/{msg_id}/modify",
                                     headers={"Authorization": f"Bearer {token}"},
                                     json={"removeLabelIds": ["INBOX"]})
        resp.raise_for_status()

    async def send_message(self, user_id: str, to: str, subject: str, body: str,
                           html: bool = False, attachments: list[dict] | None = None) -> dict:
        """
        Send an email, optionally with attachments.
        attachments: list of {"filename": "report.pdf", "path": "/data/report.pdf"}
                     or {"filename": "data.csv", "content": "<base64>", "mime_type": "text/csv"}
        """
        import mimetypes
        from email.mime.base import MIMEBase
        from email import encoders

        token = await self._get_access_token(user_id)

        if attachments:
            msg = MIMEMultipart("mixed")
            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(body, "html" if html else "plain"))
            msg.attach(alt)
            for att in attachments:
                filename = att.get("filename", "attachment")
                if "path" in att:
                    path = att["path"]
                    mime_type, _ = mimetypes.guess_type(path)
                    main_type, sub_type = (mime_type or "application/octet-stream").split("/", 1)
                    with open(path, "rb") as f:
                        data = f.read()
                elif "content" in att:
                    data = base64.b64decode(att["content"])
                    mime_type = att.get("mime_type", "application/octet-stream")
                    main_type, sub_type = mime_type.split("/", 1)
                else:
                    continue
                part = MIMEBase(main_type, sub_type)
                part.set_payload(data)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=filename)
                msg.attach(part)
        elif html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "html"))
        else:
            msg = MIMEText(body, "plain")

        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{GMAIL_API}/messages/send",
                                     headers={"Authorization": f"Bearer {token}"},
                                     json={"raw": raw})
        resp.raise_for_status()
        return resp.json()

    async def create_draft(self, user_id: str, to: str, subject: str, body: str,
                           html: bool = False) -> dict:
        """Create a Gmail draft (not sent)."""
        token = await self._get_access_token(user_id)
        if html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "html"))
        else:
            msg = MIMEText(body, "plain")
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{GMAIL_API}/drafts",
                                     headers={"Authorization": f"Bearer {token}"},
                                     json={"message": {"raw": raw}})
        resp.raise_for_status()
        return resp.json()

    def disconnect(self, user_id: str):
        d = self._load(user_id)
        d["refresh_token"] = ""
        d["access_token"] = ""
        d["connected"] = False
        d["email"] = ""
        self._save(user_id, d)

    def is_connected(self, user_id: str) -> bool:
        return bool(self._load(user_id).get("refresh_token"))
