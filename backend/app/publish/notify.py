"""Email the site's notices (courtside/server/notices.ts) from Gmail.

The site has no mail of its own, so what it wants said waits there and
comes down with each sync (publish/site_sync.py):

- "request":  someone asked to join a team -> each admin, with a link
  to approve it (Reply-To is the person asking).
- "approved": their request was approved -> the person who asked, with
  a link to the team.

Sent from the Google account VME is signed in to (upload/gmail.py).
Until that grant can send mail, notices stay on the site and go out once
it can; the site drops any older than two weeks.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..upload import gmail

logger = logging.getLogger(__name__)

# Say once an hour (not every minute) that notices are waiting.
_QUIET_SECONDS = 3600.0
_last_warning = 0.0


def _who(m: dict) -> str:
    if m.get("role") == "coach":
        return "a coach"
    return f"the family of {m.get('player_name') or 'a player'}"


def compose(notice: dict, site_url: str, admins: list[str]) -> list[tuple[str, str, str, str | None]]:
    """(to, subject, body, reply_to) for each email one notice makes."""
    m = notice.get("member") or {}
    email = str(m.get("email") or "")
    team = str(m.get("team") or "")
    team_name = str(m.get("team_name") or team)
    base = site_url.rstrip("/")
    if notice.get("kind") == "request":
        note = str(m.get("note") or "").strip()
        body = f"{email} asked to join {team_name} as {_who(m)}.\n"
        if note:
            body += f'Their note: "{note}"\n'
        body += f"\nApprove or decline: {base}/admin\n(or in VME, from the bell at the top.)\n"
        subject = f"Courtside VB: {email} asks to join {team_name}"
        return [(a, subject, body, email or None) for a in admins]
    if notice.get("kind") == "approved" and email:
        body = (
            f"Your request to join {team_name} as {_who(m)} has been approved.\n\n"
            f"Open the team: {base}/t/{team}\n\n"
            f"Sign in with this address ({email}) to see the matches, videos and stats.\n"
        )
        return [(email, f"You're in: {team_name} on Courtside VB", body, None)]
    return []


def send_all(data: dict[str, Any], site_url: str) -> list[int]:
    """Send what the sync brought; return the ids that are done with."""
    global _last_warning
    notices = data.get("notices") or []
    if not notices:
        return []
    if not gmail.available():
        if time.time() - _last_warning > _QUIET_SECONDS:
            _last_warning = time.time()
            logger.warning(
                "%d site notice(s) waiting: VME's Google connection cannot send "
                "mail. Reconnect Google in Settings -> Connections.",
                len(notices),
            )
        return []
    admins = [a for a in (data.get("admins") or []) if isinstance(a, str) and "@" in a]
    done: list[int] = []
    for n in notices:
        try:
            for to, subject, body, reply_to in compose(n, site_url, admins):
                gmail.send(to, subject, body, reply_to=reply_to)
            done.append(int(n["id"]))
            logger.info("site notice %s (%s) emailed", n.get("id"), n.get("kind"))
        except Exception as exc:  # noqa: BLE001 - try again next round
            logger.warning("site notice %s not sent: %s", n.get("id"), exc)
            break  # most failures (auth, network) would fail the rest too
    return done
