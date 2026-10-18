package works.vme.streamer.overlay

import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Shader
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.sqrt

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
 *
 * The fit is to the crest itself, not to its image. Fitting the image
 * box into the circle's inscribed square left a round crest at under
 * three quarters of the disc, and any transparent margin in the file -
 * most club PNGs have one, and every zoomed-out crop is one - shrank it
 * further, so badges sat small in the middle of a white plate. Instead
 * the visible pixels are found, centred, and scaled until all but the
 * outermost 1% of them sit within [CONTENT_FILL] of the radius: a round
 * crest fills the disc, and a wide one is not held small for the sake
 * of an ear tip, which may just meet the rim. VME's renderer fits the
 * same way (`_fit_crest`), so a badge is one size on stream and render.
 */
object LogoDisc {

    /** Fraction of pixels that must be see-through for the image to
     *  count as transparent. Matches VME's `LOGO_ALPHA_FRACTION`. */
    private const val ALPHA_FRACTION = 0.02f

    /** How far out a crest reaches on the plate, as a fraction of the
     *  radius, measured at [REACH_PERCENTILE] of its visible pixels. */
    private const val CONTENT_FILL = 0.96f

    /** The share of a crest's visible pixels that must sit within
     *  [CONTENT_FILL]; the rest - tips, whiskers - may reach the rim. */
    private const val REACH_PERCENTILE = 0.99f

    /** Alpha at which a pixel counts as part of the crest. Low, so a
     *  soft edge counts, but above the faint haze some exports leave
     *  across the whole transparent field. */
    private const val SOLID_ALPHA = 24

    fun of(src: Bitmap, diameter: Int): Bitmap {
        val d = diameter.coerceAtLeast(8)
        val out = Bitmap.createBitmap(d, d, Bitmap.Config.ARGB_8888)
        val c = Canvas(out)
        val r = d / 2f
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)

        if (hasTransparency(src)) {
            paint.color = Color.WHITE
            c.drawCircle(r, r, r, paint)
            // Clipped to the plate, so a glow or shadow fainter than
            // SOLID_ALPHA cannot spill past its edge.
            c.save()
            c.clipPath(Path().apply { addCircle(r, r, r, Path.Direction.CW) })
            c.drawBitmap(src, fitToCircle(src, r), paint)
            c.restore()
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

    /**
     * Where to draw [src] so its visible pixels are centred on the disc
     * of radius [r], [REACH_PERCENTILE] of them within [CONTENT_FILL].
     *
     * Every pixel is read: the badge arrives decoded at overlay size
     * (a few hundred pixels a side), and this runs when the overlays
     * are built, not per frame.
     */
    private fun fitToCircle(src: Bitmap, r: Float): Matrix {
        val w = src.width
        val h = src.height
        val px = IntArray(w * h)
        src.getPixels(px, 0, w, 0, 0, w, h)
        fun solid(i: Int) = (px[i] ushr 24) >= SOLID_ALPHA

        var minX = w; var minY = h; var maxX = -1; var maxY = -1
        for (y in 0 until h) {
            val row = y * w
            for (x in 0 until w) {
                if (!solid(row + x)) continue
                if (x < minX) minX = x
                if (x > maxX) maxX = x
                if (y < minY) minY = y
                if (y > maxY) maxY = y
            }
        }
        if (maxX < 0) {
            // Nothing visible at all: just fit the image box.
            val scale = 2f * r * CONTENT_FILL / max(w, h)
            return Matrix().apply {
                postTranslate(-w / 2f, -h / 2f)
                postScale(scale, scale)
                postTranslate(r, r)
            }
        }
        val cx = (minX + maxX + 1) / 2f
        val cy = (minY + maxY + 1) / 2f
        // How far the crest reaches from that centre, by each visible
        // pixel's farthest corner, ignoring the outermost few tips.
        val dist = FloatArray(w * h)
        var n = 0
        for (y in minY..maxY) {
            val row = y * w
            val dy = max(abs(y - cy), abs(y + 1 - cy))
            for (x in minX..maxX) {
                if (!solid(row + x)) continue
                val dx = max(abs(x - cx), abs(x + 1 - cx))
                dist[n++] = sqrt(dx * dx + dy * dy)
            }
        }
        java.util.Arrays.sort(dist, 0, n)
        val far = dist[((n - 1) * REACH_PERCENTILE).toInt()]
        val scale = r * CONTENT_FILL / far.coerceAtLeast(1f)
        return Matrix().apply {
            postTranslate(-cx, -cy)
            postScale(scale, scale)
            postTranslate(r, r)
        }
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
