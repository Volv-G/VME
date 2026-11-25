"""Web copies of the reels for the Courtside VB site.

OneDrive won't play a personal file inside another site, and the reels
themselves (1080p HEVC at ~10 Mbps) don't play in most browsers anyway.
So for every reel the site lists, VME makes a small 720p H.264 copy and
uploads it to the site's R2 bucket (`courtside/server/videos.ts`); the
site plays it in place, to the same people who may see the reel.

A copy's key is `reels/<team>/<number>/<hash>.mp4`, the hash taken from
the reel's path, size and time - a re-rendered reel gets a new copy, and
the old one is pruned at the next publish. Nothing is kept on disk: a
copy is made in the temp folder, uploaded, deleted.

The free R2 tier holds 10 GB. Uploads stop at STORAGE_LIMIT; reels past it
simply keep their OneDrive link.
"""
from __future__ import annotations

import hashlib
import logging
import queue
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from ..library import paths
from ..library.ffmpeg_tools import ffmpeg_path

logger = logging.getLogger(__name__)

PREFIX = "reels"
HEIGHT = 720
STORAGE_LIMIT = int(9.5 * 1024**3)
MAX_COPY = 95 * 1024 * 1024  # the site's request size limit


def key_for(team: str, number: int, reel: Path) -> str:
    st = reel.stat()
    try:
        rel = reel.relative_to(paths.team_dir(team)).as_posix()
    except ValueError:
        rel = reel.name
    digest = hashlib.sha1(f"{rel}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:20]
    return f"{PREFIX}/{team}/{number}/{digest}.mp4"


def _request(cfg: dict, method: str, path: str, **kw) -> requests.Response:
    return requests.request(
        method,
        cfg["url"].rstrip("/") + path,
        headers={"Authorization": f"Bearer {cfg['token']}", **kw.pop("headers", {})},
        timeout=kw.pop("timeout", 60),
        **kw,
    )


def stored(cfg: dict, prefix: str) -> Optional[dict[str, int]]:
    """key -> size of the copies under `prefix`; None when the site has no
    video storage (or can't be reached), so nothing is attached or pruned."""
    try:
        r = _request(cfg, "GET", "/api/publish/videos", params={"prefix": prefix})
    except requests.RequestException as exc:
        logger.warning("site videos: listing failed: %s", exc)
        return None
    if r.status_code != 200:
        logger.info("site videos: not available (%s %s)", r.status_code, r.text[:120])
        return None
    return {v["key"]: int(v["size"]) for v in r.json().get("videos", [])}


@dataclass
class TeamCopies:
    """One team's copies during a publish: which exist (to put in the
    docs), which are wanted (to upload), which are stale (to prune)."""

    team: str
    cfg: dict
    have: Optional[dict[str, int]] = None
    wanted: dict[str, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.have = stored(self.cfg, f"{PREFIX}/{self.team}/")

    def attach(self, video: dict, number: Optional[int], reel: Path) -> None:
        if self.have is None or number is None or not reel.is_file():
            return
        key = key_for(self.team, number, reel)
        self.wanted[key] = reel
        if key in self.have:
            video["web"] = key

    def missing(self) -> list[tuple[str, Path]]:
        if self.have is None:
            return []
        items = [(k, p) for k, p in self.wanted.items() if k not in self.have]
        # Newest reels first: the match people are waiting for.
        return sorted(items, key=lambda kp: kp[1].stat().st_mtime, reverse=True)

    def prune(self) -> int:
        """Drop copies no reel wants any more (re-rendered or deleted)."""
        if self.have is None:
            return 0
        stale = [k for k in self.have if k not in self.wanted]
        for k in stale:
            r = _request(self.cfg, "DELETE", f"/api/publish/video/{k}")
            if r.status_code != 200:
                logger.warning("site videos: could not delete %s: %s", k, r.status_code)
        if stale:
            logger.info("site videos: pruned %d stale copies of %s", len(stale), self.team)
        return len(stale)


def transcode(src: Path, dst: Path) -> None:
    """720p H.264 + AAC, fast-start so playback begins before the download
    ends. NVENC when the GPU has it, else x264."""
    common_in = ["-hide_banner", "-loglevel", "error", "-y"]
    video_filter = ["-vf", f"scale=-2:'min({HEIGHT},ih)'"]
    tail = ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart", str(dst)]
    encoders = [
        ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "28", "-b:v", "1500k", "-maxrate", "2200k",
         "-bufsize", "4400k", "-profile:v", "high"],
        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "2200k", "-bufsize", "4400k",
         "-profile:v", "high"],
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    last = ""
    for enc in encoders:
        cmd = [ffmpeg_path(), *common_in, "-i", str(src), *video_filter, *enc, *tail]
        p = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
        if p.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            return
        last = (p.stderr or "").strip()[-300:]
    raise RuntimeError(f"ffmpeg could not make a web copy of {src.name}: {last}")


def upload_one(cfg: dict, key: str, reel: Path, used: list[int]) -> bool:
    """Make and upload one copy. `used` is the bucket's total bytes, kept
    current across a run so the storage limit holds."""
    if used[0] >= STORAGE_LIMIT:
        return False
    with tempfile.TemporaryDirectory(prefix="vme-web-") as tmp:
        out = Path(tmp) / "copy.mp4"
        transcode(reel, out)
        size = out.stat().st_size
        if size > MAX_COPY:
            logger.warning("site videos: copy of %s is %d MB, too big to upload", reel.name, size // 2**20)
            return False
        if used[0] + size > STORAGE_LIMIT:
            logger.warning("site videos: storage limit reached (%.1f GB); %s keeps its OneDrive link",
                           used[0] / 1024**3, reel.name)
            return False
        with out.open("rb") as f:
            r = _request(cfg, "PUT", f"/api/publish/video/{key}", data=f,
                         headers={"Content-Type": "video/mp4", "Content-Length": str(size)}, timeout=600)
        if r.status_code != 200:
            raise RuntimeError(f"site refused {key}: {r.status_code} {r.text[:200]}")
        used[0] += size
        logger.info("site videos: uploaded %s (%.1f MB) for %s", key, size / 2**20, reel.name)
        return True


def total_stored(cfg: dict) -> Optional[int]:
    have = stored(cfg, f"{PREFIX}/")
    return None if have is None else sum(have.values())


def upload_all(cfg: dict, items: list[tuple[str, Path]], on_progress=None) -> int:
    """Upload `items` in order, stopping at the storage limit. Returns how
    many were uploaded."""
    used = [total_stored(cfg) or 0]
    done = 0
    for i, (key, reel) in enumerate(items):
        if used[0] >= STORAGE_LIMIT:
            logger.warning("site videos: storage limit reached; %d copies not made", len(items) - i)
            break
        try:
            if upload_one(cfg, key, reel, used):
                done += 1
        except Exception:
            logger.warning("site videos: %s failed", reel, exc_info=True)
        if on_progress:
            on_progress(i + 1, len(items), key)
    return done


class Uploader:
    """Background queue: a publish hands over what's missing; once the
    queue drains, the site is republished so the new copies show."""

    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[dict, str, Path]]" = queue.Queue()
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def enqueue(self, cfg: dict, items: list[tuple[str, Path]]) -> None:
        with self._lock:
            fresh = [(k, p) for k, p in items if k not in self._queued]
            for k, p in fresh:
                self._queued.add(k)
                self._q.put((cfg, k, p))
            if fresh and self._thread is None:
                self._thread = threading.Thread(target=self._run, name="site-videos", daemon=True)
                self._thread.start()
        if fresh:
            logger.info("site videos: %d copies queued", len(fresh))

    def _run(self) -> None:
        from . import site

        uploaded = 0
        used: Optional[list[int]] = None
        while True:
            try:
                cfg, key, reel = self._q.get(timeout=5)
            except queue.Empty:
                with self._lock:
                    # Under the lock, so an enqueue now starts a new thread.
                    if self._q.empty():
                        self._thread = None
                        break
                continue
            try:
                if used is None:
                    used = [total_stored(cfg) or 0]
                if upload_one(cfg, key, reel, used):
                    uploaded += 1
            except Exception:
                logger.warning("site videos: %s failed", reel, exc_info=True)
            finally:
                with self._lock:
                    self._queued.discard(key)
        if uploaded:
            site.publish_soon(10.0)


UPLOADER = Uploader()
