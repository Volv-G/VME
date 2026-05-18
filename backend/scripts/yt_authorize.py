"""One-time YouTube OAuth authorization.

Usage:
    python -m scripts.yt_authorize [PATH_TO_CLIENT_SECRET_JSON]

With no argument the script expects the client-secret JSON to already
live at `<DATA_DIR>/youtube_client_secret.json`. With a path argument
the file is copied there for you first - useful right after a fresh
download from Google Cloud Console where the file usually lands in
`~/Downloads/client_secret_<random>.apps.googleusercontent.com.json`.

The copy (not move) is intentional: the original is left in place so
you can re-import it if you need to redo the setup.

What this script does:
  1. Resolves the client-secret JSON path (argument or default).
  2. Copies it to `<DATA_DIR>/youtube_client_secret.json` if needed.
  3. Opens your default browser to Google's consent screen.
  4. Saves the resulting access + refresh tokens to
     `<DATA_DIR>/youtube_token.json`.

After this finishes the backend can upload videos without ever
re-prompting (the refresh token has no expiry as long as the OAuth
client stays in "Testing" mode with you as a Test User, OR is fully
verified). If you ever rotate the client secret or revoke the token
from your Google account, just re-run the script.

Prerequisites in Google Cloud Console:
  - Create a project (or pick an existing one).
  - Enable the "YouTube Data API v3".
  - On the "OAuth consent screen" page: set User type=External, fill
    in the bare minimum metadata, add yourself as a Test User. (You
    do NOT need to publish or submit for verification - test users
    can use the app indefinitely.)
  - On "Credentials": create an OAuth 2.0 Client ID, application
    type "Desktop app". Download the JSON; either move it to the
    data dir manually or pass its path to this script.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Make `app.*` importable when invoked as a script from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.upload.youtube import (  # noqa: E402
    SCOPES,
    client_secrets_path,
    token_path,
)


def _resolve_secret(arg_path: str | None) -> Path | None:
    """Pick the client-secret JSON to use, copying into the data dir
    if a custom path was provided.

    Returns the path inside the data dir on success, or None when the
    user asked for a file that doesn't exist (the caller prints an
    error and exits in that case).
    """
    dest = client_secrets_path()
    if arg_path is None:
        # Default flow: file must already be at the expected location.
        return dest if dest.is_file() else None

    src = Path(arg_path).expanduser().resolve()
    if not src.is_file():
        return None
    # If the user passed the same file that's already at the target,
    # skip the copy (avoid the SameFileError on Windows).
    if src == dest.resolve():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    print(f"Copied {src}")
    print(f"     -> {dest}")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="yt_authorize",
        description=(
            "Run the one-time YouTube OAuth flow and save the refresh "
            "token to the VME data dir."
        ),
    )
    parser.add_argument(
        "client_secret",
        nargs="?",
        default=None,
        help=(
            "Path to the OAuth client-secret JSON downloaded from "
            "Google Cloud Console. Optional; if omitted, the script "
            "expects the file at <DATA_DIR>/youtube_client_secret.json."
        ),
    )
    args = parser.parse_args()

    cs = _resolve_secret(args.client_secret)
    if cs is None:
        if args.client_secret is None:
            print(
                f"ERROR: missing client secret file at {client_secrets_path()}.\n"
                "Either move the JSON there manually or run this script with "
                "the path as an argument, e.g.:\n"
                "    python -m scripts.yt_authorize "
                "~/Downloads/client_secret_xxx.json",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: file not found: {args.client_secret}",
                file=sys.stderr,
            )
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        print(
            f"ERROR: google-auth-oauthlib is not installed ({exc}).\n"
            "Run `uv pip install -e .` (or install the google-* deps "
            "directly) from backend/.",
            file=sys.stderr,
        )
        return 2

    # `run_local_server(port=0)` picks a free port and spins a tiny HTTP
    # server to capture the redirect from Google. Works on a desktop;
    # on a headless box you'd need to SSH-tunnel that port.
    flow = InstalledAppFlow.from_client_secrets_file(str(cs), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    # `prompt="consent"` forces Google to issue a refresh_token even on
    # repeat authorizations; without it, second-time runs can get an
    # access-only response that breaks long-term unattended use.

    out = token_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved YouTube credentials to {out}")
    print("You can now upload videos from the VME UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
