"""Reconcile local thumbnails with what YouTube is actually serving.

The `.youtube.json` sidecars only started recording `thumbnail_synced`
recently, so every render uploaded before that reads as "not synced" and
the UI flags it - even when YouTube took the thumbnail fine. This script
answers the question for real, then writes the answer back.

It does NOT use the API (so it costs no quota and needs no auth): it
downloads the image YouTube publishes on its CDN and compares it with
the local file. A custom thumbnail comes back re-encoded, so the bytes
never match - the comparison is perceptual (downscale to 32x32 luma,
compare RMS), which cleanly separates "our image, recompressed" from
"an auto-generated frame grab".

    python -m scripts.verify_thumbnails                  # every team
    python -m scripts.verify_thumbnails Eastlake_JV      # one team
    python -m scripts.verify_thumbnails --dry-run

Run it with VME_MEDIA_ROOT pointing at the media tree you mean to touch.
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from app.library import paths, scanner  # noqa: E402
from app.render.thumbnail import thumbnail_path  # noqa: E402

# RMS distance (0-255 scale) below which we call it the same image.
# Our thumbnails are flat color blocks and big text, which survive
# re-encoding almost exactly; a frame grab from the same match scores
# far higher because the wedges and captions simply aren't there.
MATCH_RMS = 18.0
FINGERPRINT = 32


def _fingerprint(img: Image.Image) -> list[float]:
    small = img.convert("L").resize((FINGERPRINT, FINGERPRINT), Image.BOX)
    return list(small.getdata())


def _rms(a: list[float], b: list[float]) -> float:
    n = len(a)
    return (sum((x - y) ** 2 for x, y in zip(a, b)) / n) ** 0.5


def _fetch(video_id: str) -> Image.Image | None:
    """The image YouTube publishes for a video, best size available."""
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        url = f"https://i.ytimg.com/vi/{video_id}/{name}.jpg"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return Image.open(io.BytesIO(resp.read()))
        except Exception:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("team", nargs="?", help="Only this team (default: all)")
    ap.add_argument(
        "--dry-run", action="store_true", help="Report without writing sidecars"
    )
    args = ap.parse_args()

    teams = (
        [args.team]
        if args.team
        else [t.name for t in scanner.list_teams()]
    )

    checked = matched = 0
    for team in teams:
        for info in scanner.list_team_full_renders(team):
            if not info.youtube_video_id:
                continue
            render = (
                paths.renders_dir(
                    info.team, info.tournament, info.date, info.match
                )
                / info.filename
            )
            local_path = thumbnail_path(render)
            if not local_path.is_file():
                continue
            checked += 1
            remote = _fetch(info.youtube_video_id)
            if remote is None:
                print(f"  ?? {info.filename}: could not fetch from YouTube")
                continue
            score = _rms(
                _fingerprint(Image.open(local_path)), _fingerprint(remote)
            )
            same = score <= MATCH_RMS
            matched += same
            mark = "OK " if same else "-- "
            print(f"  {mark}{info.filename}  (rms {score:5.1f})")
            if same and not args.dry_run and not info.thumbnail_synced:
                scanner.mark_thumbnail_synced(
                    render, True, scanner.file_digest(local_path)
                )
    print(
        f"\n{matched}/{checked} thumbnails confirmed live on YouTube"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
