"""Scoreboard overlay rendering.

Pure Pillow + NumPy. No coupling to event types or domain Match - takes a
`GameState` plus team metadata and produces a composited frame.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...domain.game_state import GameState

logger = logging.getLogger(__name__)

OVERLAY_OPACITY = 0.9
SCORE_FONT_SCALE = 0.025
FONT_SCALE_TEAM = 0.45
FONT_SCALE_SETS = 0.40
FONT_SCALE_SCORE = 0.50
POINT_CIRCLE_MAX_DIAMETER = 10
# Logo disc size as a fraction of the main bar height, and the gap
# between the disc and the team name.
LOGO_HEIGHT_SCALE = 0.82
LOGO_GAP_SCALE = 0.35
# Supersampling factor for the circular mask. Drawing the mask at 4x and
# downsampling is what makes the disc edge smooth; Pillow's ellipse is
# hard-edged.
LOGO_MASK_SUPERSAMPLE = 4
# A logo is treated as "has transparency" (and therefore fitted whole,
# rather than cover-cropped) when this fraction of its pixels are at
# least partly transparent.
LOGO_ALPHA_FRACTION = 0.02
# Divider slant, as a fraction of the bar height: the half-offset each
# boundary moves, so the total lean across the bar is 2x this.
SKEW_SCALE = 0.34
# The slanted blocks are drawn at this scale and box-filtered back down.
# Pillow's polygon fill is hard-edged, and a near-vertical diagonal is
# exactly where that shows. Text and logos are drawn afterwards at
# native resolution so they stay crisp.
SHAPE_SUPERSAMPLE = 4


@dataclass
class TeamBranding:
    name: str
    color: str  # hex like "#2d8a4e"
    # Absolute path to the team's logo image, or None. Rendered as a
    # circular disc at the outer edge of the team's block - cropped to a
    # circle so logos with an opaque rectangular background don't show
    # as a mismatched box on the colored bar.
    logo_path: Optional[str] = None
    # Absolute paths to player photos, keyed by jersey number. Not used
    # by the scoreboard itself - it rides along here because TeamBranding
    # is the bundle of "how this team looks" that every overlay already
    # receives, and the popup renderer needs it to draw a player's face
    # next to their name.
    player_photos: dict[int, str] = field(default_factory=dict)


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
        # Circular logo discs, keyed (path, diameter). Prepared once per
        # render (decode + crop + mask is far too slow per frame) and
        # reused by the memoized overlay bitmap.
        self._logo_cache: dict[tuple[str, int], Optional[Image.Image]] = {}

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
            self.home.logo_path,
            self.away.logo_path,
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
        # Logo discs live inside the team blocks, at the outer edges
        # (home on the far left, away on the far right) - the broadcast
        # convention. A team with no logo simply keeps the old layout.
        logo_d = max(8, int(main_h * LOGO_HEIGHT_SCALE))
        logo_gap = max(4, int(logo_d * LOGO_GAP_SCALE))
        home_logo = self._logo_disc(self.home.logo_path, logo_d)
        away_logo = self._logo_disc(self.away.logo_path, logo_d)
        home_extra = (logo_d + logo_gap) if home_logo is not None else 0
        away_extra = (logo_d + logo_gap) if away_logo is not None else 0

        # Section dividers are slanted, broadcast-style: they lean toward
        # the center block going down, mirrored either side of the score.
        # `skew` is the half-offset, so a divider moves 2*skew across the
        # bar's height. Every section gets `skew` of extra width so the
        # slant eats into padding rather than into the text.
        skew = max(1, int(main_h * SKEW_SCALE))

        home_section = home_w + pad * 2 + home_extra + skew
        away_section = away_w + pad * 2 + away_extra + skew
        sets_section = single_set_w + pad * 2 + skew
        center_section = score_w + pad * 2 + skew

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

        # Boundary x positions, measured at MID height - so the section
        # widths above still describe the bar where the text sits, and a
        # change of `skew` never shifts the labels.
        b1 = home_section
        b2 = b1 + sets_section
        b3 = b2 + center_section
        b4 = b3 + sets_section

        # Left of the score the dividers lean "\\" (top edge further
        # left), right of it they lean "/" - which is what makes the
        # center block a trapezoid and the set boxes parallelograms.
        self._draw_slanted_blocks(
            overlay,
            main_h,
            total_w,
            (b1, b2, b3, b4),
            skew,
            (home_rgba, sets_color, center_color, sets_color, away_rgba),
        )

        if home_logo is not None:
            overlay.alpha_composite(home_logo, (pad, (main_h - logo_d) // 2))
        draw.text(
            (pad + home_extra, text_y),
            self.home.name,
            font=team_font,
            fill=(255, 255, 255, 255),
            anchor="lm",
        )

        x = home_section
        draw.text(
            (x + sets_section // 2, text_y),
            str(state.home_sets),
            font=sets_font,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        x += sets_section

        draw.text(
            (x + center_section // 2, text_y),
            score_text,
            font=score_font,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        x += center_section

        draw.text(
            (x + sets_section // 2, text_y),
            str(state.away_sets),
            font=sets_font,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        x += sets_section

        draw.text(
            (x + pad + skew, text_y), self.away.name, font=team_font, fill=(255, 255, 255, 255), anchor="lm"
        )
        if away_logo is not None:
            overlay.alpha_composite(
                away_logo, (total_w - pad - logo_d, (main_h - logo_d) // 2)
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

    @staticmethod
    def _draw_slanted_blocks(
        overlay: Image.Image,
        main_h: int,
        total_w: int,
        boundaries: tuple[int, int, int, int],
        skew: int,
        colors: tuple,
    ) -> None:
        """Fill the five bar sections as slant-edged polygons.

        `boundaries` are the four divider positions at mid height. Each
        divider is drawn from `x - skew` at the top to `x + skew` at the
        bottom on the home side, and mirrored on the away side, so both
        set boxes lean outward and the score block is a trapezoid.

        Drawn supersampled onto a scratch layer and box-filtered down -
        an exact 1/N area average, so the diagonals are smooth without
        the ringing a Lanczos downscale would add at these hard color
        boundaries.
        """
        b1, b2, b3, b4 = boundaries
        ss = SHAPE_SUPERSAMPLE
        shapes = Image.new("RGBA", (total_w * ss, main_h * ss), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shapes)

        def poly(points, fill) -> None:
            sd.polygon([(round(px * ss), round(py * ss)) for px, py in points], fill=fill)

        home_c, home_sets_c, center_c, away_sets_c, away_c = colors
        # top-x, bottom-x for each divider (home side leans right going
        # down, away side leans left).
        d1 = (b1 - skew, b1 + skew)
        d2 = (b2 - skew, b2 + skew)
        d3 = (b3 + skew, b3 - skew)
        d4 = (b4 + skew, b4 - skew)

        poly([(0, 0), (d1[0], 0), (d1[1], main_h), (0, main_h)], home_c)
        poly([(d1[0], 0), (d2[0], 0), (d2[1], main_h), (d1[1], main_h)], home_sets_c)
        poly([(d2[0], 0), (d3[0], 0), (d3[1], main_h), (d2[1], main_h)], center_c)
        poly([(d3[0], 0), (d4[0], 0), (d4[1], main_h), (d3[1], main_h)], away_sets_c)
        poly(
            [(d4[0], 0), (total_w, 0), (total_w, main_h), (d4[1], main_h)],
            away_c,
        )

        overlay.alpha_composite(shapes.resize((total_w, main_h), Image.BOX), (0, 0))

    def _logo_disc(self, path: Optional[str], diameter: int) -> Optional[Image.Image]:
        """Load `path` as a circular RGBA disc of `diameter` px, or None.

        Two fitting modes, chosen from the source image itself:

        * **Opaque logo** (a JPEG, or a PNG with a filled background):
          scaled to *cover* the circle and center-cropped, so the disc is
          completely filled and the square background never shows.
        * **Transparent logo**: scaled to *fit* inside the circle, so a
          wide or tall mark keeps all of its detail - the circle mask is
          then a no-op on the already-transparent margins.

        Failures (missing file, corrupt image, unreadable format) are
        logged once and degrade to no logo rather than killing a render
        that's already minutes in.
        """
        if not path:
            return None
        key = (path, diameter)
        if key in self._logo_cache:
            return self._logo_cache[key]

        disc: Optional[Image.Image] = None
        try:
            src = Image.open(path)
            src.load()
            src = src.convert("RGBA")
            if _has_transparency(src):
                fitted = _fit_contain(src, diameter)
            else:
                fitted = _fit_cover(src, diameter)
            disc = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
            disc.paste(
                fitted,
                ((diameter - fitted.width) // 2, (diameter - fitted.height) // 2),
            )
            # Multiply the existing alpha by the circle mask instead of
            # replacing it, so transparent logos stay transparent.
            mask = _circle_mask(diameter)
            alpha = Image.fromarray(
                (
                    np.asarray(disc.getchannel("A"), dtype=np.uint16)
                    * np.asarray(mask, dtype=np.uint16)
                    // 255
                ).astype(np.uint8)
            )
            disc.putalpha(alpha)
        except Exception:
            logger.warning("could not load team logo %s", path, exc_info=True)
            disc = None

        self._logo_cache[key] = disc
        return disc

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


def _has_transparency(img: Image.Image) -> bool:
    """True when a meaningful share of `img` is not fully opaque.

    A handful of stray semi-transparent pixels (JPEG-ish artifacts, or an
    anti-aliased border on an otherwise solid image) shouldn't flip the
    fit mode, hence the fraction threshold rather than `min(alpha) < 255`.
    """
    alpha = np.asarray(img.getchannel("A"), dtype=np.uint8)
    if alpha.size == 0:
        return False
    return float((alpha < 250).mean()) >= LOGO_ALPHA_FRACTION


def _fit_contain(img: Image.Image, box: int) -> Image.Image:
    """Scale `img` to fit entirely within a `box`-square, keeping aspect."""
    scale = min(box / img.width, box / img.height)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.LANCZOS)


def _fit_cover(img: Image.Image, box: int, y_bias: float = 0.5) -> Image.Image:
    """Scale + crop `img` to exactly fill a `box`-square.

    `y_bias` picks the vertical crop window: 0.5 centers it, lower values
    keep more of the top - which is what a portrait photo needs, since
    center-cropping a standing player lands on their torso.
    """
    scale = max(box / img.width, box / img.height)
    size = (max(box, round(img.width * scale)), max(box, round(img.height * scale)))
    resized = img.resize(size, Image.LANCZOS)
    left = (resized.width - box) // 2
    top = int((resized.height - box) * min(1.0, max(0.0, y_bias)))
    return resized.crop((left, top, left + box, top + box))


_MASK_CACHE: dict[int, Image.Image] = {}


def _circle_mask(diameter: int) -> Image.Image:
    """Antialiased circular alpha mask, cached per diameter."""
    cached = _MASK_CACHE.get(diameter)
    if cached is not None:
        return cached
    big = diameter * LOGO_MASK_SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, big - 1, big - 1], fill=255)
    mask = mask.resize((diameter, diameter), Image.LANCZOS)
    _MASK_CACHE[diameter] = mask
    return mask


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
