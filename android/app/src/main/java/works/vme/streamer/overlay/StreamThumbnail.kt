package works.vme.streamer.overlay

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Typeface
import java.io.ByteArrayOutputStream

/**
 * The still YouTube shows before a broadcast starts.
 *
 * A port of `backend/app/render/thumbnail.py`'s landscape layout, so
 * a channel's live streams and its uploaded matches look like one
 * set: two team-coloured wedges meeting at a slanted seam, a circular
 * badge on each side, and a neutral "VS" plate in the middle with the
 * names under it. Never a score -- these go up before the match, and
 * on an upload it would spoil the result.
 *
 * Specifically the *flat* variant. VME darkens a frame lifted from
 * the render and fades the wedges over it, which is what makes an
 * uploaded match look like that match. There is no footage yet when
 * this is generated, so the colours are the whole design -- which is
 * exactly what `_draw_wedges(flat=True)` does when VME has no
 * backdrop either.
 *
 * Pure Canvas: no Pillow equivalent to reach for, and the layout is
 * a dozen shapes.
 */
object StreamThumbnail {

    /** YouTube's recommended size, and its minimum width. */
    const val W = 1280
    const val H = 720

    /**
     * Compose the thumbnail.
     *
     * Never throws for content reasons: a missing logo falls back to
     * an initials badge, and a blank name to an empty one. A stream
     * with a plain thumbnail is better than a stream that failed to
     * start because a crest would not decode.
     */
    fun build(
        homeName: String,
        awayName: String,
        homeColor: Int,
        awayColor: Int,
        homeLogo: Bitmap?,
        awayLogo: Bitmap?,
    ): Bitmap {
        val bmp = Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        drawWedges(c, homeColor, awayColor)

        val y = (H - LOGO_D) / 2f - 20f
        drawDisc(c, homeLogo, homeName, homeColor, W * 0.13f - LOGO_D / 2f, y)
        drawDisc(c, awayLogo, awayName, awayColor, W * 0.87f - LOGO_D / 2f, y)

        drawCentre(c, homeName, awayName)
        return bmp
    }

    /**
     * JPEG bytes for `thumbnails.set`, which rejects anything over
     * 2 MiB. A flat-colour 1280x720 lands around 100 KB at q90, so
     * the loop below is a guard rather than a routine step.
     */
    fun jpeg(bmp: Bitmap): ByteArray {
        var quality = 90
        while (true) {
            val out = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, quality, out)
            val bytes = out.toByteArray()
            if (bytes.size <= MAX_BYTES || quality <= 40) return bytes
            quality -= 15
        }
    }

    /**
     * Short form of a team name, for the disc when there is no crest.
     *
     * Initials for a multi-word name ("Bellevue Storm" -> "BS"), the
     * first three letters for a single word ("Eastlake" -> "EAS").
     * Digits are kept, because age-group sides are routinely "Test 3"
     * or "Rivers 14U" and dropping the number merges two teams that
     * the operator needs to tell apart.
     */
    fun abbreviate(name: String): String {
        val words = name.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
        if (words.isEmpty()) return ""
        if (words.size == 1) return words[0].take(3).uppercase()
        return words.take(3)
            .joinToString("") { w -> w.first().toString() }
            .uppercase()
    }

    // ---- pieces -------------------------------------------------------

    private val fill = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.FILL
    }
    private val bmpPaint = Paint().apply {
        isAntiAlias = true
        isFilterBitmap = true
    }
    private val textPaint = Paint().apply {
        isAntiAlias = true
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
    }

    /** Two colour wedges meeting at a seam with the scoreboard's lean. */
    private fun drawWedges(c: Canvas, homeColor: Int, awayColor: Int) {
        val xTop = W * SEAM_TOP
        val xBot = W * SEAM_BOTTOM

        fill.color = homeColor
        c.drawPath(
            Path().apply {
                moveTo(0f, 0f); lineTo(xTop, 0f)
                lineTo(xBot, H.toFloat()); lineTo(0f, H.toFloat()); close()
            },
            fill,
        )
        fill.color = awayColor
        c.drawPath(
            Path().apply {
                moveTo(xTop, 0f); lineTo(W.toFloat(), 0f)
                lineTo(W.toFloat(), H.toFloat()); lineTo(xBot, H.toFloat()); close()
            },
            fill,
        )
    }

    /**
     * A team's disc: the crest on white, or initials on the team
     * colour when there is no crest.
     *
     * White plate under a crest for the same reason VME uses one --
     * a dark crest on a dark jersey colour disappears, and most
     * crests are designed against white anyway.
     */
    private fun drawDisc(
        c: Canvas, logo: Bitmap?, name: String, color: Int, x: Float, y: Float,
    ) {
        val d = LOGO_D.toFloat()
        val cx = x + d / 2f
        val cy = y + d / 2f

        if (logo != null) {
            fill.color = Color.WHITE
            c.drawCircle(cx, cy, d / 2f, fill)
            // Contained, not cropped to the circle: a crest is a
            // shape, and cutting its corners off to fill a disc is
            // worse than leaving white around it.
            val pad = d * 0.13f
            val box = d - pad * 2f
            val scale = minOf(box / logo.width, box / logo.height)
            val w = logo.width * scale
            val h = logo.height * scale
            c.drawBitmap(
                logo, null,
                RectF(cx - w / 2f, cy - h / 2f, cx + w / 2f, cy + h / 2f),
                bmpPaint,
            )
            return
        }

        // Fallback: white ring, team-coloured field, initials.
        fill.color = Color.WHITE
        c.drawCircle(cx, cy, d / 2f, fill)
        fill.color = color
        c.drawCircle(cx, cy, d / 2f - d * 0.045f, fill)

        val text = abbreviate(name)
        if (text.isEmpty()) return
        textPaint.color = Color.WHITE
        // Shrink to fit rather than pick a size: "BS" and "EAS" want
        // very different points to fill the same disc.
        var size = d * 0.42f
        val usable = d * 0.62f
        val bounds = Rect()
        while (size > 8f) {
            textPaint.textSize = size
            textPaint.getTextBounds(text, 0, text.length, bounds)
            if (bounds.width() <= usable && bounds.height() <= usable) break
            size *= 0.9f
        }
        val fm = textPaint.fontMetrics
        c.drawText(text, cx, cy - (fm.ascent + fm.descent) / 2f, textPaint)
    }

    /** "VS" on a dark plate, with both names under it. */
    private fun drawCentre(c: Canvas, homeName: String, awayName: String) {
        val cx = W / 2f
        val cy = H / 2f - 40f

        textPaint.color = Color.WHITE
        textPaint.textSize = 150f
        val vs = "VS"
        val bounds = Rect()
        textPaint.getTextBounds(vs, 0, vs.length, bounds)

        // A plate keeps it legible whatever the two team colours are,
        // including the case where one of them is near-white.
        fill.color = Color.argb(205, 20, 16, 30)
        val padX = 44f
        val padY = 20f
        c.drawRoundRect(
            RectF(
                cx - bounds.width() / 2f - padX, cy - bounds.height() / 2f - padY,
                cx + bounds.width() / 2f + padX, cy + bounds.height() / 2f + padY,
            ),
            24f, 24f, fill,
        )
        val fm = textPaint.fontMetrics
        c.drawText(vs, cx, cy - (fm.ascent + fm.descent) / 2f, textPaint)

        val namesY = cy + bounds.height() / 2f + padY + 60f

        // One size for both names, sized so the longer one clears its
        // disc. There is about 294px between a disc's inner edge and
        // the centre gutter at 1280 wide; a two-word club name at the
        // nominal 52pt is half as wide again, so this is a fit that
        // routinely applies, not a safety net for a pathological case.
        // Both sides share a size because two names at visibly
        // different sizes reads as a mistake.
        val room = (cx - 30f) - (W * 0.13f + LOGO_D / 2f) - 12f
        textPaint.textSize = 52f
        val widest = maxOf(
            textPaint.measureText(homeName.uppercase()),
            textPaint.measureText(awayName.uppercase()),
        )
        if (widest > room) {
            textPaint.textSize = (52f * room / widest).coerceAtLeast(24f)
        }
        textPaint.textAlign = Paint.Align.RIGHT
        c.drawText(homeName.uppercase(), cx - 30f, namesY, textPaint)
        textPaint.textAlign = Paint.Align.CENTER
        c.drawText("|", cx, namesY, textPaint)
        textPaint.textAlign = Paint.Align.LEFT
        c.drawText(awayName.uppercase(), cx + 30f, namesY, textPaint)
        textPaint.textAlign = Paint.Align.CENTER
    }

    /** Seam position at the top and bottom edge, as a fraction of
     *  width. Matches `thumbnail.py`'s SEAM_TOP / SEAM_BOTTOM, which
     *  in turn match the scoreboard's dividers. */
    private const val SEAM_TOP = 0.56f
    private const val SEAM_BOTTOM = 0.44f
    private const val LOGO_D = 300
    /** `thumbnails.set` rejects anything larger. */
    private const val MAX_BYTES = 2 * 1024 * 1024
}
