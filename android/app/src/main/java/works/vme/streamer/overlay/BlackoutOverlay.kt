package works.vme.streamer.overlay

import android.graphics.Bitmap
import android.graphics.Color
import android.util.Log
import com.pedro.encoder.input.gl.render.filters.`object`.ImageObjectFilterRender

/**
 * Flat black over the whole frame, on a switch.
 *
 * The bottom of [works.vme.streamer.data.StreamQuality]: when the
 * venue's wifi cannot carry video, the camera is painted out and the
 * stream becomes the scoreboard and the pop-ups. Two things make that
 * nearly free on the wire -- the encoder sees an unchanging picture,
 * so inter-frame compression has almost nothing to send, and what
 * little it does send is a solid colour.
 *
 * Implemented as a filter rather than by stopping the video source,
 * because the overlays *are* filters: they composite onto the camera
 * frame, and with no frame arriving there is nothing to composite
 * onto. Blacking out the bottom layer keeps the pipeline running
 * exactly as it does when the picture is up.
 *
 * Like every other overlay here it must be added to the GL interface
 * before `startStream`, and it must be added FIRST so the scoreboard,
 * pop-up and card filters draw over it rather than under it.
 */
class BlackoutOverlay {

    val filter: ImageObjectFilterRender = ImageObjectFilterRender()

    /**
     * Load the texture and start hidden.
     *
     * 2x2 pixels stretched over the frame: the image is one flat
     * colour, so a full-size bitmap would be megabytes of identical
     * black. GL scales it for nothing.
     *
     * The explicit `setAlpha(0f)` matters -- `setImage` is what makes
     * the filter drawable at all, so without it the first frame after
     * attach would be black.
     */
    fun attach() {
        val bmp = Bitmap.createBitmap(2, 2, Bitmap.Config.ARGB_8888).also {
            it.setHasAlpha(false)
            it.eraseColor(Color.BLACK)
        }
        filter.setScale(100f, 100f)
        filter.setPosition(0f, 0f)
        filter.setImage(bmp)
        filter.setAlpha(0f)
        Log.i(TAG, "attached (hidden)")
    }

    /** True = camera hidden, overlays only. */
    fun setShown(shown: Boolean) {
        filter.setAlpha(if (shown) 1f else 0f)
        Log.i(TAG, if (shown) "video blacked out" else "video visible")
    }

    private companion object {
        const val TAG = "BlackoutOverlay"
    }
}
