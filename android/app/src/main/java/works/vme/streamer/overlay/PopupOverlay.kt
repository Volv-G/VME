package works.vme.streamer.overlay

import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.Rect
import android.graphics.Shader
import android.graphics.Typeface
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.pedro.encoder.input.gl.render.filters.`object`.ImageObjectFilterRender
import works.vme.streamer.data.PhotoCache

/**
 * Event pop-up burned into the RTMP feed.
 *
 * Direct port of VME's `backend/app/render/overlays/message.py`, so a
 * live stream and a rendered match look like the same product:
 *
 * ```
 *                                    +-|------------------------+
 *                                    | | Kill                   |   <- bold title
 *                                    | | #12 Sarah M.           |   <- regular subtitle
 *                                    +-|------------------------+
 *                                      ^ 6px accent bar (bg * 0.6)
 * ```
 *
 * ### What matches VME
 *
 * - **Bottom-right** of the frame, clearing the scoreboard bar.
 * - Background is the **team colour** (so home and away pop-ups are
 *   visually distinct), falling back to VME's `(45, 35, 75)`.
 * - 6 px accent bar down the left at `bg * 0.6`.
 * - Title **bold**, subtitle **regular**, both at
 *   `videoHeight * 0.020`. VME deliberately renders them at the same
 *   size -- the hierarchy comes from weight, not scale, so an action
 *   label ("Kill") does not shout over the player info the viewer
 *   actually cares about.
 * - 16 px padding, 4 px line gap.
 * - Subtitle is `#NN First L.` from the roster.
 *
 * ### Player avatars
 *
 * Ported too. VME blits a circle-cropped photo at the left of the
 * pop-up at `AVATAR_SCALE = 1.9` times the text-block height, with a
 * 2 px white ring. Photos are shot standing, so a centred square crop
 * lands on the torso -- `AVATAR_CROP_BIAS = 0.22` biases the crop
 * window upward to catch the face.
 *
 * A player with no photo gets a **jersey-number disc** at `bg * 0.55`
 * instead. A substitution names two people and therefore always draws
 * two circles, so neither face can be mistaken for the other player's
 * -- that ambiguity, not the missing photo, is what VME's comment
 * calls out as the thing to avoid.
 *
 * Photos come from `PhotoCache`, populated at roster import. Nothing
 * here touches the network.
 *
 * ### What is dropped
 *
 * ### Slide in / slide out
 *
 * Ported from `message.py::_x_offset`: the pop-up slides in from
 * off the right edge over the first 10% of its life, sits still,
 * then slides back out over the last 10%. Easing matches VME --
 * `1-(1-t)^3` on the way in (decelerating, so it arrives softly)
 * and `t^3` on the way out (accelerating, so it leaves briskly).
 *
 * VME gets this for free: it renders per frame from a frame map and
 * just recomputes `x` each time. Here there is no render loop to
 * hook, so a `Handler` ticks at [FRAME_MS] and calls
 * `filter.setPosition(x, y)`. Only the position moves -- the bitmap
 * is uploaded once per pop-up, not per tick, because `setImage`
 * re-uploads a GL texture and doing that 30 times a second for an
 * animation that only translates would be wasteful.
 *
 * ### What is dropped
 *
 * - ~~Slide-in animation~~ -- VME animates `x` over the pop-up's life
 *   from a frame map. Here the pop-up is pushed as a single bitmap on
 *   an event, so it appears and disappears instantly.
 * - **Stacking.** VME stacks concurrent pop-ups upward. One filter
 *   means one pop-up; a new [show] replaces whatever is up.
 *
 * ### Fixed-size bitmap, hugging box
 *
 * `setScale` after attach reproducibly blanks these filters (see
 * [ScoreboardOverlay]), so the bitmap is allocated **once** at a
 * worst-case size and the scale is set once. Each [show] clears it
 * fully transparent and draws the real box hugging the bitmap's
 * bottom-right corner, so the box still *looks* like it hugs its
 * text the way VME's does.
 *
 * That relies on per-pixel alpha working. Reading the actual shader
 * (`encoder/src/main/res/raw/object_fragment.glsl`):
 *
 * ```glsl
 * gl_FragColor = mix(samplerPixel, objectPixel, objectPixel.a * uAlpha);
 * ```
 *
 * `objectPixel.a == 0` passes the video through untouched, so
 * transparency does work. (The scoreboard's KDoc claims otherwise;
 * that was almost certainly the recycled-bitmap bug wearing a
 * disguise, since a recycled texture reads as opaque black.)
 *
 * `ImageObjectFilterRender.setImage()` hands the bitmap to GL and the
 * texture loader drops the reference, so a fresh bitmap is allocated
 * per [show] rather than reusing one.
 */
class PopupOverlay(
    private val videoWidth: Int,
    private val videoHeight: Int,
    /** Height of the scoreboard bar in pixels, so the pop-up can sit
     *  clear of it. Pass 0 if there is no scoreboard. */
    scoreboardHeightPx: Int = 0,
) {
    private val handler = Handler(Looper.getMainLooper())
    val filter: ImageObjectFilterRender = ImageObjectFilterRender()

    // ---- VME constants (message.py) --------------------------------
    private val titleFontPx = videoHeight * TITLE_FONT_SCALE
    private val subtitleFontPx = videoHeight * SUBTITLE_FONT_SCALE
    private val padPx = (videoHeight * PADDING_SCALE).toInt().coerceAtLeast(8)
    private val lineGapPx = (videoHeight * LINE_GAP_SCALE).toInt().coerceAtLeast(2)
    private val accentPx = (videoHeight * ACCENT_SCALE).toInt().coerceAtLeast(3)

    private val titlePaint = Paint().apply {
        color = Color.WHITE
        isAntiAlias = true
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        textSize = titleFontPx
    }
    private val subtitlePaint = Paint().apply {
        color = Color.WHITE
        isAntiAlias = true
        typeface = Typeface.DEFAULT
        textSize = subtitleFontPx
    }
    private val fillPaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.FILL
    }
    private val avatarPaint = Paint().apply {
        isAntiAlias = true
        isFilterBitmap = true
    }
    private val ringPaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.STROKE
        color = Color.argb(235, 255, 255, 255)
        strokeWidth = (videoHeight * RING_SCALE).coerceAtLeast(1.5f)
    }
    private val badgePaint = Paint().apply {
        color = Color.WHITE
        isAntiAlias = true
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
    }
    private val avatarGapPx = (videoHeight * AVATAR_GAP_SCALE).toInt().coerceAtLeast(3)

    /**
     * Canvas size, fixed at construction. Wide enough for the widest
     * realistic pop-up -- a substitution subtitle naming two players
     * ("#12 Cristiano R. -> #7 Alexandra K.") is the worst case by a
     * comfortable margin.
     */
    private val canvasW: Int
    private val canvasH: Int

    /** Avatar diameter, derived from the text block exactly as VME
     *  does (`diameter = text_h * AVATAR_SCALE`). Fixed, because the
     *  canvas is fixed. */
    private val avatarPx: Int

    init {
        val worstSubtitle = "#12 Cristiano R.  \u2192  #7 Alexandra K."
        val worstTitle = "Substitution"
        val textW = maxOf(
            titlePaint.measureText(worstTitle),
            subtitlePaint.measureText(worstSubtitle),
        )
        val textH = lineHeight(titlePaint) + lineGapPx + lineHeight(subtitlePaint)
        avatarPx = (textH * AVATAR_SCALE).toInt()
        // Worst case is a substitution: two avatars, a gap between
        // them, then the padding gap, then the text.
        val avatarBlockW = avatarPx * 2 + avatarGapPx + padPx
        canvasW = (textW + avatarBlockW + padPx * 2 + accentPx).toInt()
            .coerceAtMost((videoWidth * 0.6f).toInt())
        canvasH = (maxOf(textH, avatarPx.toFloat()) + padPx * 2).toInt()
    }

    // Margins, as fractions of the frame, matching message.py's
    // margin=20 / bottom_margin=80 at 1080p.
    private val marginPx = (videoWidth * 0.0104f).toInt()
    private val bottomMarginPx =
        (videoHeight * 0.074f).toInt().coerceAtLeast(scoreboardHeightPx + padPx)

    // ---- animation state -------------------------------------------
    // Written on the main thread by show()/tick(), read by tick().
    private var animStartMs = 0L
    private var animDurationMs = 0L
    /** Resting position, computed once in [attach]. */
    private var restXPct = 0f
    private var yPct = 0f
    /** How far off-screen "fully slid out" is, in scale percent. */
    private var slideSpanPct = 0f

    private val hideRunnable = Runnable { hide() }

    private val tickRunnable = object : Runnable {
        override fun run() {
            val elapsed = System.currentTimeMillis() - animStartMs
            val progress = if (animDurationMs <= 0L) 1f
                           else (elapsed.toFloat() / animDurationMs)
            filter.setPosition(restXPct + xOffsetPct(progress), yPct)
            // Keep ticking while there is movement left. Between the
            // two ramps the position is constant, but we keep the
            // timer running rather than stopping and restarting it --
            // one cheap no-op call per frame is simpler than managing
            // a second scheduled wake-up.
            if (progress < 1f) handler.postDelayed(this, FRAME_MS)
        }
    }

    /**
     * Horizontal offset at [progress] through the pop-up's life, as
     * scale percent. Port of `message.py::_x_offset`, where the
     * offset is in pixels of box width; here the sprite works in
     * percent, so [slideSpanPct] plays the role of `width`.
     */
    private fun xOffsetPct(progress: Float): Float = when {
        progress < SLIDE_IN_FRAC -> {
            val t = progress / SLIDE_IN_FRAC
            slideSpanPct * (1f - easeOut(t))
        }
        progress < 1f - SLIDE_OUT_FRAC -> 0f
        else -> {
            val t = ((progress - (1f - SLIDE_OUT_FRAC)) / SLIDE_OUT_FRAC)
                .coerceIn(0f, 1f)
            slideSpanPct * easeIn(t)
        }
    }

    /** `1 - (1-t)^3` -- decelerating, so the pop-up arrives softly. */
    private fun easeOut(t: Float): Float {
        val u = 1f - t
        return 1f - u * u * u
    }

    /** `t^3` -- accelerating, so the pop-up leaves briskly. */
    private fun easeIn(t: Float): Float = t * t * t

    /**
     * Wire the filter into the pipeline. Call once, right after
     * `stream.getGlInterface().addFilter(popup.filter)` and BEFORE
     * `startStream`.
     *
     * `ImageObjectFilterRender.drawFilter()` forces `uAlpha = 0` while
     * no image has ever been loaded, so the filter is invisible until
     * the first [show] without needing an explicit `setAlpha(0f)`.
     *
     * Uses the `setPosition(x, y)` percentage overload rather than a
     * `TranslateTo` constant -- the pop-up has to sit above the
     * scoreboard bar, and the enum has no vertical-offset variant.
     */
    fun attach() {
        val sxPct = 100f * canvasW / videoWidth
        val syPct = 100f * canvasH / videoHeight
        restXPct = 100f - sxPct - (100f * marginPx / videoWidth)
        yPct = 100f - syPct - (100f * bottomMarginPx / videoHeight)
        // Slide far enough right that the whole card, plus its
        // margin, is off the frame at full offset.
        slideSpanPct = sxPct + (100f * marginPx / videoWidth)
        filter.setScale(sxPct, syPct)
        filter.setPosition(restXPct + slideSpanPct, yPct)
        Log.i(TAG, "attached canvas=${canvasW}x$canvasH " +
            "scale=${"%.1f".format(sxPct)}x${"%.1f".format(syPct)} " +
            "rest=${"%.1f".format(restXPct)},${"%.1f".format(yPct)}")
    }

    /**
     * Show a pop-up titled [title] with an optional [subtitle],
     * tinted [teamColor]. Pass `holdMs = 0` to pin it until the next
     * [show] or an explicit [hide].
     *
     * VME holds pop-ups for 3.0 s (`popup_duration_seconds`).
     */
    fun show(
        title: String,
        subtitle: String? = null,
        teamColor: Int = DEFAULT_BG,
        holdMs: Long = 3000L,
        avatars: List<Avatar> = emptyList(),
    ) {
        handler.removeCallbacks(hideRunnable)
        handler.removeCallbacks(tickRunnable)

        val bmp = Bitmap.createBitmap(canvasW, canvasH, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        // Fully transparent canvas; only the box itself is painted, so
        // the rest of the frame shows through (see class KDoc).
        c.drawColor(Color.TRANSPARENT, PorterDuff.Mode.SRC)

        val titleW = if (title.isBlank()) 0f else titlePaint.measureText(title)
        val subW = if (subtitle.isNullOrBlank()) 0f
                   else subtitlePaint.measureText(subtitle)
        val textW = maxOf(titleW, subW)
        val titleH = if (title.isBlank()) 0f else lineHeight(titlePaint)
        val subH = if (subtitle.isNullOrBlank()) 0f else lineHeight(subtitlePaint)
        val textH = titleH + (if (subH > 0f && titleH > 0f) lineGapPx else 0) + subH

        // VME draws avatars only when at least one real photo exists:
        // two anonymous number discs add nothing to a subtitle that
        // already names both numbers.
        val faces = if (avatars.any { it.photoPath != null }) avatars else emptyList()
        val avatarsW = if (faces.isEmpty()) 0f else
            (faces.size * avatarPx + (faces.size - 1) * avatarGapPx + padPx).toFloat()

        val contentW = avatarsW + textW
        val contentH = maxOf(textH, if (faces.isEmpty()) 0f else avatarPx.toFloat())

        val boxW = (contentW + padPx * 2 + accentPx).coerceAtMost(canvasW.toFloat())
        val boxH = (contentH + padPx * 2).coerceAtMost(canvasH.toFloat())
        // Hug the bitmap's bottom-right corner, so after the filter
        // places the canvas the visible box lands bottom-right of the
        // frame regardless of how short this particular label is.
        val left = canvasW - boxW
        val top = canvasH - boxH

        fillPaint.color = opaque(teamColor)
        c.drawRect(left, top, canvasW.toFloat(), canvasH.toFloat(), fillPaint)
        fillPaint.color = scaleRgb(teamColor, ACCENT_DARKEN)
        c.drawRect(left, top, left + accentPx, canvasH.toFloat(), fillPaint)

        var cursorX = left + accentPx + padPx
        for ((i, a) in faces.withIndex()) {
            val cy = top + padPx + (contentH - avatarPx) / 2f
            drawAvatar(c, a, cursorX, cy, teamColor)
            cursorX += avatarPx
            if (i + 1 < faces.size) cursorX += avatarGapPx
        }
        if (faces.isNotEmpty()) cursorX += padPx

        // Text block is vertically centred against the taller of the
        // avatar column and the text itself.
        var baseline = top + padPx + (contentH - textH) / 2f
        if (title.isNotBlank()) {
            baseline -= titlePaint.fontMetrics.ascent
            c.drawText(title, cursorX, baseline, titlePaint)
            baseline += titlePaint.fontMetrics.descent
        }
        if (!subtitle.isNullOrBlank()) {
            if (title.isNotBlank()) baseline += lineGapPx
            baseline -= subtitlePaint.fontMetrics.ascent
            c.drawText(subtitle, cursorX, baseline, subtitlePaint)
        }

        // Same translucency as every other overlay -- see OverlayAlpha.
        OverlayAlpha.apply(c, canvasW, canvasH)
        filter.setImage(bmp)
        filter.setAlpha(1f)

        if (holdMs > 0) {
            // Animated: slide in, hold, slide out, then go dark.
            animStartMs = System.currentTimeMillis()
            animDurationMs = holdMs
            handler.post(tickRunnable)
            handler.postDelayed(hideRunnable, holdMs)
        } else {
            // Pinned (the test button): skip the animation entirely
            // and park it at rest, so there is no slide-out to sit
            // through and no timer to fight with.
            animDurationMs = 0L
            filter.setPosition(restXPct, yPct)
        }
        Log.i(TAG, "show \"$title\" / \"${subtitle ?: ""}\" " +
            "faces=${faces.size} hold=${holdMs}ms")
    }

    /**
     * One circle on a pop-up: a player's cached photo when we have
     * one, otherwise a jersey-number disc.
     */
    data class Avatar(val jersey: Int, val photoPath: String?)

    /**
     * Draw [a] as a circle of [avatarPx] diameter at ([x], [y]).
     *
     * Photo path: cover-crop (never fit) so the face fills the
     * circle -- a photo is not a logo, and fitting one whole into a
     * circle leaves a person floating in a box of background. The
     * crop window is biased upward by [AVATAR_CROP_BIAS] because a
     * centred square crop of a standing photo lands on the torso.
     *
     * No photo: a disc at `bg * 0.55` with the jersey number, which
     * is VME's `_badge`.
     */
    private fun drawAvatar(c: Canvas, a: Avatar, x: Float, y: Float, bg: Int) {
        val d = avatarPx.toFloat()
        val r = d / 2f
        val cx = x + r
        val cy = y + r
        val photo = decodedPhoto(a.photoPath)
        if (photo != null) {
            // Cover-crop: scale so the short side fills the circle,
            // then offset to centre (biased up).
            val scale = maxOf(d / photo.width, d / photo.height)
            val sw = photo.width * scale
            val sh = photo.height * scale
            val dx = x - (sw - d) / 2f
            val dy = y - (sh - d) * AVATAR_CROP_BIAS
            val m = Matrix().apply {
                setScale(scale, scale)
                postTranslate(dx, dy)
            }
            avatarPaint.shader = BitmapShader(
                photo, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP,
            ).apply { setLocalMatrix(m) }
            c.drawCircle(cx, cy, r, avatarPaint)
            avatarPaint.shader = null
        } else {
            fillPaint.color = scaleRgb(bg, BADGE_DARKEN)
            c.drawCircle(cx, cy, r, fillPaint)
            badgePaint.textSize = d * BADGE_FONT_SCALE
            val fm = badgePaint.fontMetrics
            c.drawText(
                a.jersey.toString(), cx,
                cy - (fm.ascent + fm.descent) / 2f, badgePaint,
            )
        }
        c.drawCircle(cx, cy, r - ringPaint.strokeWidth / 2f, ringPaint)
    }

    /** Hide by fading the filter region out. Safe to call any time. */
    fun hide() {
        handler.removeCallbacks(hideRunnable)
        handler.removeCallbacks(tickRunnable)
        runCatching {
            filter.setAlpha(0f)
            // Park off-frame so the next show() starts its slide from
            // the right edge even if attach() ran long ago.
            filter.setPosition(restXPct + slideSpanPct, yPct)
        }
        Log.i(TAG, "hide")
    }

    /**
     * Decoded photos, memoised for this overlay's lifetime.
     *
     * A pop-up fires on an event and the same faces recur all match,
     * so decoding from disk every time would be pure waste. A roster
     * is a dozen players and each bitmap is `avatarPx` square (~80 px
     * at 1080p, so ~25 KB), which is nothing.
     *
     * A null result is cached too -- `containsKey` distinguishes
     * "tried and there is no photo" from "not tried yet", so a
     * missing or corrupt file is not re-read on every single pop-up.
     * That is the same trap VME's `_avatar_cache` comment calls out.
     */
    private val photoCache = HashMap<String, Bitmap?>()

    private fun decodedPhoto(path: String?): Bitmap? {
        if (path.isNullOrBlank()) return null
        if (photoCache.containsKey(path)) return photoCache[path]
        val bmp = PhotoCache.load(path, avatarPx)
        photoCache[path] = bmp
        return bmp
    }

    private fun lineHeight(p: Paint): Float {
        val fm = p.fontMetrics
        return fm.descent - fm.ascent
    }

    private fun opaque(c: Int) = Color.rgb(Color.red(c), Color.green(c), Color.blue(c))

    private fun scaleRgb(c: Int, f: Float) = Color.rgb(
        (Color.red(c) * f).toInt().coerceIn(0, 255),
        (Color.green(c) * f).toInt().coerceIn(0, 255),
        (Color.blue(c) * f).toInt().coerceIn(0, 255),
    )

    companion object {
        private const val TAG = "VMEPopup"

        // message.py: TITLE_FONT_SCALE / SUBTITLE_FONT_SCALE, both
        // 0.020 -- same size on purpose, hierarchy comes from weight.
        private const val TITLE_FONT_SCALE = 0.020f
        private const val SUBTITLE_FONT_SCALE = 0.020f
        // message.py uses fixed pixel values tuned at 1080p; expressed
        // here as fractions of the frame height so they scale.
        private const val PADDING_SCALE = 16f / 1080f
        private const val LINE_GAP_SCALE = 4f / 1080f
        private const val ACCENT_SCALE = 6f / 1080f
        // message.py: accent = bg * 0.6
        private const val ACCENT_DARKEN = 0.6f
        // message.py: AVATAR_SCALE / AVATAR_CROP_BIAS / AVATAR_GAP /
        // AVATAR_RING / BADGE_FONT_SCALE, and the badge's bg * 0.55.
        private const val AVATAR_SCALE = 1.9f
        private const val AVATAR_CROP_BIAS = 0.22f
        private const val AVATAR_GAP_SCALE = 6f / 1080f
        private const val RING_SCALE = 2f / 1080f
        private const val BADGE_FONT_SCALE = 0.44f
        private const val BADGE_DARKEN = 0.55f
        // message.py: SLIDE_IN_FRAC / SLIDE_OUT_FRAC, fractions of
        // the pop-up's total duration.
        private const val SLIDE_IN_FRAC = 0.1f
        private const val SLIDE_OUT_FRAC = 0.1f
        /** ~30 fps; the encoder runs at 30, so finer is invisible. */
        private const val FRAME_MS = 33L
        /** message.py `BG_COLOR_DEFAULT` */
        val DEFAULT_BG: Int = Color.rgb(45, 35, 75)
    }
}
