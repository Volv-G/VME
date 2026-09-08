"""Thumbnail and poster generation.

Produces a 1280x720 JPEG next to a render (`<render>.thumbnail.jpg`),
which the upload job then attaches with `thumbnails.set`, plus - for
full match renders - a pair of Jellyfin sidecars (see
`write_jellyfin_sidecars`) including a 2:3 portrait poster.

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
import shutil
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

# Portrait poster, for media servers whose grid is built around 2:3 box
# art (Jellyfin's "Primary" image). 1000x1500 is Jellyfin's recommended
# poster size. The layout stacks the teams instead of placing them side
# by side: at this aspect there is no room for two 300px discs plus
# legible names on one line, and vertical stacking is what box art looks
# like anyway.
POSTER_W = 1000
POSTER_H = 1500
POSTER_LOGO_D = 340
# The seam between the team colors runs across the poster rather than
# down it, with the same lean as the landscape version.
POSTER_SEAM_LEFT = 0.54
POSTER_SEAM_RIGHT = 0.46
# A portrait crop of a 16:9 frame keeps the full height, which would put
# the render's burnt-in scoreboard - including the score - right at the
# bottom of the poster. Zoom in and anchor the crop to the top so the
# bottom fifth of the frame, scoreboard and all, is cut away.
POSTER_ZOOM = 1.24
POSTER_CROP_BIAS = 0.0


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
    img = _backdrop_layer(spec.backdrop, THUMB_W, THUMB_H)
    _draw_wedges(img, spec, flat=spec.backdrop is None)
    _draw_logos(img, spec)
    _draw_center(img, spec)
    _draw_caption(img, spec)
    return img.convert("RGB")


def build_poster(spec: ThumbnailSpec) -> Image.Image:
    """Compose the 2:3 portrait poster. Same design, stacked vertically."""
    img = _backdrop_layer(
        spec.backdrop,
        POSTER_W,
        POSTER_H,
        zoom=POSTER_ZOOM,
        crop_bias=POSTER_CROP_BIAS,
    )
    _draw_poster_wedges(img, spec, flat=spec.backdrop is None)
    _draw_poster_content(img, spec)
    return img.convert("RGB")


def write_thumbnail(path: Path, spec: ThumbnailSpec) -> Optional[Path]:
    """Render `spec` to `<path>.thumbnail.jpg`; return it, or None."""
    return _write(thumbnail_path(path), build_thumbnail, spec, path)


def write_poster(target: Path, spec: ThumbnailSpec) -> Optional[Path]:
    """Render `spec` as a portrait poster to `target`; return it, or None."""
    return _write(target, build_poster, spec, target)


def write_jellyfin_sidecars(
    render_path: Path, spec: ThumbnailSpec, thumbnail: Optional[Path] = None
) -> list[Path]:
    """Write Jellyfin's local-image sidecars for a render.

    Jellyfin's local image provider matches on filename: for `Match.mp4`
    it reads `Match-thumb.jpg` as the landscape Thumb and
    `Match-poster.jpg` as the 2:3 Primary. Note how that differs from our
    own sidecar (`Match.mp4.thumbnail.jpg`) - Jellyfin wants the media
    extension *replaced*, not appended, so this can't be the same file
    under a second name.

    The landscape image is copied from the thumbnail we already built
    rather than re-rendered; the poster is composed here because its
    layout differs. Returns the files written - possibly none, since
    like everything thumbnail-related this is best-effort.
    """
    written: list[Path] = []
    thumb_target, poster_target = jellyfin_paths(render_path)
    source = thumbnail or thumbnail_path(render_path)
    try:
        if source.is_file():
            shutil.copyfile(source, thumb_target)
            written.append(thumb_target)
    except OSError:
        logger.warning("could not write %s", thumb_target, exc_info=True)
    poster = write_poster(poster_target, spec)
    if poster is not None:
        written.append(poster)
    return written


def thumbnail_path(render_path: Path) -> Path:
    """Sidecar path for a render, alongside `.youtube.json`/`.chapters.txt`."""
    return render_path.with_suffix(render_path.suffix + ".thumbnail.jpg")


def jellyfin_paths(render_path: Path) -> tuple[Path, Path]:
    """`(<name>-thumb.jpg, <name>-poster.jpg)` for a render."""
    stem = render_path.with_suffix("")
    return (
        stem.with_name(stem.name + "-thumb.jpg"),
        stem.with_name(stem.name + "-poster.jpg"),
    )


def _write(target: Path, builder, spec: ThumbnailSpec, subject: Path):
    """Build with `builder(spec)` and save as JPEG under the size cap.

    Failures are logged and swallowed: images are a nicety, and a render
    that took 40 minutes must not be marked failed because a font was
    missing. Quality steps down until the file fits YouTube's 2 MiB cap -
    at these sizes even q=92 is a few hundred KB, so this is belt and
    braces rather than an expected path.
    """
    try:
        img = builder(spec)
        for quality in (92, 85, 75, 60):
            img.save(target, "JPEG", quality=quality, optimize=True)
            if target.stat().st_size <= MAX_THUMB_BYTES:
                break
        logger.info("wrote %s (%d KiB)", target, target.stat().st_size // 1024)
        return target
    except Exception:
        logger.warning("could not write image for %s", subject, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


def _backdrop_layer(
    frame: Optional[np.ndarray],
    width: int,
    height: int,
    *,
    zoom: float = BACKDROP_ZOOM,
    crop_bias: float = BACKDROP_CROP_BIAS,
) -> Image.Image:
    """Darkened, slightly blurred cover-crop of `frame`, or flat charcoal."""
    if frame is None:
        return Image.new("RGB", (width, height), (26, 22, 38))
    try:
        src = Image.fromarray(np.asarray(frame, dtype=np.uint8), "RGB")
        # Cover-crop rather than squash: the source is 16:9 already, but
        # a differently-shaped render shouldn't distort players - and the
        # portrait poster crops a 16:9 frame hard by definition.
        scale = max(width / src.width, height / src.height) * zoom
        src = src.resize(
            (max(width, round(src.width * scale)), max(height, round(src.height * scale))),
            Image.LANCZOS,
        )
        left = (src.width - width) // 2
        top = int((src.height - height) * crop_bias)
        src = src.crop((left, top, left + width, top + height))
        src = src.filter(ImageFilter.GaussianBlur(BACKDROP_BLUR))
        arr = np.asarray(src, dtype=np.float32) * (1.0 - BACKDROP_DARKEN)
        return Image.fromarray(arr.astype(np.uint8), "RGB")
    except Exception:
        logger.warning("unusable backdrop frame; falling back to flat", exc_info=True)
        return Image.new("RGB", (width, height), (26, 22, 38))


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
# Portrait poster layers
# ---------------------------------------------------------------------------


def _draw_poster_wedges(img: Image.Image, spec: ThumbnailSpec, *, flat: bool) -> None:
    """Two team-color bands meeting at a slanted horizontal seam."""
    y_left = int(POSTER_H * POSTER_SEAM_LEFT)
    y_right = int(POSTER_H * POSTER_SEAM_RIGHT)

    layer = Image.new("RGBA", (POSTER_W, POSTER_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(
        [(0, 0), (POSTER_W, 0), (POSTER_W, y_right), (0, y_left)],
        fill=_rgba(spec.home_color, 255),
    )
    d.polygon(
        [(0, y_left), (POSTER_W, y_right), (POSTER_W, POSTER_H), (0, POSTER_H)],
        fill=_rgba(spec.away_color, 255),
    )
    if not flat:
        # Vertical ramp this time: strong at the top and bottom edges,
        # nearly clear across the middle band where the action is.
        ys = np.linspace(0.0, 1.0, POSTER_H, dtype=np.float32)
        edge = np.abs(ys - 0.5) * 2.0
        ramp = WEDGE_ALPHA_INNER + (WEDGE_ALPHA - WEDGE_ALPHA_INNER) * edge**1.5
        mask = np.tile((ramp * 255).astype(np.uint8)[:, None], (1, POSTER_W))
        alpha = np.asarray(layer.getchannel("A"), dtype=np.uint16) * mask // 255
        layer.putalpha(Image.fromarray(alpha.astype(np.uint8)))
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))


def _draw_poster_content(img: Image.Image, spec: ThumbnailSpec) -> None:
    """Caption, then home crest/name, "VS", away crest/name, subject."""
    cx = POSTER_W // 2
    home_y = int(POSTER_H * 0.21)
    away_y = int(POSTER_H * 0.72)

    home_disc = _logo_disc(spec.home_logo, POSTER_LOGO_D, photo=spec.home_is_photo)
    if home_disc is None and spec.home_badge:
        home_disc = _badge_disc(spec.home_badge, spec.home_color, POSTER_LOGO_D)
    for disc, cy in ((home_disc, home_y), (_logo_disc(spec.away_logo, POSTER_LOGO_D), away_y)):
        if disc is None:
            continue
        rgba = img.convert("RGBA")
        rgba.alpha_composite(
            disc, (cx - POSTER_LOGO_D // 2, cy - POSTER_LOGO_D // 2)
        )
        img.paste(rgba.convert("RGB"), (0, 0))

    d = ImageDraw.Draw(img, "RGBA")
    name_font = _font(64, bold=True)
    name_gap = POSTER_LOGO_D // 2 + 60
    _fit_text(d, (cx, home_y + name_gap), spec.home_name, name_font, POSTER_W - 80)
    _fit_text(d, (cx, away_y + name_gap), spec.away_name, name_font, POSTER_W - 80)

    # "VS" plate on the seam, between the two teams.
    text = spec.center_text or "VS"
    vs_font = _font(110, bold=True)
    vs_y = (home_y + away_y) // 2
    bbox = d.textbbox((cx, vs_y), text, font=vs_font, anchor="mm")
    d.rounded_rectangle(
        [bbox[0] - 40, bbox[1] - 18, bbox[2] + 40, bbox[3] + 18],
        radius=22,
        fill=(20, 16, 30, 205),
    )
    _text_with_shadow(d, (cx, vs_y), text, vs_font, anchor="mm")

    if spec.caption:
        d.rectangle([0, 0, POSTER_W, 96], fill=(12, 10, 20, 175))
        _fit_text(d, (cx, 48), spec.caption, _font(48, bold=True), POSTER_W - 40)
    if spec.subject:
        d.rectangle([0, POSTER_H - 120, POSTER_W, POSTER_H], fill=(12, 10, 20, 195))
        _fit_text(
            d,
            (cx, POSTER_H - 60),
            spec.subject,
            _font(60, bold=True),
            POSTER_W - 40,
        )


def _fit_text(draw, xy, text: str, font, max_width: int) -> None:
    """Centered text, shrunk until it fits `max_width`.

    The poster is only 1000px wide, so a long school name or tournament
    caption that fits the landscape thumbnail comfortably would run off
    both edges here.
    """
    if not text:
        return
    size = getattr(font, "size", 48)
    while size > 12:
        box = draw.textbbox(xy, text, font=font, anchor="mm")
        if (box[2] - box[0]) <= max_width:
            break
        size = int(size * 0.92)
        font = _font(size, bold=True)
    _text_with_shadow(draw, xy, text, font, anchor="mm")


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
