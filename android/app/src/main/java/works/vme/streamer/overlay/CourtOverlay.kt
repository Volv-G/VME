package works.vme.streamer.overlay

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Typeface
import android.util.Log
import com.pedro.encoder.input.gl.render.filters.`object`.ImageObjectFilterRender

/**
 * The whole frame as a court diagram: who is standing where.
 *
 * The bottom rung of [works.vme.streamer.data.StreamQuality]. When the
 * hall's wifi cannot carry video, the choice is not "video or nothing"
 * -- a still picture of the rotation, with faces and numbers, plus the
 * scoreboard and the pop-ups over it, tells a parent watching at home
 * more than a 300 kbps smear of a gym does. It is also nearly free to
 * send: the image only changes on a substitution or a rotation, so the
 * encoder spends its bits on the few frames that differ.
 *
 * Home only, like every other rotation surface in this app: the
 * opponent's line-up is not tracked, and half a diagram invented from
 * nothing would be worse than none.
 *
 * ### Canvas size
 *
 * Drawn at half the video's dimensions and scaled up by GL. The
 * content is flat shapes, large type and photographs that were never
 * sharp at 1080p anyway, so the softness is invisible, while a
 * full-frame ARGB bitmap per redraw would be 8 MB of allocation on
 * every substitution.
 */
class CourtOverlay(
    private val videoWidth: Int,
    private val videoHeight: Int,
    /** Team colour, used for the discs of players with no photo. */
    private val teamColor: Int,
) {

    /** One rotation slot, as the activity knows it. */
    data class Slot(
        val position: Int,
        val jersey: Int?,
        val name: String,
        val photo: Bitmap?,
        val libero: Boolean,
    )

    val filter: ImageObjectFilterRender = ImageObjectFilterRender()

    private val canvasW = (videoWidth / 2).coerceAtLeast(320)
    private val canvasH = (videoHeight / 2).coerceAtLeast(180)

    private var shown = false
    private var lastKey: String? = null

    // ---- geometry --------------------------------------------------
    //
    // The court is inset from the frame on every side: the scoreboard
    // bar sits across the bottom and the pop-ups slide in over the
    // lower right, and a diagram drawn under either would lose the
    // very numbers it exists to show.

    private val courtLeft = canvasW * 0.13f
    private val courtRight = canvasW * 0.87f
    private val courtTop = canvasH * 0.07f
    // The scoreboard bar occupies roughly the bottom tenth of the
    // frame (its height plus VME's 3% margin), so the court stops
    // above it rather than being drawn over.
    private val courtBottom = canvasH * 0.86f
    private val colW = (courtRight - courtLeft) / 3f
    private val rowH = (courtBottom - courtTop) / 2f
    private val faceR = minOf(colW, rowH) * 0.26f

    private val fillPaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.FILL
    }
    private val linePaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.STROKE
        color = Color.argb(150, 255, 255, 255)
        strokeWidth = (canvasH * 0.004f).coerceAtLeast(1.5f)
    }
    private val netPaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.STROKE
        color = Color.argb(230, 255, 255, 255)
        strokeWidth = (canvasH * 0.010f).coerceAtLeast(3f)
    }
    private val photoPaint = Paint().apply {
        isAntiAlias = true
        isFilterBitmap = true
    }
    private val ringPaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.STROKE
        color = Color.argb(220, 255, 255, 255)
        strokeWidth = (faceR * 0.09f).coerceAtLeast(1.5f)
    }
    private val servePaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.STROKE
        color = ScoreboardOverlay.SERVE_COLOR
        strokeWidth = (faceR * 0.16f).coerceAtLeast(2.5f)
    }
    private val numberPaint = Paint().apply {
        color = Color.WHITE
        isAntiAlias = true
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
        textSize = faceR * 0.90f
    }
    private val namePaint = Paint().apply {
        color = Color.argb(215, 235, 235, 235)
        isAntiAlias = true
        typeface = Typeface.DEFAULT
        textAlign = Paint.Align.CENTER
        textSize = faceR * 0.42f
    }
    // Deliberately no "P4"/"P5" captions. The diagram is for viewers,
    // not the operator: position numbers are scorers' jargon, and the
    // court's own geometry already says who is at the net.

    /**
     * Wire the filter in. Call once, right after
     * `glInterface.addFilter(court.filter)` and BEFORE `startStream` --
     * and after the blackout filter, so the diagram covers it.
     */
    fun attach() {
        filter.setScale(100f, 100f)
        filter.setPosition(0f, 0f)
        Log.i(TAG, "attached ${canvasW}x$canvasH")
    }

    /** Show or hide without redrawing. */
    fun setShown(on: Boolean) {
        shown = on
        runCatching { filter.setAlpha(if (on) 1f else 0f) }
    }

    /**
     * Redraw for the current line-up.
     *
     * Skips the work when nothing that shows has changed: this is
     * called from the same push as every other overlay, which runs on
     * every event, and most events do not move anybody.
     */
    fun update(slots: List<Slot>, servingPosition: Int?) {
        val key = slots.joinToString("|") {
            "${it.position}:${it.jersey}:${it.name}:${it.libero}:${it.photo != null}"
        } + "/$servingPosition"
        if (key == lastKey) return
        lastKey = key

        val bmp = Bitmap.createBitmap(canvasW, canvasH, Bitmap.Config.ARGB_8888)
            .also { it.setHasAlpha(false) }
        val c = Canvas(bmp)
        c.drawColor(BACKGROUND)

        drawCourt(c)
        for (slot in slots) {
            val (cx, cy) = centreOf(slot.position)
            drawSlot(c, slot, cx, cy, serving = slot.position == servingPosition)
        }

        filter.setImage(bmp)
        filter.setAlpha(if (shown) 1f else 0f)
    }

    // ---- drawing ---------------------------------------------------

    /**
     * The court itself: the home half, net at the top.
     *
     * The orientation every rotation grid in this app uses, and the one
     * the operator sees from behind their own bench -- P4/P3/P2 across
     * the front, P5/P6/P1 behind.
     */
    private fun drawCourt(c: Canvas) {
        fillPaint.color = COURT_FILL
        c.drawRect(courtLeft, courtTop, courtRight, courtBottom, fillPaint)
        c.drawRect(courtLeft, courtTop, courtRight, courtBottom, linePaint)
        // The attack line, three metres off the net: the boundary the
        // back row may not cross to hit, and the reason P5/P6/P1 stand
        // where they do.
        val attackY = courtTop + rowH
        c.drawLine(courtLeft, attackY, courtRight, attackY, linePaint)
        // The net, drawn heavier than the court lines and slightly
        // proud of the top edge so the half reads as a half.
        c.drawLine(courtLeft, courtTop, courtRight, courtTop, netPaint)
    }

    private fun centreOf(position: Int): Pair<Float, Float> {
        val col = when (position) {
            4, 5 -> 0
            3, 6 -> 1
            else -> 2          // 2 and 1
        }
        val row = if (position in FRONT_ROW) 0 else 1
        return (courtLeft + (col + 0.5f) * colW) to (courtTop + (row + 0.5f) * rowH)
    }

    private fun drawSlot(c: Canvas, slot: Slot, cx: Float, cy: Float, serving: Boolean) {
        // Face, number, name: one block, centred in the cell, so the
        // rows stay clear of each other and of the attack line however
        // the frame is proportioned.
        val blockH = 2 * faceR + numberPaint.textSize + namePaint.textSize * 1.3f
        val faceCy = cy - blockH / 2f + faceR
        val photo = slot.photo
        if (photo != null) {
            c.save()
            val clip = Path().apply {
                addCircle(cx, faceCy, faceR, Path.Direction.CW)
            }
            c.clipPath(clip)
            // Cover, not fit: a portrait cropped to the circle beats one
            // letterboxed inside it with bars nobody can explain.
            val side = (2 * faceR).toInt()
            val src = coverCrop(photo, side)
            c.drawBitmap(
                photo, src,
                RectF(cx - faceR, faceCy - faceR, cx + faceR, faceCy + faceR),
                photoPaint,
            )
            c.restore()
        } else {
            // No photo: the team-coloured disc used everywhere else,
            // with the number in it so the slot still identifies
            // somebody.
            fillPaint.color = if (slot.jersey == null) EMPTY_FILL else teamColor
            c.drawCircle(cx, faceCy, faceR, fillPaint)
        }
        c.drawCircle(cx, faceCy, faceR, ringPaint)
        if (serving) {
            // Who is about to serve, in the same yellow the scoreboard
            // uses for the serving team.
            c.drawCircle(cx, faceCy, faceR + servePaint.strokeWidth, servePaint)
        }

        val labelY = faceCy + faceR + numberPaint.textSize * 0.92f
        c.drawText(
            if (slot.jersey != null) "${slot.jersey}" else "–",
            cx, labelY, numberPaint,
        )
        val sub = when {
            slot.jersey == null -> "—"
            slot.libero -> "${slot.name} (L)".trim()
            else -> slot.name
        }
        if (sub.isNotBlank()) {
            c.drawText(sub, cx, labelY + namePaint.textSize * 1.35f, namePaint)
        }
    }

    /** Centre-square crop of [bmp], biased slightly up because these
     *  are head-and-shoulders portraits. */
    private fun coverCrop(bmp: Bitmap, @Suppress("UNUSED_PARAMETER") side: Int): Rect {
        val s = minOf(bmp.width, bmp.height)
        val left = (bmp.width - s) / 2
        val top = ((bmp.height - s) * 0.35f).toInt().coerceAtLeast(0)
        return Rect(left, top, left + s, top + s)
    }

    private companion object {
        const val TAG = "CourtOverlay"
        val FRONT_ROW = setOf(2, 3, 4)
        val BACKGROUND: Int = Color.rgb(14, 17, 22)
        /** Court blue, dark enough that white numbers stay readable
         *  over it at any bitrate. */
        val COURT_FILL: Int = Color.rgb(28, 47, 74)
        val EMPTY_FILL: Int = Color.rgb(44, 48, 56)
    }
}
