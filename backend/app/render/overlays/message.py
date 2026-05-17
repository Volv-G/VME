"""Message popup overlay rendering.

Slide-in popups shown stacked from the bottom-right of the frame. Operates on
the `ActiveMessage` records produced by `frame_map_builder`.

Player popups carry a `team`/`player_number` reference; this renderer looks
up the player on the matching roster to add a `#NN First L.` subtitle, and
uses the team's color as the background tint so home and away events are
visually distinct.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...domain.roster import Roster
from ..frame_map import ActiveMessage
from .scoreboard import TeamBranding

OVERLAY_OPACITY = 0.9
TITLE_FONT_SCALE = 0.028
SUBTITLE_FONT_SCALE = 0.020
ACCENT_BAR_WIDTH = 6
PADDING = 16
LINE_GAP = 4
BG_COLOR_DEFAULT: tuple[int, int, int, int] = (45, 35, 75, 255)
TEXT_COLOR: tuple[int, int, int, int] = (255, 255, 255, 255)

# Animation envelope (fractions of total popup duration).
SLIDE_IN_FRAC = 0.1
SLIDE_OUT_FRAC = 0.1


class MessageOverlayRenderer:
    """Renders a list of `ActiveMessage`s onto frames."""

    def __init__(
        self,
        *,
        home_roster: Optional[Roster] = None,
        away_roster: Optional[Roster] = None,
        home: Optional[TeamBranding] = None,
        away: Optional[TeamBranding] = None,
    ) -> None:
        self._home_roster = home_roster
        self._away_roster = away_roster
        self._home_branding = home
        self._away_branding = away
        self._image_cache: dict[str, Image.Image] = {}

    def apply(self, frame: np.ndarray, messages: list[ActiveMessage]) -> np.ndarray:
        if not messages:
            return frame
        h, w = frame.shape[:2]
        video = Image.fromarray(frame).convert("RGBA")
        margin = 20
        bottom_margin = 80
        y_offset = h - bottom_margin

        for msg in messages:
            title = msg.text or ""
            subtitle = self._resolve_subtitle(msg)
            bg = self._resolve_bg(msg)
            box = self._build_box(title, subtitle, h, msg.image_path, bg)
            x_off = self._x_offset(msg.progress, box.width)
            x = w - box.width - margin + x_off
            y = y_offset - box.height

            alpha = box.split()[3].point(lambda v: int(v * OVERLAY_OPACITY))
            box.putalpha(alpha)
            video.paste(box, (x, y), box)
            y_offset = y - 10

        return np.array(video.convert("RGB"))

    # ------------------------------------------------------------------

    def _resolve_subtitle(self, msg: ActiveMessage) -> Optional[str]:
        if msg.player_number is None:
            return None
        roster = self._roster_for(msg.team)
        name_part = f"#{msg.player_number}"
        if roster is not None:
            player = roster.find_by_number(msg.player_number)
            if player is not None:
                short = player.short_name or _short_from_name(player.name)
                if short:
                    name_part = f"#{msg.player_number} {short}"
        return name_part

    def _resolve_bg(self, msg: ActiveMessage) -> tuple[int, int, int, int]:
        # Explicit hex on the message wins.
        if msg.bg_color:
            return _hex_to_rgba(msg.bg_color)
        # Otherwise, fall back to team color.
        branding = self._branding_for(msg.team)
        if branding is not None and branding.color:
            return _hex_to_rgba(branding.color)
        return BG_COLOR_DEFAULT

    def _roster_for(self, team: Optional[str]) -> Optional[Roster]:
        if team == "home":
            return self._home_roster
        if team == "away":
            return self._away_roster
        return None

    def _branding_for(self, team: Optional[str]) -> Optional[TeamBranding]:
        if team == "home":
            return self._home_branding
        if team == "away":
            return self._away_branding
        return None

    def _build_box(
        self,
        title: str,
        subtitle: Optional[str],
        video_height: int,
        image_path: Optional[str],
        bg_color: tuple[int, int, int, int],
    ) -> Image.Image:
        title_font = _load_font(int(video_height * TITLE_FONT_SCALE), bold=True)
        subtitle_font = (
            _load_font(int(video_height * SUBTITLE_FONT_SCALE), bold=False)
            if subtitle
            else None
        )

        loaded_img: Optional[Image.Image] = None
        max_img_h = int(video_height * 0.15)
        if image_path and os.path.exists(image_path):
            ck = f"{image_path}:{max_img_h}"
            loaded_img = self._image_cache.get(ck)
            if loaded_img is None:
                try:
                    loaded_img = Image.open(image_path).convert("RGBA")
                    if loaded_img.height > max_img_h:
                        scale = max_img_h / loaded_img.height
                        new_w = int(loaded_img.width * scale)
                        loaded_img = loaded_img.resize(
                            (new_w, max_img_h), Image.Resampling.LANCZOS
                        )
                    self._image_cache[ck] = loaded_img
                except Exception:
                    loaded_img = None

        tmp = Image.new("RGBA", (1, 1))
        td = ImageDraw.Draw(tmp)
        title_w, title_h = _measure_text(td, title, title_font)
        if subtitle and subtitle_font is not None:
            sub_w, sub_h = _measure_text(td, subtitle, subtitle_font)
        else:
            sub_w, sub_h = 0, 0

        text_w = max(title_w, sub_w)
        text_h = title_h + (LINE_GAP + sub_h if sub_h else 0)

        img_w = loaded_img.width if loaded_img else 0
        img_h = loaded_img.height if loaded_img else 0

        if loaded_img:
            gap = PADDING if (title or subtitle) else 0
            content_w = img_w + gap + text_w
            content_h = max(img_h, text_h)
        else:
            content_w, content_h = text_w, text_h

        box_w = content_w + PADDING * 2
        box_h = content_h + PADDING * 2

        img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        draw.rectangle([(0, 0), (box_w - 1, box_h - 1)], fill=bg_color)
        accent = (
            int(bg_color[0] * 0.6),
            int(bg_color[1] * 0.6),
            int(bg_color[2] * 0.6),
            255,
        )
        draw.rectangle([(0, 0), (ACCENT_BAR_WIDTH - 1, box_h - 1)], fill=accent)

        x_cursor = PADDING
        if loaded_img:
            img_y = PADDING + (content_h - img_h) // 2
            img.paste(loaded_img, (x_cursor, img_y), loaded_img)
            x_cursor += img_w + (PADDING if (title or subtitle) else 0)

        # Stack title + subtitle vertically inside the text region.
        text_block_top = PADDING + (content_h - text_h) // 2
        if title:
            draw.text(
                (x_cursor, text_block_top), title, font=title_font, fill=TEXT_COLOR
            )
        if subtitle and subtitle_font is not None:
            draw.text(
                (x_cursor, text_block_top + title_h + LINE_GAP),
                subtitle,
                font=subtitle_font,
                fill=TEXT_COLOR,
            )

        return img

    def _x_offset(self, progress: float, width: int) -> int:
        if progress < SLIDE_IN_FRAC:
            t = progress / SLIDE_IN_FRAC
            return int(width * (1.0 - _ease_out(t)))
        if progress < 1.0 - SLIDE_OUT_FRAC:
            return 0
        t = (progress - (1.0 - SLIDE_OUT_FRAC)) / SLIDE_OUT_FRAC
        return int(width * _ease_in(t))


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    if not text:
        return 0, 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _hex_to_rgba(hex_color: str) -> tuple[int, int, int, int]:
    s = hex_color.lstrip("#")
    if len(s) != 6:
        return BG_COLOR_DEFAULT
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
    except ValueError:
        return BG_COLOR_DEFAULT


def _short_from_name(name: str) -> str:
    """Derive a compact display from a full name: "Kate Greene" -> "Kate G.".

    Returns the original `name` unchanged when it can't be split sensibly.
    """
    if not name:
        return ""
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def _load_font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    if sys.platform == "darwin":
        path = "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf"
    else:
        path = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(path, max(8, size))
    except Exception:
        return ImageFont.load_default()


def _ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _ease_in(t: float) -> float:
    return t ** 3
