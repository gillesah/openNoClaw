"""Email triage quotidien — appelé par le cron email-triage.

Logique :
1. Liste les emails non lus (max 25)
2. Newsletters/promos reçues depuis > 24h → archive
3. Newsletters récentes (< 24h) → laisse en inbox pour que Gilles puisse les lire
4. Emails suspects → alerte Telegram, ne pas toucher
5. Envoie un résumé par email à Gilles

Sécurité : lit uniquement headers + snippet, ne suit aucun lien, pas de pièces jointes.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import httpx

BASE = os.environ.get("OPENCLAW_BASE", "http://localhost:8080")
TOKEN = os.environ.get("OPENCLAW_TOKEN", "")
MAX_EMAILS = 60
NEWSLETTER_DELAY_HOURS = 24  # garder les newsletters 24h avant d'archiver

# Mots-clés newsletters/promos
NEWSLETTER_KEYWORDS = [
    "newsletter", "noreply", "no-reply", "promo", "unsubscribe", "marketing",
    "offre", "promotion", "deal", "soldes", "subscription", "digest",
    "weekly", "daily", "monthly", "info@", "contact@", "hello@",
    "team@", "notification@", "donotreply", "do-not-reply",
    "facebookmail.com", "notifications-noreply@linkedin.com",
    "groups-noreply@linkedin.com", "security-noreply@linkedin.com",
    "tldrnewsletter.com", "newsletter-investir", "meetup.com",
    "discover@airbnb.com", "no_reply@communications.paypal.com",
    "annonce@amazon.fr", "enquetesatisfaction@chronopost.fr",
    "friendupdates@facebookmail.com", "substack.com",
    "families-noreply@google.com", "no-reply@accounts.google.com",
    "azure-noreply@microsoft.com", "sc-noreply@google.com",
    "no-reply@vinted.fr",
]

# Mots-clés suspects (phishing, arnaque — volontairement stricts)
SUSPICIOUS_KEYWORDS = [
    "vérifiez votre compte", "verify your account", "suspended",
    "confirm your password", "click here immediately", "your account will be",
    "wire transfer", "crypto payment", "inheritance", "lottery",
    "you have won", "claim your prize",
]


def is_newsletter(frm: str, subj: str) -> bool:
    combined = (frm + " " + subj).lower()
    return any(kw in combined for kw in NEWSLETTER_KEYWORDS)


def is_suspicious(frm: str, subj: str, snippet: str) -> bool:
    combined = (frm + " " + subj + " " + snippet).lower()
    return any(kw in combined for kw in SUSPICIOUS_KEYWORDS)


def email_age_hours(date_str: str) -> float | None:
    """Retourne l'âge de l'email en heures, ou None si la date est illisible."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt
        return age.total_seconds() / 3600
    except Exception:
        return None


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        # 1. Récupérer les emails non lus
        r = await c.get(f"{BASE}/api/gmail/messages",
                        params={"token": TOKEN, "max": MAX_EMAILS})
        if r.status_code != 200:
            print(f"Gmail non connecté ou erreur: {r.status_code} {r.text[:200]}")
            sys.exit(1)

        msgs = r.json().get("messages", [])
        print(f"Emails non lus récupérés : {len(msgs)}")

        actions = []
        alerts = []

        for m in msgs:
            msg_id = m.get("id", "")
            frm = m.get("from", "")
            subj = m.get("subject", "")
            snippet = m.get("snippet", "")
            date = m.get("date", "")

            if is_suspicious(frm, subj, snippet):
                alerts.append(f"⚠️ Email suspect :\nDe : {frm}\nSujet : {subj}\nSnippet : {snippet[:200]}")
                actions.append(f"🚨 SUSPECT  | {frm} | {subj}")
                continue

            if is_newsletter(frm, subj):
                age = email_age_hours(date)
                if age is not None and age < NEWSLETTER_DELAY_HOURS:
                    actions.append(f"⏳ ATTENTE   | {frm} | {subj} ({age:.0f}h — archivage dans {NEWSLETTER_DELAY_HOURS - age:.0f}h)")
                else:
                    try:
                        await c.post(f"{BASE}/api/gmail/messages/{msg_id}/archive",
                                     params={"token": TOKEN})
                        actions.append(f"📁 ARCHIVÉ  | {frm} | {subj}")
                    except Exception as e:
                        actions.append(f"❌ ERREUR   | {frm} | {subj} ({e})")
            else:
                actions.append(f"📥 INBOX    | {frm} | {subj}")

        # 2. Alertes Telegram
        for alert in alerts:
            await c.post(f"{BASE}/api/connexions/notify",
                         params={"token": TOKEN},
                         json={"channel": "telegram", "message": alert})

        # 3. Résumé
        archived = sum(1 for a in actions if "ARCHIVÉ" in a)
        kept = sum(1 for a in actions if "INBOX" in a)
        waiting = sum(1 for a in actions if "ATTENTE" in a)
        suspect = len(alerts)

        summary_lines = [
            f"Tri email du jour — {len(msgs)} email(s) traité(s)",
            f"Archivés : {archived} | Inbox : {kept} | En attente 24h : {waiting} | Suspects : {suspect}",
            "",
            *actions,
        ]
        if alerts:
            summary_lines += ["", "--- Alertes Telegram envoyées ---", *alerts]

        summary = "\n".join(summary_lines)
        print(summary)

        # 4. Envoyer le résumé par email
        r2 = await c.post(f"{BASE}/api/actions/send-email",
                          params={"token": TOKEN},
                          json={
                              "subject": f"[openNoClaw] Tri emails — {archived} archivés, {kept} inbox, {waiting} en attente",
                              "body": summary,
                          })
        print(f"Résumé email : {r2.status_code} {r2.text[:100]}")


if __name__ == "__main__":
    asyncio.run(main())
