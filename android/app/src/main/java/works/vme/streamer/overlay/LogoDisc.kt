package works.vme.streamer.overlay

import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader

/**
 * A team badge as a circle: what every overlay draws in place of the
 * raw image.
 *
 * Badges arrive in every shape -- a crest cut out on transparency, a
 * square club logo on a coloured field, a phone photo of a jersey --
 * and drawn as they come they read as a jumble of rectangles along the
 * scoreboard. A disc is the one shape all of them can share.
 *
 * Two treatments, picked from the image itself, as VME's renderer does
 * (`_logo_disc` in `render/overlays/scoreboard.py`):
 *
 * - **Opaque**: scaled to cover the circle and centre-cropped, so the
 *   disc is filled edge to edge and the square background never shows.
 * - **Transparent**: a crest on transparency, or a crop zoomed out
 *   past the image's edges (see `LogoCropActivity`). Fitted whole
 *   inside the circle on a white plate. Covering would cut a crest's
 *   points off, and without the plate a transparent crest would not
 *   look like a disc at all -- the circle has to be visible to be one.
 *   White because most crests are designed against it, and it is what
 *   the stream thumbnail already used.
 */
object LogoDisc {

    /** Fraction of pixels that must be see-through for the image to
     *  count as transparent. Matches VME's `LOGO_ALPHA_FRACTION`. */
    private const val ALPHA_FRACTION = 0.02f

    /** A crest on the plate is fitted into this fraction of the
     *  diameter: inside the circle's inscribed square, so a square
     *  crest keeps its corners. */
    private const val CONTAIN_FRAC = 0.74f

    fun of(src: Bitmap, diameter: Int): Bitmap {
        val d = diameter.coerceAtLeast(8)
        val out = Bitmap.createBitmap(d, d, Bitmap.Config.ARGB_8888)
        val c = Canvas(out)
        val r = d / 2f
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)

        if (hasTransparency(src)) {
            paint.color = Color.WHITE
            c.drawCircle(r, r, r, paint)
            val box = d * CONTAIN_FRAC
            val scale = minOf(box / src.width, box / src.height)
            val w = src.width * scale
            val h = src.height * scale
            c.drawBitmap(src, null, RectF(r - w / 2f, r - h / 2f, r + w / 2f, r + h / 2f), paint)
        } else {
            // A shader-filled circle rather than draw-then-mask: one
            // pass, and the edge is anti-aliased by the circle itself.
            val scale = maxOf(d.toFloat() / src.width, d.toFloat() / src.height)
            val m = Matrix().apply {
                postScale(scale, scale)
                postTranslate((d - src.width * scale) / 2f, (d - src.height * scale) / 2f)
            }
            paint.shader = BitmapShader(src, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP)
                .apply { setLocalMatrix(m) }
            c.drawCircle(r, r, r, paint)
        }
        return out
    }

    /** Sampled rather than scanned: a 256px badge is 65k pixels and
     *  every 4th in each direction is plenty to tell a crest from a
     *  photo. */
    private fun hasTransparency(b: Bitmap): Boolean {
        if (!b.hasAlpha()) return false
        var seen = 0
        var clear = 0
        val step = 4
        var y = 0
        while (y < b.height) {
            var x = 0
            while (x < b.width) {
                if (Color.alpha(b.getPixel(x, y)) < 250) clear++
                seen++
                x += step
            }
            y += step
        }
        return seen > 0 && clear.toFloat() / seen > ALPHA_FRACTION
    }
}
