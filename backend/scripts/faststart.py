"""Move the moov atom to the front of already-rendered mp4/mov files.

Renders produced before the preset gained `"Optimize": true` have their
index (`moov`) written *after* the media data, so a player can't start
until it has probed to the end of a multi-gigabyte file and pulled the
index back. Over a remote link that is seconds of blank spinner, and
with a flaky connection it can fail outright.

This fixes existing files without re-encoding: ffmpeg remuxes with
`-c copy -movflags +faststart`, which rewrites the container only. Cost
is one read + one write of the file; quality is bit-identical.

Usage (from `backend/`, with the venv python):

    python -m scripts.faststart                 # dry run, whole library
    python -m scripts.faststart --apply
    python -m scripts.faststart --team Eastlake_JV --apply

Safe to re-run: files whose moov is already first are skipped.

The rewrite goes to a `.faststart.tmp` sibling and is only swapped in
after ffmpeg exits 0 and the result parses with moov first, so an
interrupted run can never leave a truncated render in place. Timestamps
are preserved (`copystat`) because the library sorts renders by mtime.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

# Import as a package module so the app's config (VME_MEDIA_ROOT etc.)
# resolves exactly as it does for the service.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MEDIA_ROOT, VIDEO_EXTENSIONS  # noqa: E402
from app.library import paths  # noqa: E402

REMUXABLE = {".mp4", ".mov", ".m4v"}


def top_level_atoms(path: Path, limit: int = 12) -> list[tuple[str, int]]:
    """`[(type, size), ...]` for the first few top-level atoms."""
    out: list[tuple[str, int]] = []
    try:
        with path.open("rb") as fh:
            offset = 0
            for _ in range(limit):
                fh.seek(offset)
                head = fh.read(8)
                if len(head) < 8:
                    break
                size = struct.unpack(">I", head[:4])[0]
                atom = head[4:8].decode("latin1", "replace")
                if size == 1:  # 64-bit extended size
                    ext = fh.read(8)
                    if len(ext) < 8:
                        break
                    size = struct.unpack(">Q", ext)[0]
                out.append((atom, size))
                if size <= 0:
                    break
                offset += size
    except OSError:
        return []
    return out


def needs_faststart(path: Path) -> bool:
    """True when `moov` appears after `mdat` (or not in the first atoms).

    `ftyp` and `free` may precede either; what matters is that the index
    is reachable before the media data.
    """
    for atom, _size in top_level_atoms(path):
        if atom == "moov":
            return False
        if atom == "mdat":
            return True
    return False  # not an mp4 we understand - leave it alone


def remux(src: Path) -> tuple[bool, str]:
    """Rewrite `src` in place with the index first. Returns (ok, message)."""
    # Keep the real extension LAST: ffmpeg picks the output muxer from
    # it, and a `.tmp` tail makes it give up with "unable to choose an
    # output format".
    tmp = src.with_name(src.name + ".faststart" + src.suffix)
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        "-c",
        "copy",
        "-map",
        "0",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "ffmpeg not found on PATH"
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        return False, f"ffmpeg failed: {proc.stderr.strip()[:200]}"
    if needs_faststart(tmp):
        tmp.unlink(missing_ok=True)
        return False, "remux produced a file with moov still last"
    try:
        shutil.copystat(src, tmp)
        os.replace(tmp, src)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return False, f"could not replace the original: {exc}"
    return True, "ok"


def iter_renders(team: str | None):
    """Every render file under the media root, optionally one team."""
    root = MEDIA_ROOT
    teams = [root / team] if team else sorted(p for p in root.iterdir() if p.is_dir())
    for team_dir in teams:
        if not team_dir.is_dir():
            continue
        for render_dir in sorted(team_dir.rglob("renders")):
            if not render_dir.is_dir():
                continue
            for f in sorted(render_dir.rglob("*")):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                if paths.is_render_scratch(f.name):
                    continue
                # Leftover from an interrupted run of this script: it has
                # to keep a video extension for ffmpeg's sake, so skip it
                # by name or we'd "fix" our own scratch file.
                if ".faststart." in f.name:
                    continue
                yield f


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--team", help="Limit to one team folder")
    ap.add_argument(
        "--apply", action="store_true", help="Actually rewrite (default: dry run)"
    )
    args = ap.parse_args()

    checked = fixed = skipped = failed = 0
    for f in iter_renders(args.team):
        checked += 1
        if f.suffix.lower() not in REMUXABLE or not needs_faststart(f):
            skipped += 1
            continue
        size_gb = f.stat().st_size / (1024**3)
        rel = f.relative_to(MEDIA_ROOT)
        if not args.apply:
            print(f"WOULD FIX  {size_gb:6.2f} GB  {rel}")
            fixed += 1
            continue
        print(f"fixing     {size_gb:6.2f} GB  {rel} ... ", end="", flush=True)
        ok, msg = remux(f)
        print(msg)
        if ok:
            fixed += 1
        else:
            failed += 1

    verb = "would fix" if not args.apply else "fixed"
    print(
        f"\n{checked} render(s) checked, {verb} {fixed}, "
        f"{skipped} already fine, {failed} failed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
