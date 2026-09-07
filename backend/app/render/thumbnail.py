"""YouTube thumbnail generation.

Produces a 1280x720 JPEG next to a render (`<render>.thumbnail.jpg`),
which the upload job then attaches with `thumbnails.set`.

Design mirrors the scoreboard so a channel looks consistent: the two
team colors meet at a slanted seam, circular team logos sit on either
side, and a neutral "VS" sits in the middle - never the result, which would
spoil the match. A darkened frame lifted from
the render itself is used as the backdrop when one is available, which
is what makes a thumbnail look like *this* match rather than a template.

Pure Pillow. No YouTube, no ffmpeg, no domain types - the caller passes
a fully-resolved `ThumbnailSpec`, which keeps this testable and lets the
same code serve full renders and player reels.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .overlays.scoreboard import _fit_contain, _fit_cover, _has_transparency

logger = logging.getLogger(__name__)

# YouTube's recommended thumbnail size. Also its minimum width (1280) -
# smaller images are accepted but upscaled, and 16:9 avoids letterboxing.
THUMB_W = 1280
THUMB_H = 720
# Hard limit enforced by the API: thumbnails.set rejects >2 MiB.
MAX_THUMB_BYTES = 2 * 1024 * 1024

# The seam between the two team colors, as a fraction of width at the
# top and bottom edge - i.e. the same lean as the scoreboard dividers.
SEAM_TOP = 0.56
SEAM_BOTTOM = 0.44
# Opacity of a team-color wedge at the OUTER edge of the frame when a
# backdrop photo is present. The wedges fade toward the seam (see
# WEDGE_ALPHA_INNER) so the play stays visible in the middle - a flat
# 60%-tint over the whole frame just looks like a color cast.
WEDGE_ALPHA = 0.80
WEDGE_ALPHA_INNER = 0.12
# Backdrop treatment: darken so white text stays readable, and blur just
# enough to stop busy crowd detail from fighting the foreground.
BACKDROP_DARKEN = 0.45
BACKDROP_BLUR = 2.0
# The render has the scoreboard burnt into its bottom edge, and a
# thumbnail showing a second, tiny scoreboard looks like a mistake. A
# slight zoom with the crop biased upward pushes it out of frame and
# tightens the shot on the court at the same time.
BACKDROP_ZOOM = 1.14
BACKDROP_CROP_BIAS = 0.38

LOGO_D = 300
# Vertical crop window for player photos (0 = top of frame).
PHOTO_CROP_BIAS = 0.22
SCORE_BOX_W = 300


@dataclass
class ThumbnailSpec:
    """Everything the thumbnail needs, already resolved by the caller."""

    home_name: str
    away_name: str
    home_color: str = "#2d8a4e"
    away_color: str = "#8a2d2d"
    home_logo: Optional[str] = None
    away_logo: Optional[str] = None
    # Center block. Deliberately NOT the final score: a thumbnail is the
    # first thing a player's family sees, and "3 - 0" spoils the match
    # before they press play. Defaults to "VS"; callers can override it
    # with something non-spoiling if they ever want to.
    center_text: str = "VS"
    # Top strip: date / tournament / match number.
    caption: str = ""
    # Bottom banner, used by player reels ("#8 Kate G - Highlights").
    subject: str = ""
    # Player reels swap the home-team crest for the player themselves:
    # `home_logo` carries their photo when there is one, and this is the
    # fallback drawn as a jersey-number badge ("#8") when there isn't.
    # Ignored when `home_logo` resolves to an image.
    home_badge: str = ""
    # True when `home_logo` is a player PHOTO rather than a crest. Photos
    # are always cover-cropped (a logo's "fit whole on its background"
    # treatment would shrink a person into a postage stamp) with the crop
    # biased upward so it lands on the face.
    home_is_photo: bool = False
    # RGB frame lifted from the render, used as the backdrop.
    backdrop: Optional[np.ndarray] = None


def build_thumbnail(spec: ThumbnailSpec) -> Image.Image:
    """Compose the thumbnail. Never raises for content reasons."""
    img = _backdrop_layer(spec.backdrop)
    _draw_wedges(img, spec, flat=spec.backdrop is None)
    _draw_logos(img, spec)
    _draw_center(img, spec)
    _draw_caption(img, spec)
    return img.convert("RGB")


def write_thumbnail(path: Path, spec: ThumbnailSpec) -> Optional[Path]:
    """Render `spec` to `<path>.thumbnail.jpg`; return it, or None.

    Failures are logged and swallowed: a thumbnail is a nicety, and a
    render that took 40 minutes must not be marked failed because a font
    was missing. Quality is stepped down until the file fits YouTube's
    2 MiB cap - at 1280x720 even q=95 is ~300 KB, so this is belt and
    braces rather than an expected path.
    """
    target = thumbnail_path(path)
    try:
        img = build_thumbnail(spec)
        for quality in (92, 85, 75, 60):
            img.save(target, "JPEG", quality=quality, optimize=True)
            if target.stat().st_size <= MAX_THUMB_BYTES:
                break
        logger.info(
            "wrote thumbnail %s (%d KiB)", target, target.stat().st_size // 1024
        )
        return target
    except Exception:
        logger.warning("could not write thumbnail for %s", path, exc_info=True)
        return None


def thumbnail_path(render_path: Path) -> Path:
    """Sidecar path for a render, alongside `.youtube.json`/`.chapters.txt`."""
    return render_path.with_suffix(render_path.suffix + ".thumbnail.jpg")


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


def _backdrop_layer(frame: Optional[np.ndarray]) -> Image.Image:
    """Darkened, slightly blurred cover-crop of `frame`, or flat charcoal."""
    if frame is None:
        return Image.new("RGB", (THUMB_W, THUMB_H), (26, 22, 38))
    try:
        src = Image.fromarray(np.asarray(frame, dtype=np.uint8), "RGB")
        # Cover-crop rather than squash: the source is 16:9 already, but
        # a differently-shaped render shouldn't distort players.
        scale = max(THUMB_W / src.width, THUMB_H / src.height) * BACKDROP_ZOOM
        src = src.resize(
            (max(THUMB_W, round(src.width * scale)), max(THUMB_H, round(src.height * scale))),
            Image.LANCZOS,
        )
        left = (src.width - THUMB_W) // 2
        top = int((src.height - THUMB_H) * BACKDROP_CROP_BIAS)
        src = src.crop((left, top, left + THUMB_W, top + THUMB_H))
        src = src.filter(ImageFilter.GaussianBlur(BACKDROP_BLUR))
        arr = np.asarray(src, dtype=np.float32) * (1.0 - BACKDROP_DARKEN)
        return Image.fromarray(arr.astype(np.uint8), "RGB")
    except Exception:
        logger.warning("unusable backdrop frame; falling back to flat", exc_info=True)
        return Image.new("RGB", (THUMB_W, THUMB_H), (26, 22, 38))


def _draw_wedges(img: Image.Image, spec: ThumbnailSpec, *, flat: bool) -> None:
    """Paint the two team-color wedges meeting at a slanted seam.

    Opaque when there's no backdrop (the colors ARE the design), and
    translucent over a photo so the action still reads through.
    """
    x_top = int(THUMB_W * SEAM_TOP)
    x_bot = int(THUMB_W * SEAM_BOTTOM)

    layer = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(
        [(0, 0), (x_top, 0), (x_bot, THUMB_H), (0, THUMB_H)],
        fill=_rgba(spec.home_color, 255),
    )
    d.polygon(
        [(x_top, 0), (THUMB_W, 0), (THUMB_W, THUMB_H), (x_bot, THUMB_H)],
        fill=_rgba(spec.away_color, 255),
    )
    if not flat:
        # Horizontal ramp: strong at both outer edges, nearly clear in
        # the middle third where the ball usually is.
        xs = np.linspace(0.0, 1.0, THUMB_W, dtype=np.float32)
        edge = np.abs(xs - 0.5) * 2.0  # 1 at the edges, 0 at center
        ramp = WEDGE_ALPHA_INNER + (WEDGE_ALPHA - WEDGE_ALPHA_INNER) * edge**1.5
        mask = np.tile((ramp * 255).astype(np.uint8), (THUMB_H, 1))
        alpha = np.asarray(layer.getchannel("A"), dtype=np.uint16) * mask // 255
        layer.putalpha(Image.fromarray(alpha.astype(np.uint8)))
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))


def _draw_logos(img: Image.Image, spec: ThumbnailSpec) -> None:
    """Circular team logos, one per side, vertically centered."""
    y = (THUMB_H - LOGO_D) // 2 - 20
    home_x = int(THUMB_W * 0.13) - LOGO_D // 2
    away_x = int(THUMB_W * 0.87) - LOGO_D // 2
    home_disc = _logo_disc(spec.home_logo, LOGO_D, photo=spec.home_is_photo)
    if home_disc is None and spec.home_badge:
        # No player photo (or it failed to load): a number badge still
        # identifies whose reel this is, which is the whole point of the
        # left-hand disc on a reel thumbnail.
        home_disc = _badge_disc(spec.home_badge, spec.home_color, LOGO_D)
    for disc, x in ((home_disc, home_x), (_logo_disc(spec.away_logo, LOGO_D), away_x)):
        rgba = img.convert("RGBA")
        rgba.alpha_composite(disc, (x, y))
        img.paste(rgba.convert("RGB"), (0, 0))


def _draw_center(img: Image.Image, spec: ThumbnailSpec) -> None:
    """Score (or "VS") in a dark plate, with the team names beneath it."""
    d = ImageDraw.Draw(img, "RGBA")
    score_font = _font(150, bold=True)
    name_font = _font(52, bold=True)

    text = spec.center_text or "VS"
    cx, cy = THUMB_W // 2, THUMB_H // 2 - 40

    # A soft plate keeps the score legible over any backdrop.
    bbox = d.textbbox((cx, cy), text, font=score_font, anchor="mm")
    pad_x, pad_y = 44, 20
    d.rounded_rectangle(
        [bbox[0] - pad_x, bbox[1] - pad_y, bbox[2] + pad_x, bbox[3] + pad_y],
        radius=24,
        fill=(20, 16, 30, 205),
    )
    _text_with_shadow(d, (cx, cy), text, score_font, anchor="mm")

    names_y = bbox[3] + pad_y + 46
    _text_with_shadow(
        d, (cx - 30, names_y), spec.home_name, name_font, anchor="rm"
    )
    _text_with_shadow(d, (cx, names_y), "|", name_font, anchor="mm")
    _text_with_shadow(
        d, (cx + 30, names_y), spec.away_name, name_font, anchor="lm"
    )


def _draw_caption(img: Image.Image, spec: ThumbnailSpec) -> None:
    """Caption strip along the top, subject banner along the bottom."""
    d = ImageDraw.Draw(img, "RGBA")
    if spec.caption:
        d.rectangle([0, 0, THUMB_W, 78], fill=(12, 10, 20, 170))
        _text_with_shadow(
            d, (THUMB_W // 2, 39), spec.caption, _font(44, bold=True), anchor="mm"
        )
    if spec.subject:
        d.rectangle([0, THUMB_H - 110, THUMB_W, THUMB_H], fill=(12, 10, 20, 190))
        _text_with_shadow(
            d,
            (THUMB_W // 2, THUMB_H - 55),
            spec.subject,
            _font(64, bold=True),
            anchor="mm",
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _logo_disc(
    path: Optional[str], diameter: int, *, photo: bool = False
) -> Optional[Image.Image]:
    """Circle-cropped logo on a solid backing disc.

    Bigger than the scoreboard version, so it's worth being smarter about
    fit. Three cases:

    * transparent logo -> fit whole, backing stays white;
    * opaque logo with a UNIFORM background (the common "logo on white"
      export) -> fit whole and paint the backing in that same color, so
      nothing is cropped and the join is invisible;
    * opaque logo with a busy background (a photo) -> cover-crop, since
      fitting it whole would just show a rectangle on a white disc.

    The backing disc also stops a logo whose edge color matches its team
    wedge from dissolving into the background.
    """
    if not path:
        return None
    try:
        src = Image.open(path)
        src.load()
        src = src.convert("RGBA")
        backing = (255, 255, 255, 255)
        if photo:
            # Faces sit in the upper third of a standing-player photo.
            fitted = _fit_cover(src, diameter, y_bias=PHOTO_CROP_BIAS)
        elif _has_transparency(src):
            inner = diameter - 16
            fitted = _fit_contain(src, inner)
        else:
            uniform = _uniform_background(src)
            if uniform is not None:
                backing = uniform
                inner = diameter - 24
                fitted = _fit_contain(src, inner)
            else:
                inner = diameter - 16
                fitted = _fit_cover(src, inner)
        disc = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
        d = ImageDraw.Draw(disc)
        ss = 4
        ring = Image.new("L", (diameter * ss, diameter * ss), 0)
        ImageDraw.Draw(ring).ellipse([0, 0, diameter * ss - 1, diameter * ss - 1], fill=255)
        ring = ring.resize((diameter, diameter), Image.LANCZOS)
        # Backing circle, then the logo, then re-mask so nothing spills
        # outside the circle.
        d.ellipse([0, 0, diameter - 1, diameter - 1], fill=backing)
        disc.alpha_composite(
            fitted,
            ((diameter - fitted.width) // 2, (diameter - fitted.height) // 2),
        )
        alpha = Image.fromarray(
            (
                np.asarray(disc.getchannel("A"), dtype=np.uint16)
                * np.asarray(ring, dtype=np.uint16)
                // 255
            ).astype(np.uint8)
        )
        disc.putalpha(alpha)
        return disc
    except Exception:
        logger.warning("could not load logo %s for thumbnail", path, exc_info=True)
        return None


def _badge_disc(text: str, color: str, diameter: int) -> Image.Image:
    """Jersey-number badge: team-colored circle, white ring, white text.

    Sized to the text rather than a fixed point size, so "#7" and "#24"
    both fill the disc without overflowing it.
    """
    ss = 2  # modest supersample; the ring is the only curved edge
    big = diameter * ss
    disc = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(disc)
    d.ellipse([0, 0, big - 1, big - 1], fill=(255, 255, 255, 255))
    inset = int(big * 0.045)
    d.ellipse(
        [inset, inset, big - 1 - inset, big - 1 - inset], fill=_rgba(color, 255)
    )

    # Shrink-to-fit: start big, step down until it fits the inner circle.
    usable = big - 2 * inset - int(big * 0.14)
    size = int(big * 0.46)
    font = _font(size, bold=True)
    while size > 8:
        box = d.textbbox((0, 0), text, font=font)
        if (box[2] - box[0]) <= usable and (box[3] - box[1]) <= usable:
            break
        size = int(size * 0.9)
        font = _font(size, bold=True)
    d.text(
        (big // 2, big // 2),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        anchor="mm",
    )
    return disc.resize((diameter, diameter), Image.LANCZOS)


def _uniform_background(img: Image.Image, tolerance: int = 18) -> Optional[tuple]:
    """The logo's background color when all four corners agree, else None.

    A cheap stand-in for real background detection: exports of team crests
    are overwhelmingly "mark centered on a flat field", and four matching
    corners is a reliable tell for that without decoding the whole image
    into a histogram.
    """
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    h, w = arr.shape[:2]
    if h < 4 or w < 4:
        return None
    corners = np.stack(
        [arr[0, 0], arr[0, w - 1], arr[h - 1, 0], arr[h - 1, w - 1]]
    ).astype(np.int16)
    if int(np.abs(corners - corners.mean(axis=0)).max()) > tolerance:
        return None
    rgb = corners.mean(axis=0).round().astype(int)
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]), 255)


def _text_with_shadow(draw, xy, text, font, *, anchor="mm") -> None:
    """White text with a dark offset shadow - readable on any backdrop."""
    x, y = xy
    draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 190), anchor=anchor)
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255), anchor=anchor)


def _rgba(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    s = (hex_color or "").lstrip("#")
    if len(s) != 6:
        return (40, 40, 40, alpha)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)
    except ValueError:
        return (40, 40, 40, alpha)


def _font(size: int, *, bold: bool = False):
    if sys.platform == "darwin":
        name = "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf"
    else:
        name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(name, size)
    except Exception:
        return ImageFont.load_default()
