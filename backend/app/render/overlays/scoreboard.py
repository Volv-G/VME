"""Scoreboard overlay rendering.

Pure Pillow + NumPy. No coupling to event types or domain Match - takes a
`GameState` plus team metadata and produces a composited frame.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...domain.game_state import GameState

OVERLAY_OPACITY = 0.9
SCORE_FONT_SCALE = 0.025
FONT_SCALE_TEAM = 0.45
FONT_SCALE_SETS = 0.40
FONT_SCALE_SCORE = 0.50
POINT_CIRCLE_MAX_DIAMETER = 10


@dataclass
class TeamBranding:
    name: str
    color: str  # hex like "#2d8a4e"


class ScoreboardOverlay:
    """Render a broadcast-style scoreboard bar from a `GameState`."""

    def __init__(self, home: TeamBranding, away: TeamBranding) -> None:
        self.home = home
        self.away = away
        self._cache: Optional[Image.Image] = None
        self._cache_key: Optional[tuple] = None
        # Numpy form of `_cache`, derived lazily by `apply`: straight RGB
        # plus a broadcastable alpha in [0,1] with OVERLAY_OPACITY already
        # folded in. Keyed on the same tuple as the Pillow cache.
        self._np_cache: Optional[tuple[np.ndarray, np.ndarray]] = None
        self._np_cache_key: Optional[tuple] = None

    def render_overlay(self, video_height: int, state: GameState) -> Image.Image:
        cache_key = (
            video_height,
            state.home_score,
            state.away_score,
            state.home_sets,
            state.away_sets,
            tuple(state.point_history),
            self.home.name,
            self.away.name,
            self.home.color,
            self.away.color,
        )
        if self._cache is not None and self._cache_key == cache_key:
            return self._cache

        score_font_size = int(video_height * SCORE_FONT_SCALE)
        main_h = max(1, int(score_font_size / FONT_SCALE_SCORE))

        team_font, sets_font, score_font = _load_fonts(main_h)

        tmp = Image.new("RGBA", (1, 1))
        td = ImageDraw.Draw(tmp)

        home_w = _text_w(td, self.home.name, team_font)
        away_w = _text_w(td, self.away.name, team_font)
        score_text = f"{state.home_score}  -  {state.away_score}"
        score_w = _text_w(td, score_text, score_font)
        single_set_w = _text_w(td, "0", sets_font)

        pad = 16
        home_section = home_w + pad * 2
        away_section = away_w + pad * 2
        sets_section = single_set_w + pad * 2
        center_section = score_w + pad * 2

        total_w = home_section + sets_section + center_section + sets_section + away_section

        slots = max(15, len(state.point_history))
        max_d = 4 * total_w / (5 * slots + 1)
        circle_d = max(2, min(POINT_CIRCLE_MAX_DIAMETER, max_d))
        gap = circle_d / 4
        point_h = int(circle_d + 2 * gap)

        total_h = main_h + point_h
        overlay = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        home_rgba = _hex_to_rgba(self.home.color)
        away_rgba = _hex_to_rgba(self.away.color)
        sets_color = (80, 60, 100, 255)
        center_color = (45, 35, 75, 255)

        text_y = main_h // 2

        draw.rectangle([(0, 0), (home_section, main_h)], fill=home_rgba)
        draw.text((pad, text_y), self.home.name, font=team_font, fill=(255, 255, 255, 255), anchor="lm")

        x = home_section
        draw.rectangle([(x, 0), (x + sets_section, main_h)], fill=sets_color)
        draw.text(
            (x + sets_section // 2, text_y),
            str(state.home_sets),
            font=sets_font,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        x += sets_section

        draw.rectangle([(x, 0), (x + center_section, main_h)], fill=center_color)
        draw.text(
            (x + center_section // 2, text_y),
            score_text,
            font=score_font,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        x += center_section

        draw.rectangle([(x, 0), (x + sets_section, main_h)], fill=sets_color)
        draw.text(
            (x + sets_section // 2, text_y),
            str(state.away_sets),
            font=sets_font,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        x += sets_section

        draw.rectangle([(x, 0), (total_w, main_h)], fill=away_rgba)
        draw.text(
            (x + pad, text_y), self.away.name, font=team_font, fill=(255, 255, 255, 255), anchor="lm"
        )

        draw.rectangle([(0, main_h), (total_w, total_h)], fill=center_color)
        if state.point_history:
            cy = main_h + point_h / 2
            r = circle_d / 2
            step = circle_d + gap
            x0 = gap + r
            for i, is_home in enumerate(state.point_history):
                cx = x0 + i * step
                pc = home_rgba if is_home else away_rgba
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=pc)
        draw.line([(0, main_h - 1), (total_w - 1, main_h - 1)], fill=center_color, width=3)

        self._cache = overlay
        self._cache_key = cache_key
        return overlay

    def _overlay_arrays(
        self, video_height: int, state: GameState
    ) -> tuple[np.ndarray, np.ndarray]:
        """Cached (rgb, alpha) numpy view of the rendered overlay.

        The old code did `overlay.copy()` + `alpha.point(lambda ...)` on
        every single frame - a Python-level callback over every pixel of
        the bar - even though the result only changes when the score does.
        Both the Pillow render and this derived array pair are memoized on
        the same key, so a steady scoreboard costs nothing per frame.
        """
        overlay = self.render_overlay(video_height, state)
        key = self._cache_key
        if self._np_cache is not None and self._np_cache_key == key:
            return self._np_cache

        rgba = np.asarray(overlay, dtype=np.uint8)
        rgb = rgba[:, :, :3].astype(np.float32)
        # Quantize exactly like the old Pillow path did
        # (`alpha.point(lambda v: int(v * OVERLAY_OPACITY))`) so the two
        # implementations produce byte-identical output.
        alpha_u8 = np.floor(rgba[:, :, 3:4].astype(np.float32) * OVERLAY_OPACITY)
        alpha = alpha_u8 / 255.0
        # Premultiplied foreground (+0.5 so the uint8 downcast in `apply`
        # rounds instead of truncating - matches Pillow's paste exactly)
        # and the matching background attenuation factor.
        pair = (rgb * alpha + 0.5, 1.0 - alpha)
        self._np_cache = pair
        self._np_cache_key = key
        return pair

    def apply(self, frame: np.ndarray, state: GameState) -> np.ndarray:
        """Alpha-composite the scoreboard onto `frame` (uint8 HxWx3).

        Only the bar's bounding box is touched. The previous implementation
        converted the *whole* frame to RGBA, pasted, and converted back to
        RGB - ~8 ms/frame at 1080p for a ~600x90 overlay. Blending just the
        destination region with numpy is ~1 ms, and the input frame is
        never mutated (decoder buffers can be read-only / reused).
        """
        h, w = frame.shape[:2]
        fg, bg_factor = self._overlay_arrays(h, state)
        oh, ow = fg.shape[:2]

        x = (w - ow) // 2
        y = h - oh - 30
        # Clamp to the frame in case the overlay is wider/taller than the
        # video (tiny renders); anything off-frame is simply cropped.
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + ow), min(h, y + oh)
        if x1 <= x0 or y1 <= y0:
            return frame
        fg_crop = fg[y0 - y : y1 - y, x0 - x : x1 - x]
        bg_crop = bg_factor[y0 - y : y1 - y, x0 - x : x1 - x]

        out = frame.copy()
        region = out[y0:y1, x0:x1].astype(np.float32)
        region *= bg_crop
        region += fg_crop
        out[y0:y1, x0:x1] = region.astype(np.uint8)
        return out


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    s = hex_color.lstrip("#")
    if len(s) != 6:
        return (40, 40, 40, alpha)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)


def _load_fonts(main_h: int):
    team_size = max(8, int(main_h * FONT_SCALE_TEAM))
    sets_size = max(8, int(main_h * FONT_SCALE_SETS))
    score_size = max(8, int(main_h * FONT_SCALE_SCORE))

    if sys.platform == "darwin":
        bold = "/Library/Fonts/Arial Bold.ttf"
        regular = "/Library/Fonts/Arial.ttf"
    else:
        bold = "arialbd.ttf"
        regular = "arial.ttf"

    try:
        return (
            ImageFont.truetype(bold, team_size),
            ImageFont.truetype(regular, sets_size),
            ImageFont.truetype(bold, score_size),
        )
    except Exception:
        d = ImageFont.load_default()
        return d, d, d
