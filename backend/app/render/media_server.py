"""Publish finished renders to a media-server library folder.

The renders tree is organised for editing:

    <team>/<tournament>/<date>/<NN_Opponent>/renders/full_20260906_190937.mp4

which tells Jellyfin nothing - it would show up as "full 20260906 190937"
inside a folder called "renders". A media server wants one flat,
self-describing name per video:

    2026.09.03.NC.M1.Liberty.mp4
    2026.09.03.NC.M1.Liberty-thumb.jpg      (landscape, Jellyfin "Thumb")
    2026.09.03.NC.M1.Liberty-poster.jpg     (2:3 portrait, "Primary")

So publishing is a copy plus a rename, driven by the same template
machinery the YouTube titles use (`app/upload/templates.py`), which
means an unnumbered match (index 0) drops `M<n>` here too.

Copying happens on the SERVER: a multi-gigabyte match must not travel
through the browser, and the destination is typically a share only the
server can reach.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..library import paths
from ..upload.templates import TemplateVars, render_template
from .renderer import RenderCancelled

logger = logging.getLogger(__name__)

# Copy in 8 MiB chunks: big enough that the syscall overhead disappears
# on a 4.5 GB file, small enough to report progress and notice a cancel
# request a few times per second even on a slow network share.
CHUNK_BYTES = 8 * 1024 * 1024


class MediaServerError(RuntimeError):
    """Configuration or destination problem, reported to the user."""


@dataclass
class CopyPlan:
    """What a publish would copy, resolved before any bytes move."""

    source: Path
    target: Path
    # Sidecar images: (source, target) pairs that exist right now.
    images: list[tuple[Path, Path]] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        total = self.source.stat().st_size
        for src, _ in self.images:
            try:
                total += src.stat().st_size
            except OSError:
                pass
        return total


def _safe_name(value: str) -> str:
    """Sanitize a rendered template into a single filename segment.

    Slashes are the important part: the template is a NAME, not a path,
    and a stray `/` from an opponent called "A/B" would otherwise write
    outside the configured folder.
    """
    cleaned = paths.safe_segment(value)
    return cleaned.strip(". ") or "render"


def target_basename(vars_: TemplateVars, template: str, *, player_label: str = "") -> str:
    """Base name (no extension) for the published copy of a render.

    A player reel gets the player appended to the match name rather than
    a template of its own: reels are the same match, and keeping the
    match name as the prefix means the whole day sorts together in a
    file listing.
    """
    base = _safe_name(render_template(template, vars_))
    if player_label:
        base = f"{base}.{_safe_name(player_label)}"
    return base


def plan_copy(
    source: Path,
    dest_dir: Path,
    basename: str,
) -> CopyPlan:
    """Resolve source/target paths for a render and its sidecar images."""
    if not source.is_file():
        raise MediaServerError(f"Render file not found: {source.name}")
    target = dest_dir / f"{basename}{source.suffix.lower()}"

    # Import here: `thumbnail` pulls in Pillow, and a machine that can
    # serve the API without rendering shouldn't need it to publish.
    from .thumbnail import jellyfin_paths, thumbnail_path

    images: list[tuple[Path, Path]] = []
    thumb, poster = jellyfin_paths(source)
    for src, suffix in ((thumb, "-thumb.jpg"), (poster, "-poster.jpg")):
        if src.is_file():
            images.append((src, dest_dir / f"{basename}{suffix}"))
    if not any(t.name.endswith("-thumb.jpg") for _, t in images):
        # Reels only get the YouTube thumbnail (`<file>.thumbnail.jpg`),
        # not the Jellyfin pair - it is the same 16:9 image, so use it.
        own = thumbnail_path(source)
        if own.is_file():
            images.append((own, dest_dir / f"{basename}-thumb.jpg"))
    return CopyPlan(source=source, target=target, images=images)


def is_up_to_date(plan: CopyPlan) -> bool:
    """Whether the destination already holds this exact render.

    Size plus mtime, the same test every file-sync tool starts with. A
    re-render always changes the mtime (and nearly always the size), so
    this only ever skips a genuine repeat - which matters when the
    destination is a NAS and the file is gigabytes.
    """
    try:
        src = plan.source.stat()
        dst = plan.target.stat()
    except OSError:
        return False
    return src.st_size == dst.st_size and int(dst.st_mtime) >= int(src.st_mtime)


def copy_file(
    src: Path,
    dst: Path,
    *,
    on_progress: Optional[Callable[[int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    base_done: int = 0,
) -> int:
    """Copy `src` to `dst` chunk-wise, reporting cumulative bytes done.

    Writes to a `.part` file and renames on completion, so an aborted
    copy can't leave a truncated video that a media server would happily
    index and play for three seconds.

    Returns the number of bytes copied.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    done = 0
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while True:
                if cancel_check is not None and cancel_check():
                    # Same exception the renderer raises, so the job
                    # dispatcher's existing cancellation handling (mark
                    # cancelled, don't log a failure) applies unchanged.
                    raise RenderCancelled("Copy cancelled by user")
                chunk = fin.read(CHUNK_BYTES)
                if not chunk:
                    break
                fout.write(chunk)
                done += len(chunk)
                if on_progress is not None:
                    on_progress(base_done + done)
        os.replace(tmp, dst)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # Carry the modification time across so `is_up_to_date` can tell a
    # published copy from a stale one.
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass
    return done


def resolve_dest_dir(configured: str) -> Path:
    """Validate the configured media-server folder and return it.

    Created if missing - a first publish into a brand-new library folder
    is normal. Anything else (a file in the way, no permission, an
    unreachable share) is reported as a configuration error, because
    that is what it is.
    """
    raw = (configured or "").strip()
    if not raw:
        raise MediaServerError(
            "No media server folder configured for this team "
            "(set it in the team settings)."
        )
    dest = Path(raw)
    if not dest.is_absolute():
        raise MediaServerError(
            f"Media server folder must be an absolute path: {raw!r}"
        )
    if dest.exists() and not dest.is_dir():
        raise MediaServerError(f"Media server path is not a folder: {raw!r}")
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MediaServerError(
            f"Cannot use media server folder {raw!r}: {exc}"
        ) from exc
    return dest
