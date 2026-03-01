"""BrowserManager — Playwright Chromium singleton for skill automation
and interactive embedded browser sessions.

Usage in skills:
    from core.browser import browser_manager
    async with browser_manager.new_page() as page:
        await page.goto("https://example.com")
        content = await page.content()

Interactive sessions (via API):
    session = await browser_manager.get_session("user_id")
    await session.navigate("https://example.com")
    png = await session.screenshot()
"""

import asyncio
import base64
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

logger = logging.getLogger(__name__)

_playwright = None
_browser = None
_lock = asyncio.Lock()

SESSION_TIMEOUT = 1800  # 30 min inactivity


async def _get_browser():
    global _playwright, _browser
    async with _lock:
        if _browser is None or not _browser.is_connected():
            try:
                from playwright.async_api import async_playwright
                _playwright = await async_playwright().start()
                headless = os.environ.get("BROWSER_HEADLESS", "true").lower() != "false"
                logger.info(f"Launching Chromium {'non-headless' if not headless else 'headless'}")
                _browser = await _playwright.chromium.launch(
                    headless=headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                )
            except ImportError:
                raise RuntimeError("Playwright not installed.")
    return _browser


class BrowserSession:
    """Persistent browser session for a user (context + page kept alive)."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._context = None
        self._page = None
        self._last_used = time.time()
        self.viewport = {"width": 1280, "height": 800}

    async def _ensure(self):
        self._last_used = time.time()
        if self._page is None or self._page.is_closed():
            browser = await _get_browser()
            storage_path = DATA_DIR / f"browser_storage_{self.user_id}.json"
            ctx_kwargs = dict(
                viewport=self.viewport,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            if storage_path.exists():
                ctx_kwargs["storage_state"] = str(storage_path)
                logger.info(f"Browser session restored from storage for {self.user_id}")
            self._context = await browser.new_context(**ctx_kwargs)
            self._page = await self._context.new_page()
            logger.info(f"Browser session created for {self.user_id}")

    async def navigate(self, url: str) -> dict:
        await self._ensure()
        if not url.startswith("http"):
            url = "https://" + url
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            logger.warning(f"Navigate timeout/error: {e}")
        return await self._state()

    async def go_back(self) -> dict:
        await self._ensure()
        try:
            await self._page.go_back(wait_until="domcontentloaded", timeout=10000)
        except Exception:
            pass
        return await self._state()

    async def refresh(self) -> dict:
        await self._ensure()
        try:
            await self._page.reload(wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        return await self._state()

    async def click(self, x: int, y: int) -> dict:
        await self._ensure()
        try:
            await self._page.mouse.click(x, y)
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Click error: {e}")
        return await self._state()

    async def type_text(self, text: str) -> dict:
        await self._ensure()
        try:
            await self._page.keyboard.type(text)
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"Type error: {e}")
        return await self._state()

    async def press_key(self, key: str) -> dict:
        """key: 'Enter', 'Tab', 'Escape', 'Backspace', etc."""
        await self._ensure()
        try:
            await self._page.keyboard.press(key)
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Key press error: {e}")
        return await self._state()

    async def scroll(self, delta_y: int) -> dict:
        await self._ensure()
        try:
            await self._page.mouse.wheel(0, delta_y)
            await asyncio.sleep(0.2)
        except Exception as e:
            logger.warning(f"Scroll error: {e}")
        return await self._state()

    async def screenshot_b64(self) -> str:
        await self._ensure()
        try:
            png = await self._page.screenshot(type="png", full_page=False)
            return base64.b64encode(png).decode()
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            return ""

    async def get_cookies(self) -> list:
        await self._ensure()
        try:
            return await self._context.cookies()
        except Exception:
            return []

    async def get_page_text(self) -> str:
        """Return the visible text content of the current page."""
        await self._ensure()
        try:
            return await self._page.inner_text("body")
        except Exception as e:
            logger.error(f"get_page_text error: {e}")
            return ""

    async def save_storage_state(self) -> str:
        """Save cookies + localStorage to /data/browser_storage_{user_id}.json"""
        await self._ensure()
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = DATA_DIR / f"browser_storage_{self.user_id}.json"
            await self._context.storage_state(path=str(path))
            cookies = await self._context.cookies()
            logger.info(f"Storage state saved for {self.user_id} ({len(cookies)} cookies)")
            return str(path)
        except Exception as e:
            logger.error(f"Save storage state error: {e}")
            return ""

    async def current_url(self) -> str:
        if self._page and not self._page.is_closed():
            return self._page.url
        return ""

    async def _state(self) -> dict:
        img = await self.screenshot_b64()
        url = await self.current_url()
        return {"url": url, "screenshot": img}

    def is_expired(self) -> bool:
        return time.time() - self._last_used > SESSION_TIMEOUT

    async def close(self):
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        self._context = None
        self._page = None
        logger.info(f"Browser session closed for {self.user_id}")


class BrowserManager:
    """Singleton manager for Playwright browser sessions."""

    def __init__(self):
        self._sessions: dict[str, BrowserSession] = {}

    async def is_available(self) -> bool:
        try:
            await _get_browser()
            return True
        except Exception:
            return False

    async def get_session(self, user_id: str) -> BrowserSession:
        """Get or create a persistent session for a user."""
        if user_id in self._sessions and not self._sessions[user_id].is_expired():
            return self._sessions[user_id]
        # Clean up expired
        if user_id in self._sessions:
            await self._sessions[user_id].close()
        session = BrowserSession(user_id)
        self._sessions[user_id] = session
        return session

    async def close_session(self, user_id: str):
        if user_id in self._sessions:
            await self._sessions[user_id].close()
            del self._sessions[user_id]

    async def has_session(self, user_id: str) -> bool:
        if user_id not in self._sessions:
            return False
        s = self._sessions[user_id]
        if s.is_expired():
            return False
        if s._page is None:
            return False
        try:
            return not s._page.is_closed()
        except Exception:
            return False

    @asynccontextmanager
    async def new_page(self, **context_kwargs) -> AsyncIterator:
        """Context manager that yields a fresh Playwright page (for skills)."""
        browser = await _get_browser()
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        try:
            yield page
        finally:
            await context.close()

    @asynccontextmanager
    async def new_context(self, **kwargs) -> AsyncIterator:
        """Context manager that yields a browser context (for skills)."""
        browser = await _get_browser()
        context = await browser.new_context(**kwargs)
        try:
            yield context
        finally:
            await context.close()

    async def screenshot(self, url: str, **kwargs) -> bytes:
        """Capture a screenshot of a URL. Returns PNG bytes."""
        async with self.new_page() as page:
            await page.goto(url, wait_until="networkidle")
            return await page.screenshot(**kwargs)

    async def get_text(self, url: str) -> str:
        """Get visible text content from a URL."""
        async with self.new_page() as page:
            await page.goto(url, wait_until="domcontentloaded")
            return await page.evaluate("document.body.innerText")

    async def close(self):
        global _playwright, _browser
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        if _browser:
            await _browser.close()
            _browser = None
        if _playwright:
            await _playwright.stop()
            _playwright = None


# Singleton
browser_manager = BrowserManager()
