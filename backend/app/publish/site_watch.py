"""Notice when anything the site is built from has changed, so the site
follows VME on its own.

Explicit triggers (a finished upload, a saved roster) publish promptly,
but there are many ways to change what families see - tagging events in
the editor changes scores and stats, a phone import adds a match, a
render's OneDrive or YouTube record appears, a file is fixed by hand -
and a missed trigger means a stale site until someone presses Publish.
So once a minute (publish/site_sync.py's loop) this fingerprints the
media tree's metadata and asks for a publish when it differs.

What counts:
- every .json (match.json, rosters, tournament info, the YouTube and
  OneDrive records beside renders), and
- every picture (player photos, logos, thumbnails).

A match.json is compared by content without the editor's own bookkeeping
(where the playhead and the timeline view were left): opening a match or
scrolling through it rewrites the file, and that is no reason to
republish. Content is only re-read for files whose size or time changed,
so a round costs a directory walk - a few hundredths of a second.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

PICTURES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# Match fields that are the editor's view state, not anything published.
VIEW_STATE = ("playhead_frame", "timeline_zoom", "timeline_anchor_frame")


class TreeWatch:
    def __init__(self, root: Path) -> None:
        self.root = root
        # path -> (size, mtime_ns, digest)
        self._files: dict[str, tuple[int, int, str]] = {}
        self._last: Optional[str] = None

    def _digest(self, path: str, name: str) -> str:
        try:
            data = Path(path).read_bytes()
        except OSError:
            return "?"
        if name == "match.json":
            try:
                doc = json.loads(data)
                for key in VIEW_STATE:
                    doc.pop(key, None)
                data = json.dumps(doc, sort_keys=True).encode()
            except ValueError:
                pass
        return hashlib.sha1(data).hexdigest()

    def fingerprint(self) -> str:
        seen: dict[str, tuple[int, int, str]] = {}
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext != ".json" and ext not in PICTURES:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                old = self._files.get(path)
                if old and old[0] == st.st_size and old[1] == st.st_mtime_ns:
                    seen[path] = old
                elif ext == ".json":
                    seen[path] = (st.st_size, st.st_mtime_ns, self._digest(path, name))
                else:
                    seen[path] = (st.st_size, st.st_mtime_ns, f"{st.st_size}:{st.st_mtime_ns}")
        self._files = seen
        h = hashlib.sha1()
        for path in sorted(seen):
            h.update(path.encode())
            h.update(seen[path][2].encode())
        return h.hexdigest()

    def changed(self) -> bool:
        """True when the tree differs from the last call (and on the first,
        which catches up with anything done while VME was off)."""
        now = self.fingerprint()
        changed = now != self._last
        self._last = now
        return changed
