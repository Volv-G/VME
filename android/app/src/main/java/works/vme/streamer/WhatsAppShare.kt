package works.vme.streamer

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build

/**
 * The WhatsApp half of the "we are live" announcement.
 *
 * ### Why a notification and not a send
 *
 * WhatsApp has no way for an app to post into an ordinary group: its
 * business API cannot reach the group parents are already in, and
 * the share intent cannot name a chat. What an app can do is open
 * WhatsApp with the text already written, leaving the operator to
 * pick the group (WhatsApp lists recent chats first) and press send.
 *
 * The operator has just pressed Start and is watching the picture, so
 * it is offered rather than opened: a notification that stays until
 * tapped or dismissed. The broadcast carries on while WhatsApp is in
 * front; only the preview pauses (see MatchLiveActivity's
 * `surfaceDestroyed`).
 */
object WhatsAppShare {

    private const val CHANNEL_ID = "announce"
    private const val NOTIFICATION_ID = 7302

    /** Regular WhatsApp first, then WhatsApp Business. Both are listed
     *  under `<queries>` in the manifest so they can be seen. */
    private val PACKAGES = listOf("com.whatsapp", "com.whatsapp.w4b")

    /** The intent that opens WhatsApp's chat picker with [text] written
     *  in; the system share sheet if WhatsApp is not installed. */
    fun intent(context: Context, text: String): Intent {
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, text)
        }
        val pkg = PACKAGES.firstOrNull { installed(context, it) }
        return if (pkg != null) send.setPackage(pkg)
        else Intent.createChooser(send, "Share the live link")
    }

    /** Post "Tell <group> on WhatsApp", which opens [intent] when tapped. */
    fun offer(context: Context, group: String, text: String) {
        val mgr = context.getSystemService(NotificationManager::class.java) ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            mgr.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Live announcement",
                    // DEFAULT, unlike the silent broadcast notification:
                    // this one asks for a tap and should be noticed.
                    NotificationManager.IMPORTANCE_DEFAULT,
                ),
            )
        }
        val pi = PendingIntent.getActivity(
            context, NOTIFICATION_ID,
            intent(context, text).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(context, CHANNEL_ID) else
            @Suppress("DEPRECATION") Notification.Builder(context)
        val n = b
            .setContentTitle("Tell $group on WhatsApp")
            .setContentText("Tap to send the live link")
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setSmallIcon(android.R.drawable.ic_menu_share)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        runCatching { mgr.notify(NOTIFICATION_ID, n) }
    }

    private fun installed(context: Context, pkg: String): Boolean = runCatching {
        context.packageManager.getPackageInfo(pkg, 0)
        true
    }.getOrDefault(false)
}
