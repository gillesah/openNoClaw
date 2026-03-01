"""
Manages the claude auth login flow.

How it works:
  1. Spawn `claude auth login` with a PTY so Ink renders the full OAuth URL.
  2. Capture the MANUAL auth URL from PTY output.
  3. Detect the local HTTP callback port that claude opened.
  4. Construct the AUTOMATIC URL by replacing redirect_uri with localhost:PORT.
  5. Show the user the AUTOMATIC URL.
     After OAuth, their browser redirects to http://localhost:PORT/callback?code=X&state=Y
     which fails (Docker container). They copy the full URL from the address bar.
  6. We extract code and state from the pasted URL, relay GET to localhost:PORT/callback.
     The local server does the token exchange with redirect_uri=localhost:PORT — matching
     the automatic URL the user visited. Token exchange succeeds, credentials saved.
"""

import asyncio
import logging
import os
import pty
import re
import fcntl
import select
import socket
import termios
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

URL_START_RE = re.compile(r"https://claude\.ai/oauth/authorize")
MANUAL_REDIRECT = "https://platform.claude.com/oauth/code/callback"


class ClaudeAuthManager:
    def __init__(self):
        self._proc = None
        self._master_fd = None
        self._auth_url: str | None = None       # manual URL (from PTY)
        self._auto_url: str | None = None       # automatic URL (localhost redirect)
        self._callback_port: int | None = None
        self._state: str | None = None
        self._ports_before: set[int] = set()

    def is_authenticated(self) -> bool:
        creds = Path.home() / ".claude" / ".credentials.json"
        return creds.exists() and creds.stat().st_size > 10

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def start_login(self) -> dict:
        await self._cleanup()

        self._ports_before = self._get_listening_ports()

        # Open PTY — claude's Ink UI needs a terminal to render the full URL
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["NO_COLOR"] = "1"

        def make_ctty():
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        self._proc = await asyncio.create_subprocess_exec(
            "claude", "auth", "login",
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            env=env,
            preexec_fn=make_ctty,
        )
        os.close(slave_fd)

        self._auth_url = None
        self._auto_url = None
        self._callback_port = None
        self._state = None

        # Read PTY until we have the full URL (must contain state=)
        try:
            self._auth_url = await asyncio.wait_for(
                self._read_url_from_pty(master_fd), timeout=22
            )
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"Auth start error: {e}")

        if not self._auth_url:
            return {"status": "error", "detail": "Could not capture auth URL"}

        # Extract state from URL
        m = re.search(r'[?&]state=([^&\s]+)', self._auth_url)
        self._state = m.group(1) if m else None

        # Detect the local HTTP callback port claude opened
        self._callback_port = await self._detect_callback_port(timeout=8.0)

        logger.info(
            f"Auth URL captured ({len(self._auth_url)}c) "
            f"port={self._callback_port} "
            f"state={self._state[:20] if self._state else None}…"
        )

        if not self._callback_port:
            return {"status": "error", "detail": "Could not detect callback port. Try again."}

        # Build the AUTOMATIC URL: same as manual but redirect_uri=localhost:PORT
        # The user visits this URL; after OAuth their browser is redirected to
        # localhost:PORT/callback which fails. They copy that URL and paste it here.
        self._auto_url = self._build_auto_url(self._auth_url, self._callback_port)

        return {
            "status": "waiting_for_callback_url",
            "url": self._auto_url,
            "port": self._callback_port,
        }

    async def complete_login(self, token: str) -> dict:
        """
        Accept the full callback URL the user copied from their browser address bar,
        e.g. http://localhost:PORT/callback?code=XXX&state=YYY

        Extract code and state, relay GET to the local callback server.
        The token exchange uses redirect_uri=localhost:PORT, matching the automatic
        URL the user visited. Credentials are saved to ~/.claude/.credentials.json.
        """
        if not self._callback_port:
            return {"status": "error", "detail": "No active auth session. Start auth again."}

        token = token.strip()

        # Accept either:
        # 1. Full callback URL: http://localhost:PORT/callback?code=XXX&state=YYY
        # 2. Just the query string: code=XXX&state=YYY
        # 3. Legacy CODE#STATE format (not used anymore but kept as fallback)
        code, state = self._extract_code_state(token)

        if not code:
            return {
                "status": "error",
                "detail": "Could not extract authorization code. "
                          "Please paste the full URL from your browser address bar.",
            }

        if not state:
            state = self._state or ""

        callback_url = (
            f"http://localhost:{self._callback_port}/callback"
            f"?code={urllib.parse.quote(code, safe='')}"
            f"&state={urllib.parse.quote(state, safe='')}"
        )
        logger.info(f"Relaying callback → port {self._callback_port}, code_len={len(code)}")

        try:
            loop = asyncio.get_event_loop()
            status, body = await loop.run_in_executor(None, self._do_relay, callback_url)
            logger.info(f"Relay result: HTTP {status} — {body[:120]!r}")

            if status is None:
                return {"status": "error", "detail": f"Could not reach callback server: {body}"}

            if status >= 400:
                return {
                    "status": "error",
                    "detail": f"Auth server rejected the code (HTTP {status}). "
                              "The code may be expired — please start over.",
                }

            # Poll for credentials file
            deadline = loop.time() + 30
            while loop.time() < deadline:
                await asyncio.sleep(1)
                if self.is_authenticated():
                    logger.info("Credentials file found — auth successful")
                    return {"status": "ok", "message": "Claude authenticated successfully"}
                if self._proc and self._proc.returncode is not None:
                    await asyncio.sleep(0.5)
                    if self.is_authenticated():
                        return {"status": "ok", "message": "Claude authenticated successfully"}
                    logger.info(f"Process exited with code {self._proc.returncode}")
                    break

            if self.is_authenticated():
                return {"status": "ok", "message": "Claude authenticated successfully"}

            return {"status": "error", "detail": "Auth failed. The code may be expired — please start over."}

        except Exception as e:
            logger.error(f"Auth complete error: {e}")
            return {"status": "error", "detail": str(e)}
        finally:
            await self._cleanup()

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #

    def _build_auto_url(self, manual_url: str, port: int) -> str:
        """Replace redirect_uri in the manual URL with localhost:PORT."""
        local_redirect = f"http://localhost:{port}/callback"
        # Find and replace redirect_uri parameter
        result = re.sub(
            r'(redirect_uri=)[^&]+',
            r'\g<1>' + urllib.parse.quote(local_redirect, safe=''),
            manual_url,
        )
        return result

    def _extract_code_state(self, token: str) -> tuple[str, str]:
        """Extract code and state from various input formats."""
        # Full URL: http://localhost:PORT/callback?code=XXX&state=YYY
        if token.startswith("http"):
            parsed = urllib.parse.urlparse(token)
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get("code", [""])[0]
            state = params.get("state", [""])[0]
            return code, state

        # Query string: code=XXX&state=YYY
        if "code=" in token:
            params = urllib.parse.parse_qs(token)
            code = params.get("code", [""])[0]
            state = params.get("state", [""])[0]
            return code, state

        # Legacy CODE#STATE format
        if "#" in token:
            parts = token.split("#", 1)
            return parts[0], parts[1]

        # Just the code
        return token, ""

    async def _read_url_from_pty(self, master_fd: int, timeout: float = 20.0) -> str | None:
        raw = b""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(0.05)
            try:
                r, _, _ = select.select([master_fd], [], [], 0)
            except (OSError, ValueError):
                break
            if r:
                while True:
                    try:
                        chunk = os.read(master_fd, 65536)
                        if not chunk:
                            break
                        raw += chunk
                    except BlockingIOError:
                        break
                    except OSError:
                        break
            if not raw:
                continue
            clean = re.sub(rb'\x1b\[[0-9;]*[mKHJABCDEFG]', b'', raw)
            clean = re.sub(rb'\x1b\][^\x07]*\x07', b'', clean)
            decoded = clean.decode(errors="replace")
            m = URL_START_RE.search(decoded)
            if m:
                after = decoded[m.start():]
                url = re.sub(r'[\s\x00-\x1f\x7f]', '', after)
                if 'state=' in url:
                    return url
        return None

    def _get_listening_ports(self) -> set[int]:
        ports: set[int] = set()
        for tcp_file in ('/proc/net/tcp', '/proc/net/tcp6'):
            try:
                with open(tcp_file) as f:
                    for line in f.read().split('\n')[1:]:
                        parts = line.split()
                        if len(parts) >= 4 and parts[3] == '0A':
                            port = int(parts[1].split(':')[-1], 16)
                            ports.add(port)
            except Exception:
                pass
        return ports

    async def _detect_callback_port(self, timeout: float = 8.0) -> int | None:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(0.3)
            current = self._get_listening_ports()
            new_ports = current - self._ports_before
            for port in sorted(new_ports, reverse=True):
                if await loop.run_in_executor(None, self._can_connect, port):
                    return port
        # Fallback
        for port in sorted(self._get_listening_ports() - {8080, 22, 80, 443}, reverse=True):
            if await loop.run_in_executor(None, self._can_connect, port):
                return port
        return None

    def _can_connect(self, port: int) -> bool:
        for family, addr in [(socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')]:
            try:
                s = socket.socket(family, socket.SOCK_STREAM)
                s.settimeout(0.5)
                if s.connect_ex((addr, port)) == 0:
                    s.close()
                    return True
                s.close()
            except Exception:
                pass
        return False

    def _do_relay(self, url: str):
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
            'Origin': 'https://platform.claude.com',
            'Referer': 'https://platform.claude.com/oauth/code/callback',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }
        for path in ['/callback', '/']:
            base = url.split('/callback?')[0].split('/?')[0]
            query = url.split('?', 1)[1] if '?' in url else ''
            test_url = f"{base}{path}?{query}" if query else f"{base}{path}"
            try:
                req = urllib.request.Request(test_url, method='GET', headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = resp.read().decode(errors='replace')
                    logger.info(f"Relay {path}: HTTP {resp.status}")
                    return resp.status, body
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors='replace')
                logger.info(f"Relay {path}: HTTP {e.code} — {body[:80]!r}")
                if e.code != 404:
                    return e.code, body
            except Exception as e:
                logger.info(f"Relay {path}: {e}")
        return None, "all relay attempts failed"

    async def _cleanup(self):
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
                await self._proc.wait()
            except Exception:
                pass
        self._proc = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
