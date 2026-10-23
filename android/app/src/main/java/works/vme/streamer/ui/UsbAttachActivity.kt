package works.vme.streamer.ui

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * The app's claim on the capture adapter when it is plugged in.
 *
 * Plugging a USB device in makes Android offer it to every app that
 * says it handles one, and a phone collects plenty of those - a USB
 * camera app, a music player, the gallery, a card reader for a till.
 * Without a claim of our own the "Open with" chooser came up on every
 * replug, over the match screen, mid-match, and VME Streamer could not
 * be picked from it. Declaring USB video devices here puts it on that
 * list; picking it with "Always" makes Android hand the adapter
 * straight to us from then on - permission granted, no chooser, no
 * prompt - which is what lets a kicked cable recover on its own.
 *
 * The reconnect itself is done by the match screen, which watches the
 * port (see MatchLiveActivity.usbWatcher). So this has nothing to do
 * but get out of the way: over a running match it closes at once and
 * the match screen is back where it was; with the app not running, it
 * opens it, since plugging the camera in is a fair sign of what comes
 * next.
 */
class UsbAttachActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (isTaskRoot) {
            startActivity(
                Intent(this, HomeActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        }
        finish()
    }
}
