package works.vme.streamer.overlay

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PorterDuff
import android.graphics.Typeface
// The last package segment is `object`, a Kotlin keyword; backticks
// are the only way to import from a package with a reserved name.
import com.pedro.encoder.input.gl.render.filters.`object`.ImageObjectFilterRender
import com.pedro.encoder.utils.gl.TranslateTo
import works.vme.streamer.data.GameState
import works.vme.streamer.data.Match
import works.vme.streamer.data.Side

/**
 * Live scoreboard, burned into the RTMP feed via RootEncoder's
 * `ImageObjectFilterRender`.
 *
 * Ports the broadcast-bar design from
 * `backend/app/render/overlays/scoreboard.py`. Five sections with
 * slanted dividers, bottom-centred on the video:
 *
 * ```
 *   HOME_COLOR | dark | HOME_SCORE - AWAY_SCORE | dark | AWAY_COLOR
 *          [home name]      [sets] [score] [sets]      [away name]
 *          --------------------------------------------------------
 *          . . . . . point history dots (current set) . . . . . . .
 * ```
 *
 * ### Bitmap is fully opaque and fixed-size
 *
 * Two independent bugs led here:
 *
 * 1. **`ImageObjectFilterRender` ignores per-pixel alpha on the S24**
 *    -- semi-transparent pixels come out opaque black. So every
 *    pixel of the bitmap must be a meaningful colour; the bitmap
 *    itself is sized to the exact bar (`barW x barH`), and anything
 *    "outside" the bar simply does not exist.
 * 2. **`ImageObjectFilterRender.setImage(bmp)` recycles the
 *    bitmap** after it has uploaded the pixels to GL. Confirmed
 *    from a real S24 stack trace:
 *
 *    ```
 *    java.lang.RuntimeException: Canvas: trying to use a
 *      recycled bitmap android.graphics.Bitmap@...
 *      at android.graphics.Canvas.<init>(Canvas.java:120)
 *      at ScoreboardOverlay.update(ScoreboardOverlay.kt:...)
 *    ```
 *
 *    This means every attempt to keep the bitmap around --
 *    redraw-in-place, two-buffer swap, three-buffer pool, any
 *    reuse pattern -- will eventually try to `new Canvas(bmp)`
 *    on a bitmap RootEncoder has already recycled and crash the
 *    app on the *second* score update (the first setImage
 *    recycles, the second event needs a canvas over the same
 *    handle).
 *
 *    The only safe pattern is single-use: allocate a fresh
 *    `Bitmap` on every [update], draw into it, hand it to
 *    `setImage`, and drop our reference. RootEncoder owns it
 *    from that point on and will recycle it when it's done. At
 *    ~250 KB per allocation and one allocation per event this is
 *    well under a megabyte per set even in a rally-heavy match.
 *
 *    (This also explains the earlier "scoreboard goes black on
 *    first score" symptom before the two-buffer attempt: the
 *    first `setImage` recycled the shared bitmap, the next
 *    `Canvas.drawXxx` calls silently no-op'd against the
 *    recycled backing, and RootEncoder was drawing whatever
 *    empty state the recycled texture left behind.)
 *
 *    Bar dimensions are still computed **once** at construction --
 *    resizing the bitmap live would need `setScale` per update
 *    (also broken), and the widest plausible score "99  -  99"
 *    fits any real game. `setScale`/`setPosition` are still
 *    called once at [attach] and never again.
 *
 *
 * The bar floats [BOTTOM_MARGIN_FRAC] of the frame height above
 * the bottom edge, matching VME's gap. Flush-bottom looked stuck
 * to the edge, and on a TV with overscan the bottom row of pixels
 * is not guaranteed to be visible at all. Positioned with the
 * percentage `setPosition(x, y)` overload -- an earlier note here
 * claimed no Y-offset API existed, which was wrong.
 *
 * Drawn at [OverlayAlpha.OVERLAY_ALPHA] so the court shows through:
 * the bar sits over the near end line, and a fully opaque slab
 * hides play the viewer wants to see. VME uses the same trick.
 * See [OverlayAlpha] for why it is a post-pass on the bitmap
 * rather than `filter.setAlpha`.
 *
 * Logos are v2: they need a fetch/cache layer plus a circular-mask
 * blit. Team-coloured blocks + names are legible without them.
 */
class ScoreboardOverlay(
    private val videoWidth: Int,
    private val videoHeight: Int,
    /** Public so a rename can be detected without rebuilding the
     *  filter to find out -- see [setTeamColors]. */
    val homeName: String,
    val awayName: String,
    private var homeColor: Int,
    private var awayColor: Int,
    /**
     * Team badges, already decoded. Null when the team has none, and
     * the section is then sized and drawn exactly as it was before
     * logos existed.
     *
     * Taken at construction, like the names, because the section
     * widths in `init` have to account for them -- see
     * [setTeamColors] for why that cannot be changed later.
     */
    private val homeLogo: android.graphics.Bitmap? = null,
    private val awayLogo: android.graphics.Bitmap? = null,
) {

    /**
     * Recolour in place. The next [update] draws with the new
     * colours; nothing else has to happen.
     *
     * There is deliberately no matching rename. [barW] is measured
     * from the two names in `init` and becomes a `setScale` at
     * [attach], and `setScale` on an attached filter reproducibly
     * blanks it. Rebuilding the filter is the alternative, and
     * `addFilter` after `startStream` silently drops filters on the
     * S24. So a rename reaches the bar at the next stream start.
     */
    fun setTeamColors(home: Int, away: Int) {
        homeColor = home
        awayColor = away
    }
    // ---- geometry: computed ONCE ----------------------------------

    // Bar height math ported from Python: main_h = video_height * 0.025 / 0.5.
    private val mainH: Int = (videoHeight * 0.05f).toInt().coerceAtLeast(48)
    private val padPx: Int = (mainH * 0.30f).toInt().coerceAtLeast(12)
    private val skewPx: Int = (mainH * 0.34f).toInt().coerceAtLeast(6)

    private val teamFontPx: Float = mainH * 0.45f
    private val setsFontPx: Float = mainH * 0.40f
    private val scoreFontPx: Float = mainH * 0.50f

    private val pointStripH: Int = (mainH * 0.28f).toInt().coerceAtLeast(8)

    /** Badge box side. Just under the bar's height so it sits inside
     *  the slanted section without touching either edge. */
    private val logoPx: Float = mainH * 0.70f
    private val logoGapPx: Float = mainH * 0.16f
    private val logoPaint = Paint().apply {
        isAntiAlias = true
        isFilterBitmap = true
    }

    // Measure ONCE using the widest plausible values. Any real score
    // ("0 - 0" through "99 - 99") fits inside "99 - 99"'s width, and
    // the sets column widens for a two-digit set count (never happens
    // in volleyball, but cheap to leave room for).
    private val hSection: Int
    private val sSection: Int
    private val cSection: Int
    private val aSection: Int

    /** Bar width. NOT the video width -- the bar is only as wide as
     *  its contents need. `setScale` is derived from this at [attach]
     *  and then never touched. */
    val barW: Int

    /** Bar height including the point-history strip. */
    val barH: Int = mainH + pointStripH

    init {
        val measurePaint = Paint().apply {
            isAntiAlias = true
            typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
            textSize = teamFontPx
        }
        val homeW = measurePaint.measureText(homeName) + logoSlotW(homeLogo)
        val awayW = measurePaint.measureText(awayName) + logoSlotW(awayLogo)
        measurePaint.textSize = setsFontPx
        // Two-digit set count as a headroom margin -- one digit will
        // never overflow.
        val setsW = measurePaint.measureText("99")
        measurePaint.textSize = scoreFontPx
        val scoreW = measurePaint.measureText("99  -  99")

        hSection = (homeW + padPx * 2 + skewPx).toInt()
        aSection = (awayW + padPx * 2 + skewPx).toInt()
        sSection = (setsW + padPx * 2 + skewPx).toInt()
        cSection = (scoreW + padPx * 2 + skewPx).toInt()

        val raw = hSection + sSection + cSection + sSection + aSection
        // Cap at 96% of video width so extreme team names still fit;
        // in that case the sections scale down proportionally (rare).
        barW = raw.coerceAtMost((videoWidth * 0.96f).toInt())
    }

    // ---- paints (reused across draws) -----------------------------

    private val textPaint = Paint().apply {
        color = Color.WHITE
        isAntiAlias = true
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
    }
    private val fillPaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.FILL
    }

    // ---- filter + working buffer ----------------------------------

    val filter: ImageObjectFilterRender = ImageObjectFilterRender()

    // A fresh Bitmap is allocated for every update(); see class
    // KDoc for why we don't reuse or swap.
    private fun newBar(): Bitmap =
        Bitmap.createBitmap(barW, barH, Bitmap.Config.ARGB_8888).also {
            it.setHasAlpha(false)  // every pixel drawn opaque
        }

    // Cached section widths after fitting to `barW`. Computed in
    // [attach] because they depend on the possibly-clamped `barW`.
    private var hSec = hSection
    private var sSec = sSection
    private var cSec = cSection
    private var aSec = aSection

    // ---- lifecycle -------------------------------------------------

    /** Add the filter to the GL pipeline: set scale + position ONCE
     *  and never touch them again. Any subsequent `setScale` call
     *  reproducibly blanks the overlay on the S24. */
    fun attach() {
        val raw = hSection + sSection + cSection + sSection + aSection
        if (raw != barW) {
            val scale = barW.toFloat() / raw
            hSec = (hSection * scale).toInt()
            aSec = (aSection * scale).toInt()
            sSec = (sSection * scale).toInt()
            cSec = barW - hSec - aSec - 2 * sSec
        }
        val sxPct = 100f * barW / videoWidth
        val syPct = 100f * barH / videoHeight
        filter.setScale(sxPct, syPct)
        // Centred horizontally, lifted clear of the bottom edge.
        val xPct = (100f - sxPct) / 2f
        val yPct = 100f - syPct - (BOTTOM_MARGIN_FRAC * 100f)
        filter.setPosition(xPct, yPct)
    }

    /**
     * Hide the bar without detaching the filter.
     *
     * VME's `GameStartEvent` / `GameEndEvent` carry a
     * `ScoreboardVisibilityEffect`, so the scoreboard only exists
     * between the whistle and the final point -- warm-up and
     * post-match footage stay clean. `setAlpha(0)` is how that maps
     * onto a GL filter, and it leaves the texture in place so the
     * next [update] can bring the bar straight back.
     */
    fun hide() {
        runCatching { filter.setAlpha(0f) }
    }

    /** Redraw with the current state and push it to the filter.
     *  Allocates a fresh backing bitmap so `setImage` always sees a
     *  new reference (see class KDoc). */
    fun update(@Suppress("UNUSED_PARAMETER") match: Match, state: GameState) {
        val bmp = newBar()
        val c = Canvas(bmp)
        // Fully opaque clear -- see class KDoc.
        c.drawColor(SECTION_CENTER_COLOR, PorterDuff.Mode.SRC)

        drawSlantedSections(c)

        val textCy = mainH / 2f

        textPaint.textSize = teamFontPx
        // Badge first, name after it -- the same order as the section
        // is measured in `init`, so the text lands where the width
        // was reserved for it.
        val homeX = padPx.toFloat()
        drawLogo(c, homeLogo, homeX, textCy)
        drawTextLeft(c, homeName, homeX + logoSlotW(homeLogo), textCy)

        val awayX = (hSec + sSec + cSec + sSec + padPx).toFloat()
        drawLogo(c, awayLogo, awayX, textCy)
        drawTextLeft(c, awayName, awayX + logoSlotW(awayLogo), textCy)

        textPaint.textSize = setsFontPx
        drawTextCentre(c, state.homeSets.toString(),
            (hSec + sSec / 2f), textCy)
        drawTextCentre(c, state.awaySets.toString(),
            (hSec + sSec + cSec + sSec / 2f), textCy)

        textPaint.textSize = scoreFontPx
        drawTextCentre(c, "${state.homeScore}  -  ${state.awayScore}",
            (hSec + sSec + cSec / 2f), textCy)

        drawPointHistory(c, state)

        OverlayAlpha.apply(c, barW, barH)

        filter.setImage(bmp)
        // Every update implies "show": the only caller that wants the
        // bar gone calls hide(), and it is cheaper to re-assert alpha
        // here than to track visibility as separate state.
        filter.setAlpha(1f)
    }

    // ---- drawing ---------------------------------------------------

    /** Five sections with slant-edged dividers: sets sections lean
     *  outward, matching VME's Python `_draw_slanted_blocks`. */
    /** Horizontal room a badge needs, including the gap after it.
     *  Zero when there is no badge, so a team without one is laid
     *  out exactly as before. */
    private fun logoSlotW(logo: android.graphics.Bitmap?): Float =
        if (logo == null) 0f else logoPx + logoGapPx

    /**
     * Draw [logo] square, vertically centred on [cy], its left edge
     * at [x].
     *
     * Aspect is preserved inside the square rather than stretched to
     * fill it: club badges are all shapes, and a squashed crest is
     * more noticeable than a small one.
     */
    private fun drawLogo(c: Canvas, logo: android.graphics.Bitmap?, x: Float, cy: Float) {
        if (logo == null) return
        val side = logoPx
        val scale = minOf(side / logo.width, side / logo.height)
        val w = logo.width * scale
        val h = logo.height * scale
        c.drawBitmap(
            logo,
            null,
            android.graphics.RectF(
                x + (side - w) / 2f, cy - h / 2f,
                x + (side + w) / 2f, cy + h / 2f,
            ),
            logoPaint,
        )
    }

    private fun drawSlantedSections(c: Canvas) {
        val h = mainH.toFloat()
        val skew = skewPx.toFloat()

        val b1 = hSec.toFloat()
        val b2 = (hSec + sSec).toFloat()
        val b3 = (hSec + sSec + cSec).toFloat()
        val b4 = (hSec + sSec + cSec + sSec).toFloat()

        val d1t = b1 - skew; val d1b = b1 + skew
        val d2t = b2 - skew; val d2b = b2 + skew
        val d3t = b3 + skew; val d3b = b3 - skew
        val d4t = b4 + skew; val d4b = b4 - skew

        drawPoly(c, homeColor,
            0f, 0f, d1t, 0f, d1b, h, 0f, h)
        drawPoly(c, SECTION_SETS_COLOR,
            d1t, 0f, d2t, 0f, d2b, h, d1b, h)
        drawPoly(c, SECTION_CENTER_COLOR,
            d2t, 0f, d3t, 0f, d3b, h, d2b, h)
        drawPoly(c, SECTION_SETS_COLOR,
            d3t, 0f, d4t, 0f, d4b, h, d3b, h)
        drawPoly(c, awayColor,
            d4t, 0f, barW.toFloat(), 0f, barW.toFloat(), h, d4b, h)
    }

    private fun drawPoly(c: Canvas, colour: Int, vararg xy: Float) {
        fillPaint.color = colour
        val p = Path()
        p.moveTo(xy[0], xy[1])
        var i = 2
        while (i < xy.size) { p.lineTo(xy[i], xy[i + 1]); i += 2 }
        p.close()
        c.drawPath(p, fillPaint)
    }

    private fun drawTextLeft(c: Canvas, text: String, x: Float, cy: Float) {
        textPaint.textAlign = Paint.Align.LEFT
        val fm = textPaint.fontMetrics
        c.drawText(text, x, cy - (fm.ascent + fm.descent) / 2f, textPaint)
    }

    private fun drawTextCentre(c: Canvas, text: String, cx: Float, cy: Float) {
        textPaint.textAlign = Paint.Align.CENTER
        val fm = textPaint.fontMetrics
        c.drawText(text, cx, cy - (fm.ascent + fm.descent) / 2f, textPaint)
    }

    private fun drawPointHistory(c: Canvas, state: GameState) {
        val stripY = mainH
        // Continue the darkest colour under the strip.
        fillPaint.color = SECTION_CENTER_COLOR
        c.drawRect(
            0f, stripY.toFloat(),
            barW.toFloat(), (stripY + pointStripH).toFloat(),
            fillPaint,
        )
        val points = state.pointHistory
        if (points.isEmpty()) return

        val slots = maxOf(15, points.size)
        val available = barW * 4f / (5f * slots + 1f)
        val diameter = available
            .coerceAtMost(10f * (videoHeight / 720f))
            .coerceAtLeast(2f)
        val gap = diameter / 4f
        val step = diameter + gap
        val r = diameter / 2f
        val cy = stripY + pointStripH / 2f
        val x0 = gap + r
        for ((i, isHome) in points.withIndex()) {
            val cx = x0 + i * step
            if (cx + r > barW) break
            fillPaint.color = if (isHome) homeColor else awayColor
            c.drawCircle(cx, cy, r, fillPaint)
        }
    }

    @Suppress("unused")
    private val homeSide = Side.Home  // keep import graph tidy

    companion object {
        /** Dark purple; matches Python `(80, 60, 100)` (sets sections). */
        private val SECTION_SETS_COLOR = Color.rgb(80, 60, 100)
        /** Darker purple; matches Python `(45, 35, 75)` (score + strip). */
        private val SECTION_CENTER_COLOR = Color.rgb(45, 35, 75)
        /** Gap between the bar and the bottom edge, as a fraction of
         *  frame height. ~32 px at 1080p, matching VME's 30 px. */
        private const val BOTTOM_MARGIN_FRAC = 0.03f
    }
}
