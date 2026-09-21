package works.vme.streamer.overlay

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.Typeface
import android.util.Log
import com.pedro.encoder.input.gl.render.filters.`object`.ImageObjectFilterRender
import com.pedro.encoder.utils.gl.TranslateTo
import works.vme.streamer.data.GameState

/**
 * Centred card, used for every "the game is not in play right now"
 * message: the pre-match line-up, a timeout, and the final score.
 *
 * ```
 *              +--------------------------------+
 *              |          FINAL                 |
 *              |                                |
 *              |   WHITECAPS    3  -  1  RIVERS |
 *              |                                |
 *              |  25-21   23-25   25-19   25-17 |
 *              +--------------------------------+
 * ```
 *
 * All three variants are the same card with a different heading,
 * centre figure and footnote, so they share one bitmap layout and
 * one filter. They are also mutually exclusive by nature -- a
 * timeout cannot overlap the final score -- so a single filter is
 * not just cheaper, it enforces something true.
 *
 * ### Why this is not the pop-up filter
 *
 * Pop-ups are bottom-right, small, and auto-hide. These are
 * centred, larger, and stay up until something replaces them.
 * Sharing a filter would mean `setScale` changing per event, and
 * `setScale` after attach reproducibly blanks these filters (see
 * [ScoreboardOverlay]). One extra full-screen quad per frame is
 * cheap by comparison.
 *
 * Colours follow the scoreboard so the package reads as one
 * design: the dark centre for the card body, team colours on the
 * names. Translucency is shared too -- see [OverlayAlpha].
 */
class CardOverlay(
    private val videoWidth: Int,
    private val videoHeight: Int,
    private var homeName: String,
    private var awayName: String,
    private var homeColor: Int,
    private var awayColor: Int,
    private var homeLogo: android.graphics.Bitmap? = null,
    private var awayLogo: android.graphics.Bitmap? = null,
) {

    /**
     * Rename and recolour.
     *
     * Unlike the scoreboard this card has no geometry derived from
     * the names -- they are centred at draw time and the card is a
     * fixed fraction of the frame -- so a redraw is the whole
     * update, with no `setScale` and no new filter.
     */
    fun setTeams(
        home: String, away: String, homeFill: Int, awayFill: Int,
        homeBadge: android.graphics.Bitmap? = null,
        awayBadge: android.graphics.Bitmap? = null,
    ) {
        homeName = home
        awayName = away
        homeColor = homeFill
        awayColor = awayFill
        homeLogo = homeBadge
        awayLogo = awayBadge
    }
    val filter: ImageObjectFilterRender = ImageObjectFilterRender()

    private val cardW = (videoWidth * CARD_W_FRAC).toInt()
    private val cardH = (videoHeight * CARD_H_FRAC).toInt()

    private val titlePaint = Paint().apply {
        color = Color.argb(200, 255, 255, 255)
        isAntiAlias = true
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        textSize = cardH * 0.11f
        textAlign = Paint.Align.CENTER
        letterSpacing = 0.25f
    }
    private val teamPaint = Paint().apply {
        isAntiAlias = true
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        textSize = cardH * 0.15f
    }
    private val centrePaint = Paint().apply {
        color = Color.WHITE
        isAntiAlias = true
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        textSize = cardH * 0.22f
        textAlign = Paint.Align.CENTER
    }
    private val footPaint = Paint().apply {
        color = Color.WHITE
        isAntiAlias = true
        typeface = Typeface.DEFAULT
        textSize = cardH * FOOT_FRAC
        textAlign = Paint.Align.CENTER
    }
    private val fillPaint = Paint().apply {
        isAntiAlias = true
        style = Paint.Style.FILL
    }
    private val logoPaint = Paint().apply {
        isAntiAlias = true
        isFilterBitmap = true
    }

    /** Scale and position, set once (see [ScoreboardOverlay] on why
     *  `setScale` must not be called again afterwards). */
    fun attach() {
        filter.setScale(100f * cardW / videoWidth, 100f * cardH / videoHeight)
        filter.setPosition(TranslateTo.CENTER)
    }

    // ---- variants ---------------------------------------------------

    /** Pre-match card: who is playing, shown until the first whistle. */
    fun showUpcoming() {
        draw(title = "COMING UP", centre = "vs")
    }

    /**
     * Timeout card. Carries the live score, because that is the
     * question a viewer joining during a stoppage actually has --
     * the scoreboard bar is the same information, but this is what
     * is on screen while play is halted.
     */
    fun showTimeout(state: GameState) {
        draw(
            title = "TIMEOUT",
            centre = "${state.homeScore}  -  ${state.awayScore}",
            sets = state.setScores,
            // Smaller here than on FINAL: during a timeout the live
            // score in the middle is the headline and the sets
            // already played are context. Still drawn with the same
            // gaps, so "smaller" does not become "crammed".
            setScale = TIMEOUT_SET_SCALE,
        )
    }

    /**
     * End-of-game card: every set's final score.
     *
     * No fallback to the running score any more. Game End records a
     * `set_end` first (see `MatchLiveActivity.confirmGameEnd`), so
     * the set being played when the match ended is in this list like
     * every other -- same size, same spacing, one row that can be
     * read straight across to see who won. The only way to reach
     * this card with nothing to show is a game that ended before a
     * point was scored.
     */
    fun showFinal(state: GameState) {
        draw(
            title = "FINAL",
            // The sets tally is gone. It was a summary of the line
            // below it, and the line below is the half worth having:
            // 25-21  23-25  25-19 says who won *and* how it went,
            // which "2 - 1" cannot. A dash keeps the two names apart
            // without asserting a number the scores already carry.
            centre = "\u2013",
            sets = state.setScores,
            setScale = FINAL_SET_SCALE,
        )
        Log.i(TAG, "final sets=${state.setScores.size}")
    }

    // ---- drawing ----------------------------------------------------

    /**
     * The set scores, evenly spaced on one row.
     *
     * Laid out item by item rather than joined into a single string
     * with spaces between. Joining and then shrinking the whole line
     * to fit shrinks the *gaps* along with the digits, so by three
     * sets the scores ran together into one long number -- the
     * opposite of what this row is for. Here the gap is a measured
     * quantity scaled in step with the type, so five sets still read
     * as five separate scores.
     *
     * Every score is drawn at one size, including the set that was
     * being played when the match ended: a row where the last score
     * looked different from the rest read as an afterthought rather
     * than as the one that decided it.
     */
    private fun drawSets(
        c: Canvas,
        sets: List<Pair<Int, Int>>,
        cx: Float,
        cy: Float,
        scale: Float,
    ) {
        // Spaces around the dash: "25 - 21" is a score, "25-21"
        // reads as one number at a glance, which is the whole
        // problem with a row of them.
        val texts = sets.map { (h, a) -> "$h - $a" }
        var size = cardH * FOOT_FRAC * scale
        var gap = size * SET_GAP_EMS
        footPaint.textSize = size

        fun rowWidth(): Float =
            texts.fold(0f) { acc, t -> acc + footPaint.measureText(t) } +
                gap * (texts.size - 1)

        var total = rowWidth()
        val usable = cardW * 0.86f
        if (total > usable) {
            val s = usable / total
            size *= s
            gap *= s
            footPaint.textSize = size
            total = rowWidth()
        }

        val fm = footPaint.fontMetrics
        val baseline = cy - (fm.ascent + fm.descent) / 2f
        footPaint.textAlign = Paint.Align.LEFT
        var x = cx - total / 2f
        for (t in texts) {
            c.drawText(t, x, baseline, footPaint)
            x += footPaint.measureText(t) + gap
        }
        // The paint is shared and every other user centres.
        footPaint.textAlign = Paint.Align.CENTER
    }

    /**
     * Draw [text] with [cy] as its optical centre, not its baseline.
     *
     * Everything on this card is placed by centre -- including the
     * badges, which are bitmaps and have no other option -- so the
     * text has to agree. It did not: `drawText` takes a baseline, so
     * a name and the badge beside it given the same `y` came out
     * about half a cap-height apart, which is exactly the "slightly
     * misaligned" look. The scoreboard already did this correctly;
     * this is the same two lines.
     */
    private fun drawAt(c: Canvas, text: String, x: Float, cy: Float, p: Paint) {
        val fm = p.fontMetrics
        c.drawText(text, x, cy - (fm.ascent + fm.descent) / 2f, p)
    }

    /**
     * Draw [logo] just outside a team name: to the left of the home
     * name, to the right of the away one.
     *
     * Skipped rather than clipped when a long name has eaten the
     * margin -- half a crest hanging off the card edge reads as a
     * rendering fault, where no crest just reads as no crest.
     */
    private fun drawLogo(
        c: Canvas,
        logo: android.graphics.Bitmap?,
        edge: Float,
        cy: Float,
        toLeft: Boolean,
    ) {
        if (logo == null) return
        val box = cardH * LOGO_FRAC
        val gap = cardH * 0.06f
        val left = if (toLeft) edge - gap - box else edge + gap
        if (left < RULE_PX || left + box > cardW - RULE_PX) return

        val scale = minOf(box / logo.width, box / logo.height)
        val w = logo.width * scale
        val h = logo.height * scale
        c.drawBitmap(
            logo,
            null,
            android.graphics.RectF(
                left + (box - w) / 2f, cy - h / 2f,
                left + (box + w) / 2f, cy + h / 2f,
            ),
            logoPaint,
        )
    }

    private fun draw(
        title: String,
        centre: String,
        sets: List<Pair<Int, Int>> = emptyList(),
        setScale: Float = 1f,
    ) {
        val bmp = Bitmap.createBitmap(cardW, cardH, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        c.drawColor(Color.TRANSPARENT, PorterDuff.Mode.SRC)

        fillPaint.color = CARD_BG
        c.drawRect(0f, 0f, cardW.toFloat(), cardH.toFloat(), fillPaint)
        // Team-coloured rules top and bottom, so the card is tied to
        // the two clubs rather than floating free.
        fillPaint.color = homeColor
        c.drawRect(0f, 0f, cardW / 2f, RULE_PX, fillPaint)
        c.drawRect(0f, cardH - RULE_PX, cardW / 2f, cardH.toFloat(), fillPaint)
        fillPaint.color = awayColor
        c.drawRect(cardW / 2f, 0f, cardW.toFloat(), RULE_PX, fillPaint)
        c.drawRect(cardW / 2f, cardH - RULE_PX, cardW.toFloat(), cardH.toFloat(), fillPaint)

        val cx = cardW / 2f

        // Rows are evenly spaced and symmetric about the middle of
        // the card. COMING UP has no set scores, so it draws two rows
        // where the other cards draw three; laying it out on the
        // three-row grid anyway left the content hugging the top
        // with an empty bottom third under it.
        val hasSets = sets.isNotEmpty()
        val titleCy: Float
        val midCy: Float
        if (hasSets) {
            titleCy = cardH * (0.5f - ROW_GAP_FRAC)
            midCy = cardH * 0.5f
        } else {
            titleCy = cardH * (0.5f - ROW_GAP_FRAC / 2f)
            midCy = cardH * (0.5f + ROW_GAP_FRAC / 2f)
        }

        drawAt(c, title, cx, titleCy, titlePaint)
        drawAt(c, centre, cx, midCy, centrePaint)

        // Team names flank the centre figure, in their own colours,
        // with each badge outboard of its name -- the card's margins
        // were empty, and putting the badge there keeps it clear of
        // both the name and the centre figure at every name length.
        teamPaint.color = homeColor
        teamPaint.textAlign = Paint.Align.RIGHT
        val homeRight = cx - cardW * 0.14f
        drawAt(c, homeName, homeRight, midCy, teamPaint)
        drawLogo(c, homeLogo, homeRight - teamPaint.measureText(homeName), midCy, true)

        teamPaint.color = awayColor
        teamPaint.textAlign = Paint.Align.LEFT
        val awayLeft = cx + cardW * 0.14f
        drawAt(c, awayName, awayLeft, midCy, teamPaint)
        drawLogo(c, awayLogo, awayLeft + teamPaint.measureText(awayName), midCy, false)

        if (hasSets) {
            drawSets(c, sets, cx, cardH * (0.5f + ROW_GAP_FRAC), setScale)
        }

        OverlayAlpha.apply(c, cardW, cardH)
        filter.setImage(bmp)
        filter.setAlpha(1f)
    }

    fun hide() {
        runCatching { filter.setAlpha(0f) }
    }

    companion object {
        private const val TAG = "VMECard"
        private const val CARD_W_FRAC = 0.62f
        private const val CARD_H_FRAC = 0.30f
        private const val RULE_PX = 6f
        /** Vertical distance between rows, as a fraction of card
         *  height. One value for both layouts, so a three-row card
         *  and a two-row one are spaced identically. */
        private const val ROW_GAP_FRAC = 0.30f
        /** Badge box side, as a fraction of card height. */
        private const val LOGO_FRAC = 0.26f
        /** Footnote size as a fraction of card height. */
        private const val FOOT_FRAC = 0.11f
        /** FINAL's set scores are the card's headline now, so they
         *  are drawn well above the nominal footnote size. Backed off
         *  from 1.7, which left no air between them and the names. */
        private const val FINAL_SET_SCALE = 1.45f
        /** TIMEOUT's are context under a live score, so smaller --
         *  but laid out with the same gaps, not squeezed. */
        private const val TIMEOUT_SET_SCALE = 0.95f
        /** Gap between two set scores, as a multiple of their type
         *  size. Scales with the text so the row keeps its rhythm at
         *  any number of sets. */
        private const val SET_GAP_EMS = 0.8f
        /** Matches the scoreboard's dark centre section. */
        private val CARD_BG: Int = Color.rgb(45, 35, 75)
    }
}
