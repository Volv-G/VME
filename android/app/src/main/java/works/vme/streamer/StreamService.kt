package works.vme.streamer

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log

/**
 * Keeps the process alive and the CPU awake while a broadcast is
 * running.
 *
 * ### Why this exists
 *
 * `FLAG_KEEP_SCREEN_ON` (round 14) only holds while the scoring
 * activity is in front and nobody has touched the power button. It
 * does nothing about the two cases that actually end a stream: the
 * operator locking the phone, and Android deciding a backgrounded
 * process can be frozen. Once the process is frozen the encoder
 * stops feeding RTMP and YouTube drops the connection a few seconds
 * later.
 *
 * A foreground service is the sanctioned way to say "this process is
 * doing something the user asked for, leave it running". The partial
 * wake lock is the other half: it keeps the CPU up with the screen
 * off, which a foreground service does **not** guarantee on its own.
 *
 * ### What it deliberately does not do
 *
 * It does not own the camera, the encoder or the stream. Those stay
 * on `MatchLiveActivity`, which is where the surface, the overlays
 * and the event log already live; moving them here would be a much
 * larger change for no benefit while the activity is never finished
 * mid-broadcast. This service is a permission and lifetime holder,
 * nothing more.
 *
 * ### Service types
 *
 * `microphone` because the stream carries audio, and background mic
 * access has needed a typed foreground service since Android 11.
 * `connectedDevice` for the USB capture adapter. Camera is
 * deliberately *not* declared: the video comes from a UVC device over
 * the USB host API, not the platform camera service, and the type
 * would demand a runtime CAMERA grant this app never asks for.
 */
class StreamService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val slug = intent?.getStringExtra(EXTRA_SLUG)
        val title = intent?.getStringExtra(EXTRA_TITLE) ?: "Streaming"
        startAsForeground(buildNotification(slug, title))
        acquireWakeLock()
        // NOT sticky: if the system kills this, the encoder and the
        // USB device went with it, so a bare service restarting into
        // an empty process would only show a notification for a
        // broadcast that is no longer running.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        releaseWakeLock()
        super.onDestroy()
    }

    // ---- foreground -----------------------------------------------

    private fun startAsForeground(n: Notification) {
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    NOTIFICATION_ID,
                    n,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                        FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE_COMPAT,
                )
            } else {
                startForeground(NOTIFICATION_ID, n)
            }
        }.onFailure {
            // A missing RECORD_AUDIO grant makes the typed call throw.
            // Falling back to an untyped foreground service still buys
            // the process-lifetime protection, which is most of the
            // point, so log it and carry on rather than taking the
            // broadcast down over a notification.
            Log.w(TAG, "typed startForeground failed: ${it.message}")
            runCatching { startForeground(NOTIFICATION_ID, n) }
                .onFailure { e -> Log.e(TAG, "startForeground failed: ${e.message}") }
        }
    }

    private fun buildNotification(slug: String?, title: String): Notification {
        val mgr = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            mgr.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Live broadcast",
                    // LOW: an ongoing broadcast should be visible and
                    // silent. DEFAULT buzzes the phone on the tripod.
                    NotificationManager.IMPORTANCE_LOW,
                ).apply { setShowBadge(false) },
            )
        }

        // Tapping it returns to the match being scored, not to a
        // cold start of the app.
        val open = Intent(this, Class.forName(LIVE_ACTIVITY)).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            if (slug != null) putExtra(EXTRA_SLUG, slug)
        }
        val pi = PendingIntent.getActivity(
            this, 0, open,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(this, CHANNEL_ID) else
            @Suppress("DEPRECATION") Notification.Builder(this)
        return b
            .setContentTitle("VME is live")
            .setContentText(title)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    // ---- wake lock -------------------------------------------------

    private fun acquireWakeLock() {
        if (wakeLock != null) return
        val pm = getSystemService(PowerManager::class.java)
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, WAKE_TAG).apply {
            setReferenceCounted(false)
            // No timeout: a set can run long, and a lock that expired
            // mid-match would reproduce exactly the bug this fixes.
            // It is released in onDestroy, and the service is stopped
            // whenever the stream stops, including from onDestroy of
            // the activity.
            acquire()
        }
        Log.i(TAG, "wake lock held")
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) runCatching { it.release() } }
        wakeLock = null
        Log.i(TAG, "wake lock released")
    }

    companion object {
        private const val TAG = "VMEService"
        private const val CHANNEL_ID = "vme_live"
        private const val NOTIFICATION_ID = 1
        private const val WAKE_TAG = "vme:stream"
        private const val EXTRA_SLUG = "match_slug"
        private const val EXTRA_TITLE = "title"
        private const val LIVE_ACTIVITY = "works.vme.streamer.ui.MatchLiveActivity"

        /** `FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE` is API 30+; the
         *  app installs to 24, so the constant is inlined rather than
         *  referenced. Harmless below 30 because the typed overload
         *  is only called from 29 up and unknown bits are ignored. */
        private const val FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE_COMPAT = 16

        /** Start holding the process up. Safe to call twice. */
        fun start(context: Context, slug: String, title: String) {
            val i = Intent(context, StreamService::class.java)
                .putExtra(EXTRA_SLUG, slug)
                .putExtra(EXTRA_TITLE, title)
            runCatching {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                    context.startForegroundService(i) else context.startService(i)
            }.onFailure { Log.e(TAG, "start failed: ${it.message}") }
        }

        /** Let go. Safe to call when it was never started. */
        fun stop(context: Context) {
            runCatching {
                context.stopService(Intent(context, StreamService::class.java))
            }.onFailure { Log.e(TAG, "stop failed: ${it.message}") }
        }
    }
}
