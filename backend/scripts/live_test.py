"""Exercise YouTube live-broadcast creation from the command line.

This is the proving ground for `app/upload/live.py` before any of it is
wired to a button. It does the whole thing end to end: get a token
(asking for consent if there isn't a usable one), find the ingest stream
the phone already points at, create a broadcast with a real title and
description, and bind them together.

    cd backend
    .venv\\Scripts\\python -E -m scripts.live_test streams
    .venv\\Scripts\\python -E -m scripts.live_test create --title "Test"
    .venv\\Scripts\\python -E -m scripts.live_test status <id>
    .venv\\Scripts\\python -E -m scripts.live_test delete <id>

Token acquisition is *on demand*
--------------------------------
If the saved token is missing, or its refresh fails - which happens by
design every 7 days while the OAuth consent screen sits in "Testing"
publishing status - this opens a browser and asks for consent, then
saves the result to the same file the rest of VME uses. No separate
credential, no copy.

What it costs
-------------
`streams` is 1 unit. `create` is 100 (insert 50 + bind 50), plus 50 if a
thumbnail is attached, plus 1 to read back. Against a 10,000/day pool
that is noise - a single `videos.insert` upload is 1600.

Nothing here is destructive except `delete`, and a created broadcast is
`private` unless told otherwise, so a test cannot notify subscribers or
appear on the channel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `app.*` importable when invoked as a script from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.upload import live, quota  # noqa: E402
from app.upload import youtube as yt  # noqa: E402


# ---- credentials ----------------------------------------------------------


def ensure_token(*, force: bool = False) -> bool:
    """Make sure a usable token exists; run the consent flow if not.

    Returns True if consent was performed. The distinction matters for
    the log: "it just worked" and "you had to re-authorize" are
    different findings when the question is whether this is practical to
    run before a match.
    """
    if not force:
        try:
            yt._build_service()
            return False
        except RuntimeError as exc:
            print(f"! existing credentials unusable: {exc}\n")

    cs = yt.client_secrets_path()
    if not cs.is_file():
        raise SystemExit(
            f"ERROR: no OAuth client secret at {cs}.\n"
            "Download one (Desktop app) from the Google Cloud console, or run\n"
            "    python -m scripts.yt_authorize <path-to-client_secret.json>"
        )

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("Opening a browser for YouTube consent...")
    flow = InstalledAppFlow.from_client_secrets_file(str(cs), yt.SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    out = yt.token_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved credentials to {out}\n")
    return True


# ---- helpers --------------------------------------------------------------


def show_stream(s: live.IngestStream, *, indent: str = "  ") -> None:
    print(
        f"{indent}{s.id}  key ...{s.key_tail}  "
        f"[{s.stream_status}]{'' if s.is_reusable else ' (single-use)'}  {s.title}"
    )


def show_broadcast(b: live.Broadcast) -> None:
    print(f"  id          {b.id}")
    print(f"  title       {b.title}")
    if b.description:
        first = b.description.splitlines()[0]
        print(f"  description {first[:70]}")
    print(f"  privacy     {b.privacy_status}")
    print(f"  status      {b.life_cycle_status}")
    print(f"  bound to    {b.bound_stream_id or '(nothing)'}")
    print(f"  watch       {b.watch_url}")


# ---- commands -------------------------------------------------------------


def cmd_streams(args) -> int:
    ensure_token(force=args.reauth)
    streams = live.list_ingest_streams()
    if not streams:
        print("No reusable stream keys on this channel.")
        return 1
    print(f"{len(streams)} ingest stream(s):")
    for s in streams:
        show_stream(s)
    print(
        "\nKeys are shown by their last 4 characters only. Compare against the "
        "tail the app logs at startup to confirm the phone points here."
    )
    return 0


def cmd_create(args) -> int:
    ensure_token(force=args.reauth)

    stream = live.find_stream(stream_id=args.stream_id, key_tail=args.key_tail)
    print("Binding to ingest stream:")
    show_stream(stream)

    print("\nCreating broadcast...")
    b = live.create_broadcast(
        title=args.title,
        description=args.description,
        privacy_status=args.privacy,
        made_for_kids=args.made_for_kids,
    )
    print(f"  created {b.id}")

    b = live.bind_broadcast(b.id, stream.id)
    print(f"  bound to {b.bound_stream_id}")

    if args.thumbnail:
        path = Path(args.thumbnail).expanduser()
        try:
            live.set_broadcast_thumbnail(b.id, path)
            print(f"  thumbnail set from {path.name}")
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            print(f"  ! thumbnail refused: {exc}")

    print()
    show_broadcast(b)
    print(
        f"\nPush to {live.INGEST_PRIMARY}/<the key ending ...{stream.key_tail}>"
        "\nThe broadcast goes live by itself when bytes arrive."
    )
    print(f"\nquota: {quota.summary()}" if hasattr(quota, "summary") else "")
    return 0


def cmd_status(args) -> int:
    ensure_token(force=args.reauth)
    b = live.get_broadcast(args.id)
    if b is None:
        print(f"No broadcast {args.id} on this channel (deleted?).")
        return 1
    show_broadcast(b)
    return 0


def cmd_delete(args) -> int:
    ensure_token(force=args.reauth)
    live.delete_broadcast(args.id)
    print(f"Deleted broadcast {args.id}.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="live_test", description=__doc__)
    p.add_argument(
        "--reauth",
        action="store_true",
        help="Force the consent flow even if the saved token looks fine.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("streams", help="List ingest streams (stream keys).").set_defaults(
        func=cmd_streams
    )

    c = sub.add_parser("create", help="Create a broadcast and bind it to a stream.")
    c.add_argument("--title", required=True)
    c.add_argument("--description", default="")
    c.add_argument(
        "--privacy",
        default="private",
        choices=list(live.VALID_PRIVACY),
        help="Default private, so a test cannot reach subscribers.",
    )
    c.add_argument("--stream-id", default=None)
    c.add_argument(
        "--key-tail",
        default=None,
        help="Last 4 characters of the stream key the phone uses.",
    )
    c.add_argument("--thumbnail", default=None, help="JPEG/PNG under 2 MB.")
    c.add_argument(
        "--made-for-kids",
        action="store_true",
        help="Declare the content made for kids. High-school sport is not.",
    )
    c.set_defaults(func=cmd_create)

    s = sub.add_parser("status", help="Read one broadcast back.")
    s.add_argument("id")
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("delete", help="Delete a broadcast.")
    d.add_argument("id")
    d.set_defaults(func=cmd_delete)

    args = p.parse_args()
    try:
        return args.func(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI: message, not traceback
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
