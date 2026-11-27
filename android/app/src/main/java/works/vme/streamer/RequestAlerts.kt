package works.vme.streamer

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.util.Log
import works.vme.streamer.data.Settings
import works.vme.streamer.data.SiteClient

/**
 * Puts each request to join a team up as a notification on this phone.
 *
 * The site also has VME email the admins, but mail an account sends to
 * itself never rings: Gmail files it without a notification. This phone
 * is the admin's and already holds a token for the site, so it asks the
 * site who is waiting and says so here.
 *
 * ### Why polling
 *
 * True push would need Firebase: a project, a config file in the app
 * and a service-account key on the site. A [JobScheduler] job every 15
 * minutes (Android's shortest period; Doze may stretch it) needs none
 * of that, and a join request can wait that long. Opening the app
 * checks at once as well.
 *
 * Each request is notified once, keyed by id and request time, so
 * asking again after a decline counts as new. A request that has been
 * decided (on the site, in VME, anywhere) has its notification taken
 * down on the next check.
 */
object RequestAlerts {

    private const val TAG = "VMERequests"
    private const val CHANNEL_ID = "join_requests"
    private const val JOB_ID = 7401
    private const val PERIOD_MS = 15 * 60 * 1000L
    /** Notification ids are this plus the request id. */
    private const val NOTIFICATION_BASE = 740_000
    private const val PREFS = "request_alerts"
    private const val KEY_SHOWN = "shown"

    /** Start the periodic check, unless it is already scheduled (to
     *  schedule again would restart its 15 minutes every app open). */
    fun schedule(ctx: Context) {
        val js = ctx.getSystemService(JobScheduler::class.java) ?: return
        if (js.getPendingJob(JOB_ID) != null) return
        val job = JobInfo.Builder(JOB_ID, ComponentName(ctx, RequestAlertJob::class.java))
            .setPeriodic(PERIOD_MS)
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPersisted(true)
            .build()
        runCatching { js.schedule(job) }.onFailure { Log.w(TAG, "not scheduled: ${it.message}") }
    }

    /**
     * Ask the site and bring the notifications in line with it.
     * Synchronous: call it off the main thread. Throws when the site
     * cannot be reached; returns quietly when no site is linked.
     */
    @Synchronized
    fun check(ctx: Context) {
        val access = Settings.siteAccess(ctx) ?: return
        val mgr = ctx.getSystemService(NotificationManager::class.java) ?: return
        // Notifications off: leave everything unshown, so the requests
        // still come up once they are allowed.
        if (!mgr.areNotificationsEnabled()) return
        val requests = SiteClient.fetchRequests(access)
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val shown = prefs.getStringSet(KEY_SHOWN, emptySet()).orEmpty()
        val current = requests.associateBy { "${it.id}:${it.requestedAt}" }

        for ((key, r) in current) {
            if (key !in shown) notify(ctx, mgr, access, r)
        }
        // Decided since: take the notification down.
        for (key in shown - current.keys) {
            key.substringBefore(':').toLongOrNull()?.let { mgr.cancel(notificationId(it)) }
        }
        prefs.edit().putStringSet(KEY_SHOWN, current.keys).apply()
    }

    private fun notificationId(requestId: Long) = NOTIFICATION_BASE + (requestId % 100_000).toInt()

    private fun notify(ctx: Context, mgr: NotificationManager, access: SiteClient.Access, r: SiteClient.JoinRequest) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            mgr.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Join requests", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Someone asked to join a team on the site"
                },
            )
        }
        val who = if (r.role == "coach") "as a coach" else "for ${r.playerName}"
        val text = buildString {
            append("${r.email} $who")
            if (r.note.isNotBlank()) append("\n“${r.note}”")
        }
        val admin = Intent(Intent.ACTION_VIEW, Uri.parse(access.url.trim().trimEnd('/') + "/admin"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pi = PendingIntent.getActivity(
            ctx, notificationId(r.id), admin,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(ctx, CHANNEL_ID) else
            @Suppress("DEPRECATION") Notification.Builder(ctx).setPriority(Notification.PRIORITY_HIGH)
        val n = b
            .setContentTitle("Join request: ${r.teamName}")
            .setContentText("${r.email} $who")
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setWhen(r.requestedAt)
            .setShowWhen(true)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        runCatching { mgr.notify(notificationId(r.id), n) }
    }
}

/** Runs [RequestAlerts.check] on JobScheduler's timer. */
class RequestAlertJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        Thread {
            runCatching { RequestAlerts.check(applicationContext) }
                .onFailure { Log.i("VMERequests", "check failed: ${it.message}") }
            // A failed check is retried at the next period, not sooner.
            jobFinished(params, false)
        }.start()
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = true
}
