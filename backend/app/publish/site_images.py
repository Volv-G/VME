"""Image helpers for the site."""

from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


def watermark(path, px: int = 480):
    """The page-background version of a team crest (see publish/site.py):
    what's behind the artwork - a square's corners, a white disc it sits
    on - taken out, the linework kept. Faded behind a page, a crest on its
    own white disc otherwise shows as a grey blob. None if nothing is left."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
    im.thumbnail((px, px))
    w, h = im.size
    # 1. Whatever surrounds the artwork, reached from the corners.
    marker = (255, 0, 255, 0)
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if im.getpixel(xy)[3] > 0:
            ImageDraw.floodfill(im, xy, marker, thresh=150)
    px_ = im.load()
    for y in range(h):
        for x in range(w):
            if px_[x, y] == marker:
                px_[x, y] = (0, 0, 0, 0)
    # ...and the few pixels of edge between it and the artwork (a rim, the
    # blur of a JPEG), which would otherwise draw the outline of the ground.
    gone = im.getchannel("A").point(lambda v: 255 if v == 0 else 0).filter(ImageFilter.MaxFilter(7))
    im.putalpha(ImageChops.subtract(im.getchannel("A"), gone))
    # 2. Artwork on a light ground (a white disc or square): light means
    #    background, so opacity follows darkness. A crest already cut out
    #    is left alone.
    opaque = [p for p in im.getdata() if p[3] > 200]
    if not opaque:
        return None
    lum = sorted((0.299 * r + 0.587 * g + 0.114 * b) for r, g, b, _ in opaque)
    if lum[len(lum) // 2] < 200:
        return im
    out = Image.new("RGBA", im.size)
    o = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px_[x, y]
            l = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            o[x, y] = (r, g, b, int(a * min(1.0, max(0.0, (1 - l) * 1.8))))
    return out
