"""Notifications — send messages via Telegram and/or Email."""

import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


async def send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    """Send a Telegram message via Bot API."""
    try:
        import httpx
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            ok = resp.status_code == 200
            if not ok:
                logger.error(f"Telegram send failed: {resp.text}")
            return ok
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


async def send_email(smtp_config: dict, to: str, subject: str, body: str, html: bool = False) -> bool:
    """Send an email via SMTP (runs in executor to avoid blocking)."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_email_sync, smtp_config, to, subject, body, html)


def _send_email_sync(smtp_config: dict, to: str, subject: str, body: str, html: bool = False) -> bool:
    try:
        msg = EmailMessage()
        from_name = smtp_config.get("from_name", "openNoClaw")
        from_email = smtp_config.get("from_email", smtp_config.get("smtp_user", ""))
        msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        msg["To"] = to
        msg["Subject"] = subject
        if html:
            msg.set_content("This email requires an HTML-capable client.", subtype="plain")
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body)

        host = smtp_config.get("smtp_host", "smtp.gmail.com")
        port = int(smtp_config.get("smtp_port", 587))
        user = smtp_config.get("smtp_user", "")
        password = smtp_config.get("smtp_password", "")

        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        logger.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Email send error: {e}")
        return False


async def send_email_smart(gmail_manager, connexion_manager, user_id: str,
                           to: str | None, subject: str, body: str, html: bool = False) -> dict:
    """Send email using Gmail OAuth if connected, otherwise SMTP.
    If `to` is empty/None, falls back to the user's configured notify/from email.
    Returns {ok, method, message}."""
    if gmail_manager is not None and gmail_manager.is_connected(user_id):
        try:
            # Gmail: if no `to`, use the Gmail account address itself
            gmail_to = to or gmail_manager._load(user_id).get("email", "")
            if not gmail_to:
                logger.warning("Gmail send: no destination address")
            else:
                await gmail_manager.send_message(user_id, gmail_to, subject, body, html=html)
                logger.info(f"Email sent via Gmail to {gmail_to}: {subject}")
                return {"ok": True, "method": "gmail", "message": f"Sent via Gmail to {gmail_to}"}
        except Exception as e:
            logger.warning(f"Gmail send failed, falling back to SMTP: {e}")

    if connexion_manager is not None:
        email_cfg = connexion_manager.get_email(user_id)
        if email_cfg.get("enabled") and email_cfg.get("smtp_host") and email_cfg.get("smtp_user"):
            smtp_to = to or email_cfg.get("notify_email") or email_cfg.get("from_email") or email_cfg.get("smtp_user")
            ok = await send_email(email_cfg, smtp_to, subject, body, html=html)
            return {"ok": ok, "method": "smtp", "message": f"{'Sent' if ok else 'Failed'} via SMTP to {smtp_to}"}

    return {"ok": False, "method": None, "message": "No email configured (Gmail not connected, SMTP not set up)"}


async def notify(connexion_manager, user_id: str, channels: list[str], subject: str, body: str, html: bool = False):
    """Send notification to a user via specified channels.
    If html is not explicitly set, auto-detect from body content."""
    results = {}
    is_html = html or body.lstrip().startswith("<")

    if "telegram" in channels:
        tg = connexion_manager.get_telegram(user_id)
        if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
            # Telegram gets a plain summary (strip HTML tags for readability)
            import re
            plain = re.sub(r"<[^>]+>", "", body) if is_html else body
            plain = plain.strip()[:1500]
            msg = f"<b>{subject}</b>\n\n{plain}"
            results["telegram"] = await send_telegram(tg["bot_token"], tg["chat_id"], msg)
        else:
            logger.warning(f"Telegram not configured for {user_id}")
            results["telegram"] = False

    if "email" in channels:
        email_cfg = connexion_manager.get_email(user_id)
        if email_cfg.get("enabled") and email_cfg.get("smtp_host") and email_cfg.get("smtp_user"):
            to = email_cfg.get("notify_email") or email_cfg.get("from_email") or email_cfg.get("smtp_user")
            results["email"] = await send_email(email_cfg, to, subject, body, html=is_html)
        else:
            logger.warning(f"Email not configured for {user_id}")
            results["email"] = False

    return results
