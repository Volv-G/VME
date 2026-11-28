"""Clean up a badge found online (club_lookup) before it becomes a club's.

Everything comes out as RGBA PNG - an alpha channel always, so a badge
sits on any colour (the scoreboard, the site's crests, the phone's
overlay) without a box around it. On top of that, optionally:

* **remove the background** - the colour around the edge is taken as the
  background and everything close to it that touches the edge is made
  transparent, with the rim softened and the background's tint taken
  out of it, so white logos don't come out with a grey fringe. Areas
  enclosed by the badge (the hole of an "O") stay unless `holes` asks
  for them too;
* **trim** the transparent margin;
* **square** it, padding with transparency.

Also: the main colours of a badge (`palette`) and colour names as
Wikipedia writes them ("Navy Blue, Red, and White") turned into hex.
"""

from __future__ import annotations

import io
import re
from typing import Optional

import numpy as np
from PIL import Image, ImageColor, ImageOps

# Badges are shown at most a couple of hundred pixels across; bigger
# only slows the background removal down.
MAX_SIDE = 512


def load(data: bytes) -> Image.Image:
    """Any image Pillow reads, upright, as RGBA, at most MAX_SIDE."""
    img = Image.open(io.BytesIO(data))
    img.load()
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGBA")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return img


def png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("RGBA").save(out, "PNG", optimize=True)
    return out.getvalue()


def process(
    img: Image.Image,
    *,
    remove_bg: bool = False,
    tolerance: int = 40,
    holes: bool = False,
    trim: bool = False,
    square: bool = False,
) -> Image.Image:
    img = img.convert("RGBA")
    if remove_bg:
        img = remove_background(img, tolerance=tolerance, holes=holes)
    if trim:
        img = trim_margin(img)
    if square:
        img = make_square(img)
    return img


def _grow(region: np.ndarray) -> np.ndarray:
    """`region` and its four-way neighbours."""
    g = region.copy()
    g[1:, :] |= region[:-1, :]
    g[:-1, :] |= region[1:, :]
    g[:, 1:] |= region[:, :-1]
    g[:, :-1] |= region[:, 1:]
    return g


def remove_background(img: Image.Image, *, tolerance: int = 40, holes: bool = False) -> Image.Image:
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    rgb, alpha = a[..., :3], a[..., 3]
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    solid = border[border[:, 3] > 200]
    # Already transparent around the edge: nothing to take away.
    if len(solid) < 0.5 * len(border):
        return img
    bg = np.median(solid[:, :3], axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=-1))
    tol = max(1.0, float(tolerance))
    close = (dist <= tol) & (alpha > 0)

    if holes:
        region = close
    else:
        # Flood from the edge through pixels close to the background.
        region = np.zeros_like(close)
        region[0, :] = close[0, :]
        region[-1, :] = close[-1, :]
        region[:, 0] |= close[:, 0]
        region[:, -1] |= close[:, -1]
        while True:
            before = region
            for _ in range(8):
                region = _grow(region) & close
            if np.array_equal(region, before):
                break

    out = a.copy()
    out[..., 3][region] = 0
    # The rim: a couple of pixels into the badge, blended with the
    # background when the image was drawn. Fade them by how close they
    # are to the background and take its tint back out.
    rim = _grow(_grow(region)) & ~region
    if rim.any():
        soft = 1.5 * tol
        keep = np.clip((dist[rim] - tol) / soft, 0.0, 1.0)
        f = np.maximum(keep, 0.05)[:, None]
        out[..., :3][rim] = np.clip(bg + (rgb[rim] - bg) / f, 0, 255)
        out[..., 3][rim] = alpha[rim] * keep
    return Image.fromarray(out.round().astype(np.uint8), "RGBA")


def trim_margin(img: Image.Image, pad: float = 0.03) -> Image.Image:
    box = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if not box:
        return img
    img = img.crop(box)
    p = max(1, round(max(img.size) * pad))
    framed = Image.new("RGBA", (img.width + 2 * p, img.height + 2 * p), (0, 0, 0, 0))
    framed.paste(img, (p, p))
    return framed


def make_square(img: Image.Image) -> Image.Image:
    side = max(img.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return sq


def _hex(rgb) -> str:
    r, g, b = (int(round(float(v))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def palette(img: Image.Image, n: int = 5) -> list[str]:
    """The badge's main colours, most used first, ignoring see-through
    pixels and colours too close to one already listed."""
    a = np.asarray(img.convert("RGBA"))
    px = a[a[..., 3] > 200][:, :3]
    if len(px) < 20:
        return []
    if len(px) > 20000:
        px = px[np.linspace(0, len(px) - 1, 20000).astype(int)]
    strip = Image.fromarray(px.reshape(1, -1, 3).astype(np.uint8), "RGB")
    q = strip.quantize(colors=10, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    out: list[tuple[int, int, int]] = []
    for count, idx in counts:
        if count / total < 0.03:
            continue
        rgb = tuple(pal[idx * 3: idx * 3 + 3])
        if any(sum((x - y) ** 2 for x, y in zip(rgb, o)) < 48 ** 2 for o in out):
            continue
        out.append(rgb)  # type: ignore[arg-type]
        if len(out) >= n:
            break
    return [_hex(c) for c in out]


# School colours CSS doesn't name, or names differently from how a
# school means them ("gold" in CSS is a lemon yellow).
_SCHOOL_COLORS = {
    "cardinal": "#c41e3a", "cardinal red": "#c41e3a", "vegas gold": "#c5b358",
    "old gold": "#cfb53b", "athletic gold": "#ffb81c", "gold": "#ffb81c",
    "kelly green": "#4cbb17", "kelly": "#4cbb17", "hunter green": "#355e3b",
    "columbia blue": "#9bddff", "carolina blue": "#4b9cd3", "navy blue": "#000080",
    "royal": "#4169e1", "scarlet": "#ff2400", "burgundy": "#800020",
    "cream": "#fffdd0", "copper": "#b87333", "bronze": "#cd7f32",
    "forest": "#228b22", "columbia": "#9bddff", "sky": "#87ceeb",
    "garnet": "#733635", "purple": "#4f2683", "maroon": "#800000",
    "crimson": "#a51c30", "grey": "#808080", "gray": "#808080",
}


def color_hex(name: str) -> Optional[str]:
    """"Navy Blue" / "navy" / "#002F6C" -> "#rrggbb"; None if unknown."""
    s = " ".join(name.casefold().replace("_", " ").split()).strip(" .;:-")
    if not s:
        return None
    m = re.fullmatch(r"#?([0-9a-f]{6}|[0-9a-f]{3})", s)
    if m:
        h = m.group(1)
        return "#" + ("".join(c * 2 for c in h) if len(h) == 3 else h)
    if s in _SCHOOL_COLORS:
        return _SCHOOL_COLORS[s]
    try:
        return _hex(ImageColor.getrgb(s.replace(" ", "")))
    except ValueError:
        return None


def colors_from_wikitext(text: str) -> list[str]:
    """An infobox's colours line -> hex, in its order.

    `{{color box|#002F6C}}` and `{{color box|navy}}` first, when there
    are any; otherwise the words ("Crimson and gray").
    """
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", " ", text, flags=re.S | re.I)
    out: list[str] = []
    for arg in re.findall(r"\{\{\s*colou?r\s*box\s*\|\s*([^|}]+)", text, flags=re.I):
        h = color_hex(arg)
        if h and h not in out:
            out.append(h)
    if out:
        return out
    plain = re.sub(r"\{\{.*?\}\}|<[^>]+>|\[\[|\]\]|\(.*?\)", " ", text)
    for word in re.split(r",|/|&|\band\b|\n", plain):
        h = color_hex(word)
        if h and h not in out:
            out.append(h)
    return out
