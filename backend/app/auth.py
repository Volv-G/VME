"""HTTP Basic Auth for the LAN deployment.

Credentials are read from environment variables:

    VME_USERNAME        single username
    VME_PASSWORD_HASH   pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
    VME_AUTH_REQUIRED   "1" to fail-closed if credentials are missing or invalid

If VME_USERNAME / VME_PASSWORD_HASH are not set, the middleware is disabled and
a warning is logged - useful for local development.

Generate VME_PASSWORD_HASH with:

    python -m app.auth hash <password>
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import sys
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

PBKDF2_ALGO = "pbkdf2_sha256"
PBKDF2_DEFAULT_ITERATIONS = 200_000


def hash_password(password: str, iterations: int = PBKDF2_DEFAULT_ITERATIONS) -> str:
    if not password:
        raise ValueError("password must be non-empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PBKDF2_ALGO}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters_s, salt_b64, hash_b64 = encoded.split("$")
        if algo != PBKDF2_ALGO:
            return False
        iterations = int(iters_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, binascii.Error):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated requests outside of `public_paths`."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        username: str | None,
        password_hash: str | None,
        public_paths: Iterable[str] = (),
        realm: str = "VME",
    ) -> None:
        super().__init__(app)
        self.username = username or None
        self.password_hash = password_hash or None
        self.public_paths = set(public_paths)
        self.realm = realm
        self.enabled = bool(self.username and self.password_hash)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not self.enabled:
            return await call_next(request)
        # Always allow CORS preflight + explicitly public endpoints.
        if request.method == "OPTIONS" or request.url.path in self.public_paths:
            return await call_next(request)

        if self._authorized(request):
            return await call_next(request)

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{self.realm}", charset="UTF-8"'},
            content="Authentication required",
            media_type="text/plain",
        )

    def _authorized(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            raw = base64.b64decode(header.split(" ", 1)[1].strip()).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, IndexError):
            return False
        user, _, pw = raw.partition(":")
        # Constant-time username compare to avoid leaking the username via timing.
        username_ok = hmac.compare_digest(
            user.encode("utf-8"), (self.username or "").encode("utf-8")
        )
        password_ok = verify_password(pw, self.password_hash or "")
        return username_ok and password_ok


def configure_from_env() -> tuple[str | None, str | None, bool]:
    """Return (username, password_hash, auth_required). Validate consistency."""
    username = os.environ.get("VME_USERNAME") or None
    password_hash = os.environ.get("VME_PASSWORD_HASH") or None
    required = os.environ.get("VME_AUTH_REQUIRED", "").strip() == "1"

    if required and not (username and password_hash):
        sys.stderr.write(
            "[FATAL] VME_AUTH_REQUIRED=1 but VME_USERNAME/VME_PASSWORD_HASH are not set.\n"
            "        Run scripts/set-credentials.ps1 first.\n"
        )
        sys.exit(2)

    if username and not password_hash:
        sys.stderr.write(
            "[FATAL] VME_USERNAME is set but VME_PASSWORD_HASH is missing.\n"
        )
        sys.exit(2)

    if not username:
        logger.warning(
            "BasicAuth disabled: VME_USERNAME / VME_PASSWORD_HASH are not configured. "
            "This is fine for local development; do NOT expose this server to the LAN "
            "without credentials."
        )

    return username, password_hash, required


def _cli() -> None:
    """`python -m app.auth hash <password>` -> prints a VME_PASSWORD_HASH value."""
    if len(sys.argv) >= 3 and sys.argv[1] == "hash":
        print(hash_password(sys.argv[2]))
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "verify":
        ok = verify_password(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
        print("ok" if ok else "fail")
        return
    sys.stderr.write("usage: python -m app.auth hash <password>\n")
    sys.exit(2)


if __name__ == "__main__":
    _cli()
