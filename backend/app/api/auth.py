"""Browser-driven OAuth, so authorising does not mean opening a terminal.

`scripts/yt_authorize.py` still works and is still the fallback when
this server has no public URL. But it has to run *on the box*, which
means the one thing that reliably breaks uploads - an expired refresh
token - can only be fixed by someone with a shell on the server. These
endpoints move that to a button.

### What this needs that the script did not

The script uses an OAuth client of type **Desktop**, which redirects to
`http://localhost:<port>` and works because the browser and the script
are on the same machine. A browser flow driven from another device
cannot use that. It needs a **Web application** client with this
server's callback registered as an authorised redirect URI:

    <public VME url>/api/auth/google/callback

Both client shapes are accepted below (`installed` vs `web` key), and
a Desktop client is detected and explained rather than left to fail at
Google with a `redirect_uri_mismatch` nobody can act on.

### State

The `state` parameter is a single-use nonce held in memory. A restart
mid-flow invalidates it, which is correct: the alternative is
persisting a CSRF token, and the failure mode of losing one is that
the user presses Connect again.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..upload import onedrive, youtube

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"

# A flow the user is part-way through. Short-lived by design: this is a
# CSRF nonce, not a session.
_PENDING: dict[str, "_Pending"] = {}
_PENDING_TTL_S = 600


@dataclass
class _Pending:
    return_to: str
    redirect_uri: str
    created: float


def _safe_return_to(value: str) -> str:
    r"""A path on this site, or `/`.

    `startswith("/")` is not enough. A **protocol-relative** URL like
    `//evil.example/x` also starts with a slash, and every browser reads
    it as `https://evil.example/x` - so the naive check turns the
    callback into an open redirect. `/\evil.example` is the same trick
    with a backslash, which some browsers normalise to `//`.
    """
    if not value.startswith("/"):
        return "/"
    if value.startswith("//") or value.startswith("/\\"):
        return "/"
    return value


def _sweep() -> None:
    cutoff = time.time() - _PENDING_TTL_S
    for key in [k for k, v in _PENDING.items() if v.created < cutoff]:
        _PENDING.pop(key, None)


class ClientInfo(BaseModel):
    present: bool
    # "web" | "installed" | None. A Desktop ("installed") client cannot
    # drive the browser flow, and saying so here is what lets the UI
    # explain it instead of the user meeting a redirect_uri_mismatch.
    kind: Optional[str] = None


class AuthStatusOut(BaseModel):
    provider: str
    connected: bool
    # Scopes the saved grant carries, so the UI can say *what* it can do
    # rather than just that something is stored.
    scopes: list[str] = []
    client: ClientInfo
    # Absolute URL that must be registered with the provider. Shown in
    # the UI so it can be copied rather than assembled by hand.
    redirect_uri: str
    reason: str = ""


def _read_client_secret() -> tuple[Optional[dict], Optional[str]]:
    """Return (config, kind) from the saved client-secret JSON."""
    path = youtube.client_secrets_path()
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    for kind in ("web", "installed"):
        if kind in data:
            return data[kind], kind
    return None, None


# A redirect URI on any of these can legitimately be http; everywhere
# else it cannot, because no OAuth provider accepts a plain-http
# callback to a public host.
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _callback_uri(request: Request, name: str = "google_callback") -> str:
    """The absolute callback URL for this deployment.

    Built from the request, so moving host needs no config change. Two
    corrections on top of that:

    **Scheme.** IIS's ARR does not forward `X-Forwarded-Proto` unless a
    server variable is whitelisted in `applicationHost.config`, so
    uvicorn sees plain http even though the world reached us over TLS -
    and we then hand the provider `http://...`, which it rejects as not
    matching the `https://...` that is registered (it could not have
    been registered as http: providers refuse those for public hosts).
    Rather than require an IIS change, force https for anything that is
    not loopback. This cannot be wrong in a way that matters: an http
    callback to a public host is unusable by definition.

    **Override.** `VME_PUBLIC_URL` wins outright, for deployments where
    the host header is not what the outside world uses. Only its scheme
    and host are taken: the path always comes from `url_for`, which
    already carries the `/vme` prefix `--root-path` adds. Pasting a full
    URL in there would otherwise prefix it twice. A bare host is
    accepted and assumed https, because that is how people write one.
    """
    url = request.url_for(name)
    override = os.environ.get("VME_PUBLIC_URL", "").strip()
    if override:
        parts = urlsplit(override if "//" in override else f"https://{override}")
        if parts.netloc:
            scheme = parts.scheme or "https"
            return f"{scheme}://{parts.netloc}{url.path}"
    if url.hostname not in _LOOPBACK and url.scheme != "https":
        url = url.replace(scheme="https")
    return str(url)


@router.get("/google/status", response_model=AuthStatusOut)
def google_status(request: Request) -> AuthStatusOut:
    cfg, kind = _read_client_secret()
    scopes = youtube.granted_scopes()
    connected = youtube.token_path().is_file()

    reason = ""
    if cfg is None:
        reason = (
            "No OAuth client is configured. Upload the client-secret JSON "
            "downloaded from Google Cloud Console."
        )
    elif kind == "installed":
        reason = (
            "The saved client is a Desktop app, which can only redirect to "
            "localhost. Create an OAuth client of type 'Web application' "
            "and add this server's callback URL to it."
        )
    elif not connected:
        reason = "Not connected yet."

    return AuthStatusOut(
        provider="google",
        connected=connected,
        scopes=scopes,
        client=ClientInfo(present=cfg is not None, kind=kind),
        redirect_uri=_callback_uri(request),
        reason=reason,
    )


@router.post("/google/client-secret", response_model=AuthStatusOut)
async def upload_client_secret(request: Request, file: UploadFile = File(...)) -> AuthStatusOut:
    """Save the client-secret JSON, so setup needs no filesystem access."""
    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, f"That is not a JSON file: {exc}") from exc
    if not any(k in data for k in ("web", "installed")):
        raise HTTPException(
            400,
            "That JSON has neither a 'web' nor an 'installed' section, so it "
            "is not an OAuth client secret from Google Cloud Console.",
        )
    path = youtube.client_secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    logger.info("saved OAuth client secret to %s", path)
    return google_status(request)


@router.post("/google/start")
def google_start(request: Request, return_to: str = "/") -> dict:
    """Begin the consent flow; the UI sends the browser to `auth_url`."""
    cfg, kind = _read_client_secret()
    if cfg is None:
        raise HTTPException(400, "No OAuth client is configured.")
    if kind != "web":
        raise HTTPException(
            400,
            "This is a Desktop OAuth client. The browser flow needs one of "
            "type 'Web application' with this server's callback registered.",
        )
    # There is no reason to leave this site after consent, and every
    # reason not to let a caller choose where we land.
    return_to = _safe_return_to(return_to)

    _sweep()
    state = secrets.token_urlsafe(32)
    redirect_uri = _callback_uri(request)
    _PENDING[state] = _Pending(return_to, redirect_uri, time.time())

    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(youtube.SCOPES),
        "state": state,
        # Both are required to get a refresh token back: without
        # `consent` Google returns only an access token when the user
        # has already approved these scopes, and the whole point here is
        # the refresh token.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return {"auth_url": f"{GOOGLE_AUTH}?{urlencode(params)}"}


@router.get("/google/callback", name="google_callback")
def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
) -> RedirectResponse:
    """Exchange the code for tokens and save them.

    Always redirects back into the app rather than returning JSON: this
    URL is visited by the browser, not by the UI's fetch layer, so the
    only useful outcome is landing the user somewhere with a message.
    """
    _sweep()
    pending = _PENDING.pop(state, None)
    target = pending.return_to if pending else "/"

    def back(status: str) -> RedirectResponse:
        sep = "&" if "?" in target else "?"
        return RedirectResponse(f"{target}{sep}google_auth={status}", status_code=303)

    if error:
        logger.warning("google consent refused: %s", error)
        return back("denied")
    if pending is None:
        # Expired, replayed, or the server restarted mid-flow.
        return back("expired")
    if not code:
        return back("failed")

    cfg, _ = _read_client_secret()
    if cfg is None:
        return back("failed")

    try:
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {"web": cfg}, scopes=youtube.SCOPES, state=state
        )
        flow.redirect_uri = pending.redirect_uri
        flow.fetch_token(code=code)
        creds = flow.credentials
        if not creds.refresh_token:
            # Without one, uploads die in an hour and cannot recover.
            logger.error("google returned no refresh token")
            return back("no_refresh_token")
        youtube.token_path().parent.mkdir(parents=True, exist_ok=True)
        youtube.token_path().write_text(creds.to_json(), encoding="utf-8")
        logger.info("saved google token to %s", youtube.token_path())
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a status
        logger.exception("google token exchange failed: %s", exc)
        return back("failed")

    return back("ok")


@router.post("/google/disconnect", response_model=AuthStatusOut)
def google_disconnect(request: Request) -> AuthStatusOut:
    """Forget the saved token.

    The client secret is left in place: disconnecting is something you
    do to sign in as someone else, and making that mean re-uploading the
    client JSON as well would be a chore with no security value.
    """
    tok = youtube.token_path()
    if tok.is_file():
        tok.unlink()
        logger.info("removed %s", tok)
    return google_status(request)


# ===================================================================
# Microsoft / OneDrive
# ===================================================================
#
# Same three steps as Google, and deliberately the same shapes, so the
# settings UI can render either from one component. The differences are
# real but small: Microsoft takes the client id and secret as two
# fields rather than a downloaded JSON, and `common` as the tenant so
# both personal and work accounts can consent.


class MicrosoftClientIn(BaseModel):
    client_id: str
    # Blank is legitimate: an app registered as a *public* client has no
    # secret. It still works here because the refresh happens
    # server-side with the same parameters.
    client_secret: str = ""


@router.get("/microsoft/status", response_model=AuthStatusOut)
def microsoft_status(request: Request) -> AuthStatusOut:
    s = onedrive.get_status()
    detail = s.reason
    if s.configured and s.drive_type:
        detail = f"{s.drive_type} drive"
        if s.account:
            detail += f" - {s.account}"
        if s.drive_type != "personal":
            # Worth saying before they wonder why parents are being
            # asked to sign in.
            detail += (
                ". Anonymous share links and custom thumbnails may be "
                "blocked by tenant policy on this drive type."
            )
    return AuthStatusOut(
        provider="microsoft",
        connected=s.has_token and s.configured,
        scopes=onedrive.SCOPES if s.has_token else [],
        client=ClientInfo(present=s.has_client, kind="web" if s.has_client else None),
        redirect_uri=_callback_uri(request, "microsoft_callback"),
        reason=detail,
    )


@router.post("/microsoft/client", response_model=AuthStatusOut)
def microsoft_client(request: Request, body: MicrosoftClientIn) -> AuthStatusOut:
    if not body.client_id.strip():
        raise HTTPException(400, "A client ID is required.")
    onedrive.save_client(body.client_id.strip(), body.client_secret.strip())
    logger.info("saved Microsoft app registration")
    return microsoft_status(request)


@router.post("/microsoft/start")
def microsoft_start(request: Request, return_to: str = "/") -> dict:
    client = onedrive.read_client()
    if client is None:
        raise HTTPException(400, "No Microsoft app is configured.")
    return_to = _safe_return_to(return_to)

    _sweep()
    state = secrets.token_urlsafe(32)
    redirect_uri = _callback_uri(request, "microsoft_callback")
    _PENDING[state] = _Pending(return_to, redirect_uri, time.time())

    params = {
        "client_id": client["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": " ".join(onedrive.SCOPES),
        "state": state,
    }
    return {"auth_url": f"{onedrive.AUTHORITY}/authorize?{urlencode(params)}"}


@router.get("/microsoft/callback", name="microsoft_callback")
def microsoft_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
) -> RedirectResponse:
    _sweep()
    pending = _PENDING.pop(state, None)
    target = pending.return_to if pending else "/"

    def back(status: str) -> RedirectResponse:
        sep = "&" if "?" in target else "?"
        return RedirectResponse(
            f"{target}{sep}microsoft_auth={status}", status_code=303
        )

    if error:
        logger.warning("microsoft consent refused: %s", error)
        return back("denied")
    if pending is None:
        return back("expired")
    if not code:
        return back("failed")

    client = onedrive.read_client()
    if client is None:
        return back("failed")

    try:
        import requests as http

        resp = http.post(
            f"{onedrive.AUTHORITY}/token",
            data={
                "client_id": client["client_id"],
                "client_secret": client.get("client_secret", ""),
                "code": code,
                "redirect_uri": pending.redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(onedrive.SCOPES),
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(
                "microsoft token exchange failed: %s %s",
                resp.status_code,
                resp.text[:300],
            )
            return back("failed")
        payload = resp.json()
        if not payload.get("refresh_token"):
            # Means `offline_access` was not granted; uploads would stop
            # in an hour with nothing able to recover them.
            logger.error("microsoft returned no refresh token")
            return back("no_refresh_token")
        onedrive.save_token(payload)
        logger.info("saved OneDrive token to %s", onedrive.token_path())
    except Exception as exc:  # noqa: BLE001 - reported to the user as a status
        logger.exception("microsoft token exchange failed: %s", exc)
        return back("failed")

    return back("ok")


@router.post("/microsoft/disconnect", response_model=AuthStatusOut)
def microsoft_disconnect(request: Request) -> AuthStatusOut:
    tok = onedrive.token_path()
    if tok.is_file():
        tok.unlink()
        logger.info("removed %s", tok)
    return microsoft_status(request)


__all__ = ["router"]
