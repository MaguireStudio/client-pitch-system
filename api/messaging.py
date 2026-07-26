"""SMS (Twilio) and email (Resend) delivery.

SAFETY: sending is OFF unless MESSAGING_LIVE=true, even when provider credentials are
present. Without it every send is recorded as a dry run and no request leaves the
process. This makes it impossible to message a real client by accident while the CRM
is still being set up.

Each function returns (status, provider_id, error):
  status: sent | dry_run | failed | unconfigured
"""

from __future__ import annotations

import base64
import logging
import os
import re

import httpx

logger = logging.getLogger("client-pitch-system.messaging")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "").strip()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "").strip()

# The master switch. Anything other than an explicit truthy value keeps sending off.
MESSAGING_LIVE = os.getenv("MESSAGING_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}

TIMEOUT = float(os.getenv("MESSAGING_TIMEOUT", "12"))

_E164 = re.compile(r"^\+?[1-9]\d{7,14}$")


def sms_configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


def email_configured() -> bool:
    return bool(RESEND_API_KEY and RESEND_FROM_EMAIL)


def normalize_phone(raw: str) -> str:
    """Best-effort E.164. Assumes +1 for bare 10-digit US numbers."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if not digits:
        return ""
    if digits.startswith("+"):
        return digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def status_summary() -> dict:
    return {
        "live": MESSAGING_LIVE,
        "sms": {
            "provider": "twilio",
            "configured": sms_configured(),
            "from": TWILIO_FROM_NUMBER or None,
        },
        "email": {
            "provider": "resend",
            "configured": email_configured(),
            "from": RESEND_FROM_EMAIL or None,
        },
    }


async def send_sms(to: str, body: str) -> tuple[str, str, str]:
    to = normalize_phone(to)
    if not to or not _E164.match(to):
        return "failed", "", f"invalid destination phone number: {to or '(empty)'}"
    if not sms_configured():
        return "unconfigured", "", "Twilio not configured (set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER)"
    if not MESSAGING_LIVE:
        logger.info("DRY RUN sms -> %s (%d chars)", to, len(body))
        return "dry_run", "", ""

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    auth = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                url,
                data={"To": to, "From": TWILIO_FROM_NUMBER, "Body": body},
                headers={"Authorization": f"Basic {auth}"},
            )
        if resp.status_code >= 400:
            # Twilio puts a human-readable reason in "message".
            detail = ""
            try:
                detail = resp.json().get("message", "")
            except Exception:
                detail = resp.text[:300]
            return "failed", "", f"Twilio {resp.status_code}: {detail}"
        return "sent", resp.json().get("sid", ""), ""
    except Exception as exc:
        logger.exception("twilio send failed")
        return "failed", "", str(exc)


async def send_email(to: str, subject: str, body: str) -> tuple[str, str, str]:
    if not to or "@" not in to:
        return "failed", "", f"invalid destination email: {to or '(empty)'}"
    if not email_configured():
        return "unconfigured", "", "Resend not configured (set RESEND_API_KEY, RESEND_FROM_EMAIL)"
    if not MESSAGING_LIVE:
        logger.info("DRY RUN email -> %s (%r)", to, subject)
        return "dry_run", "", ""

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": RESEND_FROM_EMAIL,
                    "to": [to],
                    "subject": subject or "(no subject)",
                    "text": body,
                },
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("message", "")
            except Exception:
                detail = resp.text[:300]
            return "failed", "", f"Resend {resp.status_code}: {detail}"
        return "sent", resp.json().get("id", ""), ""
    except Exception as exc:
        logger.exception("resend send failed")
        return "failed", "", str(exc)
