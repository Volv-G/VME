package works.vme.streamer.ui

import android.graphics.Rect
import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

/**
 * Android 15+ turns edge-to-edge on by default: the activity window
 * now draws underneath the status bar and (in gesture nav) the
 * navigation bar. Programmatic layouts like the ones this app uses do
 * not get automatic padding for that, so the top of every screen
 * ends up hidden behind the clock/notification icons.
 *
 * `fitsSystemWindows="true"` in XML is the old way; the modern
 * equivalent -- and the only one that also handles the display cutout
 * and the 3-button nav bar consistently -- is to install an insets
 * listener and pad the root view by whatever the system currently
 * reserves for system bars + cutout.
 *
 * ### The keyboard
 *
 * The same change made `android:windowSoftInputMode="adjustResize"` a
 * no-op: from targetSdk 35 the window no longer resizes for the IME,
 * so a field near the bottom of a screen sits underneath the keyboard
 * with no way to scroll to it. The manifest still declares it, and it
 * still does nothing.
 *
 * So the IME is an inset like any other here, folded into the bottom
 * padding. On a `ScrollView` that shrinks the viewport and the content
 * becomes scrollable; on a plain column it lifts the content clear.
 * Either way the listener then asks the focused view to bring itself
 * on screen, because shrinking the viewport does not by itself move
 * the caret into it.
 *
 * Call once, after `setContentView`, on the root view.
 */
fun View.applySystemBarInsets() {
    ViewCompat.setOnApplyWindowInsetsListener(this) { v, insets ->
        val bars = insets.getInsets(
            WindowInsetsCompat.Type.systemBars()
                or WindowInsetsCompat.Type.displayCutout()
        )
        val ime = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom
        // The IME inset already spans the nav bar it covers, so this is
        // a max rather than a sum -- adding them double-counts and
        // leaves a gap the height of the nav bar above the keyboard.
        v.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime))

        if (ime > 0) {
            // After the resize has been laid out, not during: the
            // scroll parent cannot honour the request until it knows
            // its new height.
            v.post {
                v.findFocus()?.let { focused ->
                    focused.requestRectangleOnScreen(
                        Rect(0, 0, focused.width, focused.height), false,
                    )
                }
            }
        }
        // Consume so children (e.g. SurfaceView) do not re-apply the
        // same padding and end up double-inset.
        WindowInsetsCompat.CONSUMED
    }
    // Trigger a first pass in case the view is already attached (it
    // always is by the time we're called from onCreate/setContentView,
    // but requesting insets is cheap and idempotent).
    requestApplyInsets()
}
