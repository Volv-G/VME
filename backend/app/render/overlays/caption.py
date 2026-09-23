"""Lower-third captions naming each play in a player reel.

### Why a lower third and not a title card

A reel is 4-15 plays of roughly twelve seconds each. The convention in
volleyball *recruiting* videos is a full-screen slide before each skill,
but those videos are grouped by skill ("here is all the hitting"), and
the slide introduces a section. A VME reel is one player's match in
chronological order, so every slide would introduce a section of one -
two seconds of black per play, half a minute added to a three-minute
video, to say what a caption can say over footage the viewer is not
studying anyway. General highlight-editing guidance splits the same way:
title cards between sections, lower thirds during clips.

The cheap-to-build argument points the same way, which is a happy
accident rather than the reason: a caption is per-frame compositing,
which `scoreboard.py` and `message.py` already do. A title card is
synthetic frames that do not exist in the frame map, and every one of
them would shift the timestamps in `<reel>.chapters.txt` off the plays
they name.

### Placement

Bottom-left. Message popups occupy the bottom-right and the scoreboard
the top, so the three never argue about the same pixels.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

from .message import (
    TEXT_COLOR,
    _composite,
    _hex_to_rgba,
    _load_font,
    _measure_text,
)

# Every metric is a fraction of the video height, not a pixel count.
# The popup constants in `message.py` are fixed pixels sized for a
# corner notification, and borrowing them is what made this read as a
# footnote: a caption is the only thing on screen that says what the
# viewer is watching, so it is sized like a broadcast score bug.
FONT_SCALE = 0.062
PAD_X_SCALE = 0.022
PAD_Y_SCALE = 0.015
ACCENT_SCALE = 0.011

BG_COLOR: tuple[int, int, int, int] = (12, 12, 18, 232)

# The envelope, in seconds. Long enough to read a short label twice,
# short enough to be gone before the play it names.
FADE_SECONDS = 0.3
HOLD_SECONDS = 2.0

MARGIN_X_SCALE = 0.037
MARGIN_BOTTOM_SCALE = 0.055

# Distinct captions kept rendered. A reel has at most a couple of dozen
# and each is ~40 KB at 1080p, so this never evicts in practice - it is
# here so a pathological reel cannot grow the cache without bound.
BOX_CACHE_LIMIT = 64


@dataclass(frozen=True)
class ReelCaption:
    """One play's caption, positioned in OUTPUT frame coordinates.

    `out_start`/`out_end` come from `MatchRenderer.segment_offsets()`,
    which is the same list `build_timestamps` uses - so the caption and
    the line in `<reel>.chapters.txt` can never disagree about where a
    play begins.
    """

    out_start: int
    out_end: int
    # What the play is - "Dig + Kill". On its own: the reel is one
    # player's, so their name on every caption is the one fact the
    # viewer already has, and the match clock is in the index beside the
    # file for anyone who wants to find the rally in the full render.
    title: str
    # Team colour for the accent bar. Empty falls back to the box colour,
    # which just reads as a slightly wider box.
    accent: str = ""


class CaptionOverlayRenderer:
    """Draws the caption covering a given output frame, if any."""

    def __init__(self, captions: list[ReelCaption], fps: float) -> None:
        # Sorted and searched by start, so lookup does not walk the list
        # once per frame. Segments never overlap - `_merge_segments` in
        # `reels.py` guarantees it - so the last caption starting at or
        # before a frame is the only candidate.
        self._captions = sorted(captions, key=lambda c: c.out_start)
        self._starts = [c.out_start for c in self._captions]
        self._fps = fps if fps > 0 else 30.0
        self._box_cache: dict[tuple, Image.Image] = {}

    def apply(self, frame: np.ndarray, out_frame: int) -> np.ndarray:
        """Composite the caption for `out_frame` in place, if one is live."""
        found = self._caption_for(out_frame)
        if found is None:
            return frame
        caption, elapsed = found
        alpha = self._alpha(elapsed, caption)
        if alpha <= 0.0:
            return frame

        h, w = frame.shape[:2]
        box = self._build_box(caption, h)
        out = frame if frame.flags.writeable else frame.copy()
        if alpha < 1.0:
            # Scale the box's own alpha channel rather than blending the
            # result: the text and the background fade together, so the
            # letters never darken against a background that has already
            # gone.
            faded = box.copy()
            faded.putalpha(faded.getchannel("A").point(lambda v: int(v * alpha)))
            box = faded
        _composite(
            out,
            box,
            int(h * MARGIN_X_SCALE),
            h - int(h * MARGIN_BOTTOM_SCALE) - box.height,
        )
        return out

    # ---- timing ----------------------------------------------------

    def _caption_for(self, out_frame: int) -> Optional[tuple[ReelCaption, int]]:
        """The caption covering `out_frame`, with frames elapsed into it."""
        i = bisect_right(self._starts, out_frame) - 1
        if i < 0:
            return None
        caption = self._captions[i]
        elapsed = out_frame - caption.out_start
        if elapsed < 0 or out_frame >= caption.out_end:
            return None
        return caption, elapsed

    def _alpha(self, elapsed: int, caption: ReelCaption) -> float:
        """Fade in, hold, fade out - clipped to the play's own length.

        A segment shorter than the envelope gets a proportionally shorter
        caption instead of one that outlives the play and reappears over
        the next one's opening frames.
        """
        fade = max(1, int(FADE_SECONDS * self._fps))
        full = fade * 2 + int(HOLD_SECONDS * self._fps)
        span = min(full, max(1, caption.out_end - caption.out_start))
        if elapsed >= span:
            return 0.0
        fade = min(fade, span // 2)
        if fade <= 0:
            return 1.0
        if elapsed < fade:
            return elapsed / fade
        if elapsed >= span - fade:
            return max(0.0, (span - elapsed) / fade)
        return 1.0

    # ---- drawing ---------------------------------------------------

    def _build_box(self, caption: ReelCaption, video_height: int) -> Image.Image:
        key = (caption.title, caption.accent, video_height)
        cached = self._box_cache.get(key)
        if cached is not None:
            return cached

        font = _load_font(int(video_height * FONT_SCALE), bold=True)
        pad_x = int(video_height * PAD_X_SCALE)
        pad_y = int(video_height * PAD_Y_SCALE)
        accent_w = max(3, int(video_height * ACCENT_SCALE))

        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        text_w, _ = _measure_text(probe, caption.title, font)
        # Height from the FONT, not from the ink. `textbbox` measures the
        # pixels a particular string happens to cover, so "Ace" came out
        # 16px shorter than "Dig" - the descender on the g - and the
        # caption changed size from one play to the next.
        ascent, descent = font.getmetrics()

        width = accent_w + pad_x * 2 + text_w
        height = pad_y * 2 + ascent + descent

        box = Image.new("RGBA", (width, height), BG_COLOR)
        draw = ImageDraw.Draw(box)
        draw.rectangle(
            [0, 0, accent_w - 1, height - 1],
            fill=_hex_to_rgba(caption.accent) if caption.accent else BG_COLOR,
        )
        # Pillow's default anchor puts y at the top of the ascender, so
        # every caption sits on the same baseline whatever it spells.
        draw.text(
            (accent_w + pad_x, pad_y),
            caption.title,
            font=font,
            fill=TEXT_COLOR,
        )

        if len(self._box_cache) >= BOX_CACHE_LIMIT:
            self._box_cache.clear()
        self._box_cache[key] = box
        return box
