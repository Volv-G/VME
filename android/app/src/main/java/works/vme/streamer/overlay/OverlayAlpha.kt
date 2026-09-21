package works.vme.streamer.overlay

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode

/**
 * Knocks a finished overlay bitmap back to [OVERLAY_ALPHA].
 *
 * Every overlay we draw sits on top of live play, so all of them
 * are translucent to the same degree -- a scoreboard at 90% next to
 * a pop-up at 100% looks like a bug, not a hierarchy.
 *
 * ### Why one pass at the end, not per-paint
 *
 * `DST_IN` keeps the destination colour and multiplies its alpha by
 * the source's. Running it once over the completed bitmap fades
 * every element *together*, so text stays fully opaque against its
 * own background and only the whole card blends with the video.
 *
 * Painting each element at 90% instead would make them translucent
 * to each other: the card background would show through the
 * lettering, which is exactly the muddy look this avoids.
 *
 * ### Why not `filter.setAlpha()`
 *
 * `setAlpha` is how every overlay implements `hide()`. Using it for
 * a constant translucency too would mean the two fight -- any
 * `hide()`/`show()` would have to remember to restore 0.90 rather
 * than 1.0, and one missed call leaves an overlay permanently
 * opaque or permanently invisible.
 */
object OverlayAlpha {

    /** 10% transparent. Enough to see the court through an overlay,
     *  not enough to make text work for its legibility. */
    const val OVERLAY_ALPHA = 0.90f

    private val paint = Paint()

    /** Apply to the full [width] x [height] extent of [canvas]. */
    fun apply(canvas: Canvas, width: Int, height: Int) {
        paint.xfermode = PorterDuffXfermode(PorterDuff.Mode.DST_IN)
        paint.color = Color.argb((255 * OVERLAY_ALPHA).toInt(), 255, 255, 255)
        canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), paint)
        paint.xfermode = null
    }
}
