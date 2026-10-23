package works.vme.streamer.logic

/**
 * The bitrate controller behind the Auto bandwidth setting.
 *
 * Fed one sample a second while live, it answers with a new bitrate when
 * one is due. The only thing that can change under a running encoder is
 * the bitrate (`setVideoBitrateOnFly`), so that is the one lever: the
 * frame size and rate stay what the stream was prepared with.
 *
 * Down fast, up slow - the shape TCP uses, for the same reason: a link
 * that is falling behind costs viewers seconds of delay, then frozen
 * picture, so a cut has to be immediate and deep; a link with room to
 * spare costs only some sharpness, so raising can be patient.
 *
 * - **Trouble** is the send queue more than [BAD_BACKLOG_S] behind, a
 *   dropped video frame, or the outgoing frame rate sagging under
 *   [BAD_FPS_RATIO] of what this stream normally sends. Its "normal" is
 *   learned, not the configured 30: a camera that only delivers 25 is
 *   not a network problem. Each cut is to 75% (60% when it is severe -
 *   [SEVERE_BACKLOG_S] behind, or frames dropped), then [CUT_SPACING_S]
 *   seconds for the queue to drain before another, and no raise for
 *   [RAISE_HOLDOFF_S].
 * - **Room** is the queue nearly empty with the frame rate at its norm.
 *   After [FAST_RAISE_S] good seconds the bitrate goes up 15% - unless it
 *   is near the rate that last caused trouble, where it takes
 *   [SLOW_RAISE_S] good seconds to go up 8%. Getting past that rate
 *   cleanly forgets it, so a link that has recovered gets the full
 *   ceiling back.
 */
class AutoBitrate(
    private val floor: Int,
    private val ceiling: Int,
    start: Int,
) {
    /** The bitrate the encoder should be at now. */
    var bitrate: Int = start.coerceIn(floor, ceiling)
        private set

    /** Why the last change was made, for the log. */
    var reason: String = ""
        private set

    private var t = 0L
    private var goodSeconds = 0
    private var nextCutAt = 0L
    private var noRaiseUntil = 0L
    /** The bitrate in force when trouble last came, until passed. */
    private var lastTrouble: Int? = null
    /** Recent outgoing frame rates, for what "normal" is. */
    private val fpsWindow = ArrayDeque<Int>()

    /**
     * One second's observation. [backlogSec] is how far the send queue is
     * behind, [droppedNew] whether any video frame was dropped since the
     * last sample, [fps] the frames sent in the last second (0 = unknown).
     * Returns the new bitrate when it changes, else null.
     */
    fun sample(backlogSec: Float, droppedNew: Boolean, fps: Int): Int? {
        t++
        val fpsLow = fpsSagging(fps)
        val severe = backlogSec > SEVERE_BACKLOG_S || droppedNew
        val bad = severe || backlogSec > BAD_BACKLOG_S || fpsLow
        if (bad) {
            goodSeconds = 0
            if (t < nextCutAt) return null
            nextCutAt = t + CUT_SPACING_S
            noRaiseUntil = t + RAISE_HOLDOFF_S
            lastTrouble = bitrate
            reason = when {
                droppedNew -> "frames dropped"
                backlogSec > BAD_BACKLOG_S -> "%.1fs behind".format(backlogSec)
                else -> "frame rate down to $fps"
            }
            return set((bitrate * if (severe) SEVERE_CUT else CUT).toInt())
        }
        if (backlogSec >= GOOD_BACKLOG_S || fps == 0) {
            goodSeconds = 0
            return null
        }
        goodSeconds++
        if (t < noRaiseUntil) return null
        val trouble = lastTrouble
        val near = trouble != null && bitrate >= trouble * NEAR_TROUBLE
        if (goodSeconds < if (near) SLOW_RAISE_S else FAST_RAISE_S) return null
        goodSeconds = 0
        if (trouble != null && bitrate > trouble) lastTrouble = null
        reason = if (near) "steady near the last limit" else "room to spare"
        return set((bitrate * if (near) SLOW_RAISE else FAST_RAISE).toInt())
    }

    /** Is [fps] well under what this stream usually sends? */
    private fun fpsSagging(fps: Int): Boolean {
        if (fps <= 0) return false
        fpsWindow.addLast(fps)
        if (fpsWindow.size > FPS_WINDOW_S) fpsWindow.removeFirst()
        // The usual rate: near the top of the recent window, so one slow
        // second does not drag the reference down with it.
        val usual = fpsWindow.sorted()[(fpsWindow.size * 9) / 10].coerceAtLeast(1)
        return fpsWindow.size >= MIN_FPS_SAMPLES && fps < usual * BAD_FPS_RATIO
    }

    private fun set(b: Int): Int? {
        val next = b.coerceIn(floor, ceiling)
        if (next == bitrate) return null
        bitrate = next
        return next
    }

    companion object {
        const val BAD_BACKLOG_S = 0.5f
        const val SEVERE_BACKLOG_S = 2.0f
        const val GOOD_BACKLOG_S = 0.15f
        const val BAD_FPS_RATIO = 0.75f
        const val CUT = 0.75
        const val SEVERE_CUT = 0.6
        const val FAST_RAISE = 1.15
        const val SLOW_RAISE = 1.08
        const val NEAR_TROUBLE = 0.85
        const val CUT_SPACING_S = 3L
        const val RAISE_HOLDOFF_S = 15L
        const val FAST_RAISE_S = 5
        const val SLOW_RAISE_S = 20
        const val FPS_WINDOW_S = 30
        const val MIN_FPS_SAMPLES = 5
    }
}
