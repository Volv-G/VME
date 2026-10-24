package works.vme.streamer.overlay

import com.pedro.encoder.input.gl.render.filters.`object`.ImageObjectFilterRender

/**
 * The pop-ups on the stream, stacked the way VME's renders stack them.
 *
 * One [PopupOverlay] is one pop-up: a second `show` on it replaces the
 * first. With only one, the events that come in pairs replaced each other
 * - a Kill's pop-up gone a moment later for the Assist's - where VME
 * shows both. So this keeps a few, and lays them out as
 * `render/overlays/message.py` does: up from the bottom-right corner,
 * oldest at the bottom, each newer one above it, 10px apart at 1080p.
 * When one leaves, the ones above it drop into its place.
 *
 * The rally clock has a slot of its own at the very bottom, under
 * everything, as in VME: it can be up for half a minute, and pop-ups
 * coming and going above it should not move it.
 *
 * With every event slot busy, the oldest makes way for the newest.
 */
class PopupStack(
    videoWidth: Int,
    videoHeight: Int,
    scoreboardHeightPx: Int,
    slots: Int = 3,
) {
    private val gapPx = (videoHeight * 10f / 1080f).toInt().coerceAtLeast(4)
    private val events = List(slots) { PopupOverlay(videoWidth, videoHeight, scoreboardHeightPx) }
    private val clock = PopupOverlay(videoWidth, videoHeight, scoreboardHeightPx)
    /** Event pop-ups on screen, oldest first. */
    private val order = ArrayList<PopupOverlay>()

    /** Every filter, for `addFilter` - all of them, before the stream starts. */
    val filters: List<ImageObjectFilterRender> get() = (events + clock).map { it.filter }

    init {
        for (p in events + clock) {
            p.onHidden = {
                order.remove(p)
                layout()
            }
        }
    }

    fun attach() {
        for (p in events + clock) p.attach()
    }

    /** An event pop-up, on top of the stack. See [PopupOverlay.show]. */
    fun show(
        title: String,
        subtitle: String? = null,
        teamColor: Int = PopupOverlay.DEFAULT_BG,
        holdMs: Long = 3000L,
        avatars: List<PopupOverlay.Avatar> = emptyList(),
    ) {
        val slot = events.firstOrNull { it !in order }
            ?: order.first().also { it.hide() }
        slot.show(title, subtitle, teamColor, holdMs, avatars)
        order.remove(slot)
        order.add(slot)
        layout()
    }

    /** The rally clock: up, or its number changed. */
    fun showClock(text: String) {
        clock.showPinned(text)
        layout()
    }

    /** The rally is over: the clock stays [holdMs], then leaves. */
    fun releaseClock(holdMs: Long) = clock.releasePinned(holdMs)

    /** The rally ended with no result: the clock goes now. */
    fun hideClock() = clock.hide()

    fun hideAll() {
        for (p in events + clock) p.hide()
    }

    private fun layout() {
        var lift = 0
        if (clock.isUp) {
            clock.setLift(0)
            lift = clock.boxHeightPx + gapPx
        }
        for (p in order) {
            p.setLift(lift)
            lift += p.boxHeightPx + gapPx
        }
    }
}
