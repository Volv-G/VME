"""Send plain-text email from the Google account VME is signed in to.

Rides on the YouTube sign-in (Settings -> Connections), which asks for
`gmail.send` as well (youtube.CONSENT_SCOPES). Its access token is minted
for that one scope and kept in memory only: writing it back to the token
file would record the grant as mail-only and confuse every other reader.
"""

from __future__ import annotations

import base64
import threading
from email.message import EmailMessage
from typing import Any, Optional

import requests

from . import youtube

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

_lock = threading.Lock()
_creds: Any = None


def available() -> bool:
    """The saved grant includes sending mail and is not known to be dead."""
    return (
        youtube.token_path().is_file()
        and youtube.GMAIL_SEND in youtube.granted_scopes()
        and not youtube.stale_auth_error()
    )


def _token() -> str:
    global _creds
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    with _lock:
        # `valid` already treats a token about to expire as expired.
        if _creds is None or not _creds.valid:
            creds = Credentials.from_authorized_user_file(str(youtube.token_path()), [youtube.GMAIL_SEND])
            creds.refresh(Request())
            _creds = creds
        return _creds.token


def send(to: str, subject: str, body: str, reply_to: Optional[str] = None) -> None:
    """Send one message; raises on failure (the caller decides whether to retry)."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    r = requests.post(
        SEND_URL,
        json={"raw": raw},
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=30,
    )
    if r.status_code >= 300:
        try:
            detail = r.json().get("error", {}).get("message", "")
        except ValueError:
            detail = r.text[:200]
        raise RuntimeError(f"Gmail refused the message: {r.status_code} {detail}")
