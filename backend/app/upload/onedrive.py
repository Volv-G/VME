"""Upload to OneDrive through Microsoft Graph.

### Why this exists

YouTube's Data API allows 10,000 quota units a day and `videos.insert`
costs 1,600 of them - six uploads. A match day here is a full render
plus a reel per player, so the reels alone blow through three days of
quota. There is no paid tier for that quota; the only sanctioned
increase is an audit, and even then a separate per-channel publish cap
is waiting behind it.

OneDrive has no equivalent per-day cap. Storage is the only limit and a
season of reels is tens of gigabytes against a terabyte.

### The split this is built for

Matches stay on YouTube: they are large, watched by everyone, and
YouTube serves that bandwidth free while also providing the live
stream, discovery and subscriber notifications. Reels are small,
watched by one family each, and cost almost nothing to host anywhere -
they are the entire quota problem and none of the benefit.

### Auth

Same shape as `youtube.py`: a client registered by the operator, a
one-time browser consent, and a refresh token on disk. Tokens live in
`<DATA_DIR>/onedrive_token.json`, the client in
`onedrive_client.json`. Nothing here talks to a browser; `api/auth.py`
owns the consent round-trip and calls [save_token].

### Deliberately not msal

The official library would be a new dependency for two HTTP calls we
already have `requests` for, and its token cache is its own format on
disk. Matching the YouTube module's shape is worth more here than
matching Microsoft's samples.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from ..config import DATA_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"

# `offline_access` is what makes a refresh token come back; without it
# uploads stop working an hour after consent.
SCOPES = ["Files.ReadWrite", "offline_access", "User.Read"]

# Graph requires upload chunks be a multiple of 320 KiB. 10 MiB keeps
# HTTP overhead small while still moving the progress bar often enough
# to look alive on residential upstream.
CHUNK_ALIGN = 320 * 1024
DEFAULT_CHUNK_MB = 10


@dataclass
class UploadResult:
    item_id: str
    name: str
    # Anonymous view link, when one was requested and the tenant allows
    # it. None means the file uploaded fine but has no shareable URL -
    # worth surfacing, not worth failing over.
    share_url: Optional[str] = None
    web_url: Optional[str] = None


@dataclass
class Status:
    configured: bool
    reason: str
    has_client: bool
    has_token: bool
    # "personal" | "business" | "documentLibrary" | None. Custom
    # thumbnails and anonymous links both depend on this.
    drive_type: Optional[str] = None
    account: Optional[str] = None


def _config_dir() -> Path:
    return DATA_DIR if DATA_DIR is not None else PROJECT_ROOT


def client_path() -> Path:
    return _config_dir() / "onedrive_client.json"


def token_path() -> Path:
    return _config_dir() / "onedrive_token.json"


def read_client() -> Optional[dict]:
    """`{client_id, client_secret}` as saved by the settings UI."""
    path = client_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("client_id") else None


def save_client(client_id: str, client_secret: str) -> None:
    client_path().parent.mkdir(parents=True, exist_ok=True)
    client_path().write_text(
        json.dumps({"client_id": client_id, "client_secret": client_secret}, indent=2),
        encoding="utf-8",
    )


def save_token(payload: dict) -> None:
    """Persist a token response, stamping when the access token dies.

    Graph returns `expires_in` (seconds). Storing the absolute moment
    instead means a restart does not make a live token look fresh.
    """
    data = dict(payload)
    data["expires_at"] = time.time() + float(payload.get("expires_in", 3600)) - 60
    token_path().parent.mkdir(parents=True, exist_ok=True)
    token_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_token() -> Optional[dict]:
    path = token_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def access_token() -> str:
    """A usable access token, refreshing when the saved one has expired.

    Raises RuntimeError with something the operator can act on, which is
    the same contract `youtube._build_service` keeps.
    """
    tok = _read_token()
    if not tok:
        raise RuntimeError(
            "OneDrive is not connected. Connect it from the team settings page."
        )
    if tok.get("expires_at", 0) > time.time() and tok.get("access_token"):
        return tok["access_token"]

    refresh = tok.get("refresh_token")
    client = read_client()
    if not refresh or not client:
        raise RuntimeError(
            "The saved OneDrive token cannot be refreshed. Connect it again."
        )
    resp = requests.post(
        f"{AUTHORITY}/token",
        data={
            "client_id": client["client_id"],
            "client_secret": client.get("client_secret", ""),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
            "scope": " ".join(SCOPES),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"OneDrive refresh failed ({resp.status_code}): {resp.text[:300]}. "
            "Connect it again from the team settings page."
        )
    payload = resp.json()
    # Microsoft rotates refresh tokens; keep the old one if a response
    # omits it, or the next refresh has nothing to present.
    payload.setdefault("refresh_token", refresh)
    save_token(payload)
    return payload["access_token"]


def get_status() -> Status:
    """Cheap enough for the settings page to poll; one Graph call at most."""
    client = read_client()
    if client is None:
        return Status(False, "No Microsoft app is configured.", False, False)
    if _read_token() is None:
        return Status(False, "Not connected yet.", True, False)
    try:
        token = access_token()
        r = requests.get(
            f"{GRAPH}/me/drive",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if r.status_code != 200:
            return Status(False, f"Graph said {r.status_code}.", True, True)
        drive = r.json()
        owner = (drive.get("owner") or {}).get("user") or {}
        return Status(
            True,
            "Ready.",
            True,
            True,
            drive_type=drive.get("driveType"),
            account=owner.get("displayName") or owner.get("email"),
        )
    except RuntimeError as exc:
        return Status(False, str(exc), True, True)
    except requests.RequestException as exc:
        return Status(False, f"Could not reach Graph: {exc}", True, True)


def upload_file(
    *,
    file_path: Path,
    remote_path: str,
    share: bool = True,
    progress_cb: Optional[Callable[[float, int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    chunk_size_mb: int = DEFAULT_CHUNK_MB,
) -> UploadResult:
    """Upload `file_path` to `remote_path` on the connected drive.

    `remote_path` is relative to the drive root, e.g.
    `VME/Eastlake/2026-09-20/08_Kate_G.mp4`. Intermediate folders are
    created by Graph as part of the upload session, so there is no
    mkdir dance.

    The signature mirrors `youtube.upload_video` on purpose - same
    progress and cancel hooks - so the render job can route to either
    without special-casing.
    """
    token = access_token()
    size = file_path.stat().st_size
    headers = {"Authorization": f"Bearer {token}"}

    # A session rather than a simple PUT: simple uploads cap at 250 MB,
    # and a match render is well past that. It also gives us resumable
    # byte ranges, which is what makes cancel cheap.
    session = requests.post(
        f"{GRAPH}/me/drive/root:/{remote_path}:/createUploadSession",
        headers=headers,
        json={
            "item": {
                "@microsoft.graph.conflictBehavior": "replace",
                "name": file_path.name,
            }
        },
        timeout=30,
    )
    if session.status_code not in (200, 201):
        raise RuntimeError(
            f"Could not start the OneDrive upload ({session.status_code}): "
            f"{session.text[:300]}"
        )
    upload_url = session.json()["uploadUrl"]

    chunk = max(1, chunk_size_mb) * 1024 * 1024
    chunk -= chunk % CHUNK_ALIGN  # Graph rejects unaligned chunks.
    chunk = max(chunk, CHUNK_ALIGN)

    item: dict = {}
    sent = 0
    with file_path.open("rb") as fh:
        while sent < size:
            if cancel_check and cancel_check():
                # Dropping the session tells OneDrive to bin the partial
                # file rather than leave it visible in the folder.
                requests.delete(upload_url, timeout=15)
                raise RuntimeError("Upload cancelled.")
            block = fh.read(chunk)
            if not block:
                break
            last = sent + len(block) - 1
            r = requests.put(
                upload_url,
                headers={
                    "Content-Length": str(len(block)),
                    "Content-Range": f"bytes {sent}-{last}/{size}",
                },
                data=block,
                timeout=300,
            )
            # 202 = chunk accepted, more wanted. 200/201 = finished.
            if r.status_code not in (200, 201, 202):
                requests.delete(upload_url, timeout=15)
                raise RuntimeError(
                    f"OneDrive rejected a chunk ({r.status_code}): {r.text[:300]}"
                )
            sent = last + 1
            if r.status_code in (200, 201):
                item = r.json()
            if progress_cb:
                progress_cb(sent * 100.0 / size if size else 100.0, sent, size)

    if not item:
        raise RuntimeError("OneDrive finished the upload without returning the file.")

    result = UploadResult(
        item_id=item.get("id", ""),
        name=item.get("name", file_path.name),
        web_url=item.get("webUrl"),
    )
    if share and result.item_id:
        result.share_url = create_share_link(result.item_id)
    return result


def create_share_link(item_id: str, scope: str = "anonymous") -> Optional[str]:
    """An unauthenticated view link, or None when the tenant forbids one.

    Business and SharePoint tenants routinely disable anonymous sharing
    by policy. That is a configuration fact rather than an error, so it
    returns None and lets the caller decide - failing the upload over it
    would throw away a file that uploaded perfectly well.
    """
    try:
        token = access_token()
        r = requests.post(
            f"{GRAPH}/me/drive/items/{item_id}/createLink",
            headers={"Authorization": f"Bearer {token}"},
            json={"type": "view", "scope": scope},
            timeout=30,
        )
        if r.status_code not in (200, 201):
            logger.warning(
                "createLink(%s) failed: %s %s", scope, r.status_code, r.text[:200]
            )
            return None
        return ((r.json() or {}).get("link") or {}).get("webUrl")
    except (RuntimeError, requests.RequestException) as exc:
        logger.warning("createLink failed: %s", exc)
        return None


def set_thumbnail(item_id: str, image: Path, size: str = "medium") -> bool:
    """Attach a custom thumbnail. OneDrive **Personal** only.

    Graph documents custom thumbnails as unsupported on OneDrive for
    Business and SharePoint, so this returns False there rather than
    pretending. The durable answer on any drive is to burn a title card
    onto the front of the video, which is what `render/thumbnail.py`
    already draws.
    """
    try:
        token = access_token()
        r = requests.put(
            f"{GRAPH}/me/drive/items/{item_id}/thumbnails/0/{size}/content",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "image/jpeg",
            },
            data=image.read_bytes(),
            timeout=60,
        )
        if r.status_code not in (200, 201, 204):
            logger.info(
                "custom thumbnail refused (%s) - expected on Business/SharePoint",
                r.status_code,
            )
            return False
        return True
    except (RuntimeError, OSError, requests.RequestException) as exc:
        logger.warning("set_thumbnail failed: %s", exc)
        return False
