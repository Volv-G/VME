package works.vme.streamer.data

/**
 * How much bandwidth the stream is allowed to ask for.
 *
 * Courtside wifi is whatever the venue has, and a 6 Mbps 1080p push
 * into a bad hall does not degrade gracefully -- RTMP buffers, the
 * encoder is told to slow down, and the result is the spiky,
 * stalling picture that ruined a night's stream. Capping the encoder
 * lower produces a softer but *steady* image, which is the better
 * trade for a match nobody is watching frame by frame.
 *
 * ### Not just bitrate
 *
 * A 600 kbps 1080p30 stream spends about a hundredth of a bit per
 * pixel, and the first thing that falls apart is exactly the thing
 * worth watching at that bitrate: the thin white lettering on the
 * scoreboard. Every preset therefore carries a whole encoder
 * configuration, not a number:
 *
 * * **Resolution** is the biggest lever. Halving each dimension
 *   quadruples the bits each remaining pixel gets, and the overlays
 *   are drawn as a fraction of the frame, so the scoreboard stays the
 *   same size on screen and simply gets more of the budget.
 * * **Frame rate**, next: 30 -> 15 doubles the bits per frame, and
 *   nobody misses the difference in a hall.
 * * **Keyframe interval**, last: every keyframe is a full re-send of
 *   the picture. Where the picture barely changes - and in the
 *   no-video modes it does not change at all - a long interval leaves
 *   almost the whole budget for the parts that do move.
 *
 * Bitrate is the only one of the four that can be changed on a live
 * stream ([setVideoBitrateOnFly]); the rest are set in `prepareVideo`,
 * so switching preset mid-broadcast applies the new bitrate now and
 * the rest at the next Start.
 *
 * ### The floor
 *
 * [OverlaysOnly] is the "the wifi is gone" setting: the camera frame
 * is painted over with flat black, so the encoder is compressing a
 * static image and spends nearly nothing on it, while the scoreboard,
 * pop-ups and cards still render. A viewer sees the score and every
 * play called, which is most of what a courtside stream is for, over
 * a link that cannot carry video at all.
 *
 * [CourtOnly] is the same trade with something to look at: instead of
 * black, the frame is the rotation as a diagram, with the six players'
 * faces and numbers on a schematic court (see
 * [works.vme.streamer.overlay.CourtOverlay]). It costs the same as
 * black -- the picture only changes when somebody moves -- and turns
 * a blank stream into one that shows who is on.
 */
enum class StreamQuality(
    /** Stable id for SharedPreferences. Never rename these. */
    val key: String,
    /** Menu text. Includes the number so the operator can compare. */
    val label: String,
    /** Target video bitrate in bits per second. */
    val bitrate: Int,
    /** Encoder frame size. Not the capture size: the adapter always
     *  delivers 1080p and GL scales it down here. */
    val width: Int,
    val height: Int,
    val fps: Int,
    /** Seconds between keyframes. */
    val gop: Int,
    /** False when the camera frame is blacked out. */
    val video: Boolean = true,
    /** True when the frame is the rotation diagram instead. */
    val court: Boolean = false,
) {
    Full("full", "Full – 6 Mbps, 1080p", 6_000_000, 1920, 1080, 30, 2),
    Medium("medium", "Reduced – 3 Mbps, 720p", 3_000_000, 1280, 720, 30, 2),
    Low("low", "Low – 1.5 Mbps, 720p", 1_500_000, 1280, 720, 30, 3),
    // 540p at 600 kbps rather than 1080p: a soft but readable picture
    // with a legible scoreboard beats a sharp one nobody can decode.
    Minimal("minimal", "Minimal – 600 kbps, 540p", 600_000, 960, 540, 20, 4),
    // The no-video modes go back UP in resolution: the picture is
    // static, so the bitrate is spent on one clean keyframe and then
    // almost nothing, and 720p is where the scoreboard text is sharp.
    CourtOnly(
        "court", "Court positions – no video", 500_000, 1280, 720, 10, 5,
        video = false, court = true,
    ),
    OverlaysOnly(
        "overlays", "Overlays only – no video", 400_000, 1280, 720, 10, 5,
        video = false,
    );

    /** Compact form for the header chip. */
    val shortLabel: String
        get() = when {
            court -> "court only"
            !video -> "no video"
            bitrate >= 1_000_000 -> "%.1f Mbps".format(bitrate / 1_000_000f)
            else -> "${bitrate / 1000} kbps"
        }

    companion object {
        val DEFAULT = Full

        fun fromKey(key: String?): StreamQuality =
            values().firstOrNull { it.key == key } ?: DEFAULT
    }
}
