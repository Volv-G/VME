"""Message popup overlay rendering.

Slide-in popups shown stacked from the bottom-right of the frame. Operates on
the `ActiveMessage` records produced by `frame_map_builder`.

Player popups carry a `team`/`player_number` reference; this renderer looks
up the player on the matching roster to add a `#NN First L.` subtitle, and
uses the team's color as the background tint so home and away events are
visually distinct.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...domain.roster import Roster
from ..frame_map import ActiveMessage
from .scoreboard import TeamBranding, _circle_mask, _fit_cover

logger = logging.getLogger(__name__)

OVERLAY_OPACITY = 0.9
# Title and subtitle render at the same scale on purpose - the title is
# bold, the subtitle is regular, so the visual hierarchy comes from
# weight rather than size. Pre-2026 the title was ~40% bigger than the
# subtitle which made action labels ("Kill", "Ace") shout over the
# player info the viewer actually cares about. Substitution popups
# originally pioneered this proportion via a per-effect `title_scale=0.7`
# override; it now applies to every popup by default.
TITLE_FONT_SCALE = 0.020
SUBTITLE_FONT_SCALE = 0.020
ACCENT_BAR_WIDTH = 6
PADDING = 16
LINE_GAP = 4
BG_COLOR_DEFAULT: tuple[int, int, int, int] = (45, 35, 75, 255)
TEXT_COLOR: tuple[int, int, int, int] = (255, 255, 255, 255)

# Animation envelope (fractions of total popup duration).
SLIDE_IN_FRAC = 0.1
SLIDE_OUT_FRAC = 0.1

# Player photo shown at the left of a player popup, as a circle - the
# same treatment as the scoreboard crests and thumbnail discs, so the
# whole broadcast package reads as one design.
# Diameter is a multiple of the popup's text block: tall enough to be a
# recognisable face, never taller than the box it sits in.
AVATAR_SCALE = 1.9
# Photos are shot standing, so a centered square crop lands on the
# torso. Bias the window upward to catch the face.
AVATAR_CROP_BIAS = 0.22
AVATAR_RING = 2
AVATAR_RING_COLOR: tuple[int, int, int, int] = (255, 255, 255, 235)

# Distinct popups kept rendered. ~64 KB each at 1080p.
BOX_CACHE_LIMIT = 256


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
        # Separate from `_image_cache` because a failed load caches None,
        # and `.get()` couldn't tell that from "not tried yet" - which
        # would retry a corrupt file on every single frame.
        self._avatar_cache: dict[str, Optional[Image.Image]] = {}
        # Built popups, keyed by everything that affects their pixels.
        # A popup's content is fixed for its whole on-screen life - only
        # its x position animates - so rebuilding it per frame was pure
        # waste (~1 ms each at 1080p).
        self._box_cache: dict[tuple, Image.Image] = {}

    def apply(self, frame: np.ndarray, messages: list[ActiveMessage]) -> np.ndarray:
        if not messages:
            return frame
        h, w = frame.shape[:2]
        margin = 20
        bottom_margin = 80
        y_offset = h - bottom_margin
        out = frame if frame.flags.writeable else frame.copy()

        for msg in messages:
            title = self._resolve_team_placeholders(msg.text or "")
            subtitle = self._resolve_subtitle(msg)
            bg = self._resolve_bg(msg)
            box = self._build_box(
                title,
                subtitle,
                h,
                msg.image_path,
                bg,
                title_scale=msg.title_scale,
                photo_path=self._photo_for(msg),
            )
            x_off = self._x_offset(msg.progress, box.width)
            x = w - box.width - margin + x_off
            y = y_offset - box.height
            y_offset = y - 10
            _composite(out, box, x, y)

        return out

    # ------------------------------------------------------------------

    def _resolve_team_placeholders(self, text: str) -> str:
        """Substitute `{home}` / `{away}` in popup text with the actual
        team names from the renderer's TeamBranding context.

        Uses `format_map` with a defaulting dict so unknown placeholders
        (and stray `{` that turn up in user-typed messages) survive
        unchanged - we don't want a typo'd Message event to crash the
        renderer. Implementation is shared by all popup flavors so any
        future event can use the same placeholders just by putting them
        in its `text`.
        """
        if not text or "{" not in text:
            return text
        home_name = self._home_branding.name if self._home_branding else "Home"
        away_name = self._away_branding.name if self._away_branding else "Away"
        try:
            return text.format_map(_SafeFormatDict(home=home_name, away=away_name))
        except (ValueError, IndexError):
            # `format_map` can still raise on malformed format spec strings
            # (e.g. "{home:}"). In that case just return the original
            # text unchanged - better than nothing on the rendered video.
            return text

    def _resolve_subtitle(self, msg: ActiveMessage) -> Optional[str]:
        if msg.player_number is None:
            return None
        roster = self._roster_for(msg.team)
        # Two-player subtitle (substitution): "#OUT name → #IN name".
        # The arrow's directionality matters - the outgoing player is
        # on the left, the incoming on the right, matching the natural
        # reading order of "who became who".
        if msg.player_out_number is not None:
            in_label = self._format_player(roster, msg.player_number)
            out_label = self._format_player(roster, msg.player_out_number)
            return f"{out_label} → {in_label}"
        return self._format_player(roster, msg.player_number)

    @staticmethod
    def _format_player(roster: Optional[Roster], jersey: int) -> str:
        """Compose `#NN First L.` or just `#NN` when the roster has no match."""
        label = f"#{jersey}"
        if roster is not None:
            player = roster.find_by_number(jersey)
            if player is not None:
                short = player.short_name or _short_from_name(player.name)
                if short:
                    label = f"#{jersey} {short}"
        return label

    def _resolve_bg(self, msg: ActiveMessage) -> tuple[int, int, int, int]:
        # Explicit hex on the message wins.
        if msg.bg_color:
            return _hex_to_rgba(msg.bg_color)
        # Otherwise, fall back to team color.
        branding = self._branding_for(msg.team)
        if branding is not None and branding.color:
            return _hex_to_rgba(branding.color)
        return BG_COLOR_DEFAULT

    def _photo_for(self, msg: ActiveMessage) -> Optional[str]:
        """The photo to show on this popup, or None.

        Only for popups about one identified player: a substitution names
        two people, and there is no honest way to pick whose face goes on
        it, so those keep the text-only layout.
        """
        if msg.player_number is None or msg.player_out_number is not None:
            return None
        branding = self._branding_for(msg.team)
        if branding is None:
            return None
        return branding.player_photos.get(msg.player_number)

    def _avatar(self, path: str, diameter: int) -> Optional[Image.Image]:
        """Circle-cropped player photo, cached per (path, size).

        Always cover-cropped: a photo is not a logo, and fitting one
        whole into a circle would leave a person floating in a box of
        background. Cropping is what makes twelve different players
        recognisable at popup size.
        """
        key = f"{path}:{diameter}"
        if key in self._avatar_cache:
            return self._avatar_cache[key]
        try:
            with Image.open(path) as src:
                fitted = _fit_cover(
                    src.convert("RGBA"), diameter, y_bias=AVATAR_CROP_BIAS
                )
        except Exception:
            # A deleted or corrupt photo must not take down a render;
            # the popup just falls back to text.
            logger.warning("could not load player photo %s", path, exc_info=True)
            self._avatar_cache[key] = None
            return None
        fitted.putalpha(_circle_mask(diameter))
        if AVATAR_RING:
            ring = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse(
                [(0, 0), (diameter - 1, diameter - 1)],
                outline=AVATAR_RING_COLOR,
                width=AVATAR_RING,
            )
            fitted.alpha_composite(ring)
        self._avatar_cache[key] = fitted
        return fitted

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
        title_scale: float = 1.0,
        photo_path: Optional[str] = None,
    ) -> Image.Image:
        # `title_scale` clamped to a sane range so a typo in the effect
        # (e.g. 0.0) can't produce a zero-pixel font that crashes PIL.
        scale = max(0.4, min(2.0, title_scale or 1.0))
        title_font = _load_font(int(video_height * TITLE_FONT_SCALE * scale), bold=True)
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

        # The player's face, when we have one and the popup isn't already
        # carrying an explicit image (an event-specific graphic wins - it
        # was chosen deliberately, the photo is automatic).
        if loaded_img is None and photo_path:
            loaded_img = self._avatar(photo_path, int(text_h * AVATAR_SCALE))

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

        cache_key = (
            title,
            subtitle,
            bg_color,
            photo_path,
            loaded_img is not None,
            box_w,
            box_h,
        )
        cached = self._box_cache.get(cache_key)
        if cached is not None:
            return cached

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

        # Bake the popup's constant translucency in once, here, rather
        # than per frame at paste time.
        img.putalpha(img.split()[3].point(lambda v: int(v * OVERLAY_OPACITY)))
        if len(self._box_cache) >= BOX_CACHE_LIMIT:
            # A match has a bounded set of popups, but a renderer reused
            # across many matches shouldn't grow without limit. Cheapest
            # correct eviction: start over.
            self._box_cache.clear()
        self._box_cache[cache_key] = img
        return img

    def _x_offset(self, progress: float, width: int) -> int:
        if progress < SLIDE_IN_FRAC:
            t = progress / SLIDE_IN_FRAC
            return int(width * (1.0 - _ease_out(t)))
        if progress < 1.0 - SLIDE_OUT_FRAC:
            return 0
        t = (progress - (1.0 - SLIDE_OUT_FRAC)) / SLIDE_OUT_FRAC
        return int(width * _ease_in(t))


def _composite(frame: np.ndarray, box: Image.Image, x: int, y: int) -> None:
    """Alpha-composite `box` onto `frame` in place, clipped to the frame.

    Works on the destination rectangle only. Converting the whole 1080p
    frame to RGBA and back cost ~8 ms per frame - an order of magnitude
    more than drawing the popup itself - and a popup covers ~2% of the
    picture.
    """
    h, w = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + box.width), min(h, y + box.height)
    if x0 >= x1 or y0 >= y1:
        return
    src = box.crop((x0 - x, y0 - y, x1 - x, y1 - y))
    region = Image.fromarray(frame[y0:y1, x0:x1]).convert("RGBA")
    region.alpha_composite(src)
    frame[y0:y1, x0:x1] = np.asarray(region.convert("RGB"))


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


class _SafeFormatDict(dict):
    """`dict` that leaves unknown `{key}` placeholders intact instead of
    raising `KeyError`. Used by `_resolve_team_placeholders` so messages
    that happen to contain literal braces - e.g. a user-typed annotation
    with curly brackets - aren't an error."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


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
