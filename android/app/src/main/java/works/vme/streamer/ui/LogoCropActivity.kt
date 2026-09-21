package works.vme.streamer.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import java.io.File

/**
 * Square crop for a team badge, before it is saved.
 *
 * ### Why this is hand-rolled
 *
 * A badge picked out of the gallery is rarely a badge: it is a
 * screenshot of a draw sheet, or a club photo with the crest in one
 * corner. The overlays draw it in a square box with aspect
 * preserved, so an uncropped 16:9 screenshot becomes a thin strip
 * with the crest invisible somewhere inside it.
 *
 * The two alternatives were worse. `com.android.camera.action.CROP`
 * is not part of the platform API -- it is whatever the OEM gallery
 * happens to expose, and on a phone that does not expose it the
 * feature silently does nothing. A crop library (uCrop and friends)
 * is a dependency and a few hundred KB for one screen the app uses
 * once per match.
 *
 * ### The interaction
 *
 * The frame is fixed and the image moves behind it -- the same model
 * as every avatar cropper, and the one that needs no handles to
 * drag. Pinch to zoom, drag to pan, and the image is clamped so it
 * always covers the frame, which makes a transparent-edged crop
 * impossible to produce by accident.
 *
 * Output is PNG: club crests routinely have transparent
 * backgrounds, and JPEG would fill those with black.
 */
class LogoCropActivity : ComponentActivity() {

    private lateinit var cropView: CropView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The typed overload since 33; the old one is deprecated and
        // does no type checking at all, which on a wrong extra means
        // a ClassCastException at the call site instead of a null.
        val uri = (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
            intent.getParcelableExtra(EXTRA_SOURCE, Uri::class.java)
        else
            @Suppress("DEPRECATION") intent.getParcelableExtra<Uri>(EXTRA_SOURCE))
            ?: run { finish(); return }
        val src = decode(uri) ?: run {
            setResult(Activity.RESULT_CANCELED)
            finish(); return
        }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
        }

        root.addView(TextView(this).apply {
            text = "Pinch to zoom, drag to position"
            setTextColor(Color.parseColor("#B0B0B0"))
            textSize = 13f
            gravity = Gravity.CENTER
            setPadding(24, 24, 24, 12)
        })

        cropView = CropView(this, src)
        root.addView(cropView, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0,
        ).apply { weight = 1f })

        val buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(24, 12, 24, 24)
        }
        buttons.addView(Button(this).apply {
            text = "Cancel"
            setOnClickListener {
                setResult(Activity.RESULT_CANCELED)
                finish()
            }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        buttons.addView(Button(this).apply {
            text = "Use logo"
            setOnClickListener { save() }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(buttons)

        setContentView(root)
        root.applySystemBarInsets()
    }

    private fun save() {
        val out = runCatching { cropView.crop(OUT_PX) }.getOrNull()
        if (out == null) {
            setResult(Activity.RESULT_CANCELED)
            finish(); return
        }
        // Handed over as a file rather than in the Intent: a 512px
        // PNG is comfortably past the ~1MB Binder transaction limit
        // that a Bitmap extra would have to pass through.
        val f = File(cacheDir, "logo-crop.png")
        val ok = runCatching {
            f.outputStream().use { out.compress(Bitmap.CompressFormat.PNG, 100, it) }
        }.isSuccess
        if (!ok) {
            setResult(Activity.RESULT_CANCELED)
            finish(); return
        }
        setResult(Activity.RESULT_OK, Intent().putExtra(EXTRA_RESULT_PATH, f.absolutePath))
        finish()
    }

    /**
     * Decode the picked image, downscaled.
     *
     * A modern phone photo is 12MP; held at full size alongside the
     * crop output it is the one allocation on this screen big enough
     * to matter. [MAX_SRC_PX] is still several times the 512px that
     * comes out, so nothing visible is lost.
     */
    private fun decode(uri: Uri): Bitmap? = runCatching {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, bounds)
        }
        val longest = maxOf(bounds.outWidth, bounds.outHeight)
        var sample = 1
        while (longest / (sample * 2) >= MAX_SRC_PX && sample < 32) sample *= 2
        contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(
                it, null, BitmapFactory.Options().apply { inSampleSize = sample },
            )
        }
    }.getOrNull()

    // ---- the view ---------------------------------------------------

    /**
     * The image, a fixed square frame, and a scrim over everything
     * outside it.
     */
    private class CropView(context: Context, private val src: Bitmap) : View(context) {

        private val matrix = Matrix()
        private val frame = RectF()
        private var ready = false

        private val bmpPaint = Paint().apply {
            isAntiAlias = true
            isFilterBitmap = true
        }
        private val scrimPaint = Paint().apply { color = Color.argb(170, 0, 0, 0) }
        private val framePaint = Paint().apply {
            color = Color.WHITE
            style = Paint.Style.STROKE
            strokeWidth = 3f
            isAntiAlias = true
        }

        private val scaleDetector = ScaleGestureDetector(
            context,
            object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
                override fun onScale(d: ScaleGestureDetector): Boolean {
                    matrix.postScale(
                        d.scaleFactor, d.scaleFactor, d.focusX, d.focusY,
                    )
                    clamp()
                    invalidate()
                    return true
                }
            },
        )
        private var lastX = 0f
        private var lastY = 0f

        override fun onSizeChanged(w: Int, h: Int, ow: Int, oh: Int) {
            val side = minOf(w, h) * FRAME_FRAC
            val cx = w / 2f
            val cy = h / 2f
            frame.set(cx - side / 2f, cy - side / 2f, cx + side / 2f, cy + side / 2f)

            // Start covering the frame, centred: the first thing the
            // operator sees is a valid crop, not letterboxing they
            // have to zoom out of.
            val scale = maxOf(side / src.width, side / src.height)
            matrix.reset()
            matrix.postScale(scale, scale)
            matrix.postTranslate(
                cx - src.width * scale / 2f,
                cy - src.height * scale / 2f,
            )
            ready = true
            invalidate()
        }

        override fun onDraw(canvas: Canvas) {
            if (!ready) return
            canvas.drawBitmap(src, matrix, bmpPaint)
            // Scrim as four rectangles around the frame. Cheaper and
            // more predictable than a clipped layer, and it does not
            // need a saveLayer on every frame of a pinch.
            val w = width.toFloat()
            val h = height.toFloat()
            canvas.drawRect(0f, 0f, w, frame.top, scrimPaint)
            canvas.drawRect(0f, frame.bottom, w, h, scrimPaint)
            canvas.drawRect(0f, frame.top, frame.left, frame.bottom, scrimPaint)
            canvas.drawRect(frame.right, frame.top, w, frame.bottom, scrimPaint)
            canvas.drawRect(frame, framePaint)
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            scaleDetector.onTouchEvent(event)
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    lastX = event.x; lastY = event.y
                }
                MotionEvent.ACTION_MOVE -> if (!scaleDetector.isInProgress) {
                    matrix.postTranslate(event.x - lastX, event.y - lastY)
                    lastX = event.x; lastY = event.y
                    clamp()
                    invalidate()
                }
                // A second finger landing or leaving moves the anchor;
                // without resetting it the image jumps by the distance
                // between the old finger and the new one.
                MotionEvent.ACTION_POINTER_DOWN,
                MotionEvent.ACTION_POINTER_UP -> {
                    lastX = event.x; lastY = event.y
                }
            }
            return true
        }

        /**
         * Keep the image covering the frame.
         *
         * Scale first -- if the image has been pinched smaller than
         * the frame no translation can cover it -- then nudge each
         * axis back inside. Means the crop can never contain an edge
         * of nothing, so there is no invalid state to warn about.
         */
        private fun clamp() {
            val v = FloatArray(9)
            matrix.getValues(v)
            val scale = v[Matrix.MSCALE_X]
            val min = maxOf(frame.width() / src.width, frame.height() / src.height)
            if (scale < min) {
                val f = min / scale
                matrix.postScale(f, f, frame.centerX(), frame.centerY())
                matrix.getValues(v)
            }
            val left = v[Matrix.MTRANS_X]
            val top = v[Matrix.MTRANS_Y]
            val right = left + src.width * v[Matrix.MSCALE_X]
            val bottom = top + src.height * v[Matrix.MSCALE_Y]

            var dx = 0f
            var dy = 0f
            if (left > frame.left) dx = frame.left - left
            else if (right < frame.right) dx = frame.right - right
            if (top > frame.top) dy = frame.top - top
            else if (bottom < frame.bottom) dy = frame.bottom - bottom
            if (dx != 0f || dy != 0f) matrix.postTranslate(dx, dy)
        }

        /** The framed region, rendered at [size] square. */
        fun crop(size: Int): Bitmap {
            val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
            val c = Canvas(out)
            // View matrix, shifted so the frame's corner is the
            // origin, then scaled to the output. One concatenation,
            // so the crop is resampled once rather than twice.
            val m = Matrix(matrix)
            m.postTranslate(-frame.left, -frame.top)
            m.postScale(size / frame.width(), size / frame.height())
            c.drawBitmap(src, m, bmpPaint)
            return out
        }
    }

    companion object {
        private const val EXTRA_SOURCE = "source_uri"
        const val EXTRA_RESULT_PATH = "result_path"

        /** Crop frame as a fraction of the view's shorter side. */
        private const val FRAME_FRAC = 0.78f
        /** Longest edge the source is decoded at. */
        private const val MAX_SRC_PX = 1600
        /** Output size. Comfortably above the ~50px the scoreboard
         *  draws a badge at and the ~90px the centre card does. */
        private const val OUT_PX = 512

        fun intent(context: Context, source: Uri): Intent =
            Intent(context, LogoCropActivity::class.java)
                .putExtra(EXTRA_SOURCE, source)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
}
