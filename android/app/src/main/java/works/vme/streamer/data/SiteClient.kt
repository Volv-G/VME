package works.vme.streamer.data

import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Client for the public site ("Courtside VB") -- the phone's way to
 * see and change the schedule, and to say a match is live, from
 * anywhere with mobile data.
 *
 * ### Why a second server at all
 *
 * VME lives on a PC at home, reached over the home LAN (or not at
 * all). That was fine for roster import, which happens on the sofa the
 * night before. It is not fine for the two things the phone now wants
 * to do at the gym: look up tonight's fixture, and tell families the
 * stream has started. The site is on the public internet, so it is
 * reachable from the gym; it holds a copy of the schedule VME publishes
 * to it, and queues the phone's schedule changes for VME to pick up the
 * next time the PC is on (within about a minute, when it is).
 *
 * ### Authentication
 *
 * A bearer token issued by VME, not the VME password: the site is
 * public, and the phone should be able to lose its token without
 * anyone having to change the password that guards the library. The
 * token reaches the phone through VME itself ([refreshAccessFromVme]),
 * so there is nothing to type. It is never logged.
 *
 * Same shape as [VmeClient]: synchronous, `HttpURLConnection`, callers
 * own the threading.
 */
object SiteClient {

    /** Where the site is and the token it knows this phone by. */
    data class Access(val url: String, val token: String) {
        /** The host alone, for a status line: "courtside-vb.pages.dev". */
        val host: String
            get() = runCatching { URL(url).host }.getOrNull()?.ifBlank { null } ?: url
    }

    data class Tournament(val slug: String, val name: String)

    /** A match VME has on its books and has not seen played yet. */
    data class Upcoming(
        /** VME's address, `tournament/date/name`. What update and
         *  delete name the match by, and what a phone match's
         *  [VmeOrigin] is rebuilt from. */
        val key: String,
        val tournament: String,
        val tournamentName: String,
        val date: String,
        /** `HH:MM`, or null when the time is not known yet. */
        val time: String?,
        val opponent: String,
    )

    /** A schedule change the site has taken but VME has not applied
     *  yet -- the phone's own submissions, typically. */
    data class Pending(
        val id: Long,
        /** `add`, `update` or `delete`. */
        val action: String,
        /** The upcoming entry an update or delete is about. */
        val key: String?,
        val tournament: String,
        val date: String,
        val time: String?,
        val opponent: String,
        val submittedAt: Long,
    )

    data class TeamSchedule(
        val team: String,
        val name: String,
        val tournaments: List<Tournament>,
        val upcoming: List<Upcoming>,
        val pending: List<Pending>,
    )

    /** A stream the site currently believes is live. */
    data class Live(
        val team: String,
        /** The phone match's slug, as announced. */
        val id: String,
        val matchKey: String?,
        val opponent: String,
        val url: String,
        val startedAt: Long,
    )

    data class Schedule(val teams: List<TeamSchedule>, val live: List<Live>)

    private const val TAG = "VMESite"
    private const val TIMEOUT_MS = 10_000

    // ---- schedule ----------------------------------------------------

    /**
     * GET the schedule, returning both the parsed form and the raw body.
     * The raw body is what [ScheduleCache] keeps, so the offline copy is
     * exactly what the server said rather than a re-serialisation that
     * would drift from it whenever a field is added.
     */
    fun fetchSchedule(access: Access): Pair<Schedule, String> {
        val body = request(access, "GET", "/api/publish/phone/schedule", null)
        return parseSchedule(body) to body
    }

    fun parseSchedule(body: String): Schedule {
        val o = JSONObject(body)
        val teamsArr = o.optJSONArray("teams") ?: JSONArray()
        val teams = (0 until teamsArr.length()).map { i ->
            val t = teamsArr.getJSONObject(i)
            TeamSchedule(
                team = t.optString("team"),
                name = t.optString("name").ifBlank { t.optString("team") },
                tournaments = t.optJSONArray("tournaments").objects().map {
                    Tournament(
                        slug = it.optString("slug"),
                        name = it.optString("name").ifBlank { it.optString("slug") },
                    )
                },
                upcoming = t.optJSONArray("upcoming").objects().map {
                    Upcoming(
                        key = it.optString("key"),
                        tournament = it.optString("tournament"),
                        tournamentName = it.optString("tournament_name")
                            .ifBlank { it.optString("tournament") },
                        date = it.optString("date"),
                        time = it.optStringOrNull("time"),
                        opponent = it.optString("opponent"),
                    )
                },
                pending = t.optJSONArray("pending").objects().map {
                    Pending(
                        id = it.optLong("id"),
                        action = it.optString("action"),
                        key = it.optStringOrNull("key"),
                        tournament = it.optString("tournament"),
                        date = it.optString("date"),
                        time = it.optStringOrNull("time"),
                        opponent = it.optString("opponent"),
                        submittedAt = it.optLong("submitted_at"),
                    )
                },
            )
        }
        val live = o.optJSONArray("live").objects().map {
            Live(
                team = it.optString("team"),
                id = it.optString("id"),
                matchKey = it.optStringOrNull("match_key"),
                opponent = it.optString("opponent"),
                url = it.optString("url"),
                startedAt = it.optLong("started_at"),
            )
        }
        return Schedule(teams, live)
    }

    /**
     * Ask for a schedule change. [key] names the upcoming entry for an
     * update or delete and is null for an add. A tournament the team
     * does not have yet is fine -- VME creates it -- so it goes as typed.
     * Returns the pending id the site gave it.
     */
    fun submitSchedule(
        access: Access,
        team: String,
        action: String,
        key: String?,
        tournament: String,
        date: String,
        time: String?,
        opponent: String,
    ): Long {
        val body = JSONObject()
            .put("team", team)
            .put("action", action)
            .put("key", key ?: JSONObject.NULL)
            .put("tournament", tournament)
            .put("date", date)
            .put("time", time ?: JSONObject.NULL)
            .put("opponent", opponent)
        val resp = JSONObject(request(access, "POST", "/api/publish/phone/schedule", body))
        return resp.optLong("id")
    }

    // ---- live --------------------------------------------------------

    /** What the site is told about one stream. Kept whole so the
     *  heartbeat can repeat it verbatim; see [SiteLive]. */
    data class LiveAnnouncement(
        val team: String,
        val id: String,
        val matchKey: String?,
        val opponent: String,
        val title: String,
        val url: String,
    )

    fun announceLive(access: Access, a: LiveAnnouncement, live: Boolean) {
        val body = JSONObject()
            .put("team", a.team)
            .put("id", a.id)
            .put("match_key", a.matchKey ?: JSONObject.NULL)
            .put("opponent", a.opponent)
            .put("title", a.title)
            .put("url", a.url)
            .put("live", live)
        request(access, "POST", "/api/publish/phone/live", body)
    }

    // ---- linking -----------------------------------------------------

    /**
     * Ask VME for the site's address and this phone's token, and keep
     * them. Called opportunistically, on a thread that has just talked
     * to VME successfully -- so linking the site needs no step of its
     * own: the first roster import after VME has a site does it.
     *
     * Every failure is swallowed. A VME without a site (404), an older
     * VME without the endpoint, a blip -- none of them is a reason to
     * spoil the import that triggered this, and the stored access, if
     * any, is left as it was rather than wiped by a transient error.
     */
    fun refreshAccessFromVme(ctx: Context): Access? {
        val base = Settings.baseUrl(ctx)
        if (base.isBlank()) return null
        return runCatching {
            VmeClient.fetchSiteAccess(base, Settings.credentials(ctx))
        }.onFailure {
            // The message is a URL and a status, never the token.
            Log.i(TAG, "site access not refreshed: ${it.message}")
        }.getOrNull()?.also { Settings.setSiteAccess(ctx, it.url, it.token) }
    }

    // ---- internals ---------------------------------------------------

    private fun request(access: Access, method: String, path: String, body: JSONObject?): String {
        val url = access.url.trim().trimEnd('/') + path
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = TIMEOUT_MS
            readTimeout = TIMEOUT_MS
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Authorization", "Bearer ${access.token}")
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
        }
        try {
            if (body != null) {
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                // The site answers errors as {"error": "..."}; fall back
                // to the raw text, trimmed -- an HTML error page from a
                // proxy is not something to put in a toast whole.
                val detail = runCatching {
                    val o = JSONObject(text)
                    o.optString("error").ifBlank { o.optString("detail") }
                }.getOrNull()?.ifBlank { null } ?: text.take(200)
                throw java.io.IOException("HTTP $code" + if (detail.isNotBlank()) ": $detail" else "")
            }
            return text
        } finally {
            conn.disconnect()
        }
    }

    private fun JSONArray?.objects(): List<JSONObject> =
        if (this == null) emptyList()
        else (0 until length()).mapNotNull { optJSONObject(it) }

    private fun JSONObject.optStringOrNull(key: String): String? =
        if (!has(key) || isNull(key)) null else optString(key).ifBlank { null }
}

/**
 * The last schedule the site sent, on disk, so Home can show tonight's
 * fixture with no signal -- which is exactly when it is most wanted,
 * in a gym basement.
 */
object ScheduleCache {
    private fun file(ctx: Context) = File(ctx.filesDir, "site_schedule.json")

    fun load(ctx: Context): SiteClient.Schedule? = runCatching {
        val f = file(ctx)
        if (!f.exists()) null else SiteClient.parseSchedule(f.readText())
    }.getOrNull()

    fun save(ctx: Context, raw: String) {
        runCatching {
            val f = file(ctx)
            val tmp = File(f.parentFile, f.name + ".tmp")
            tmp.writeText(raw)
            if (!tmp.renameTo(f)) {
                f.writeText(raw)
                tmp.delete()
            }
        }
    }
}

/**
 * The stream this phone has told the site about, if any.
 *
 * A process-wide singleton rather than state on the activity, because
 * the two halves of the job live in different places: the activity
 * knows when a broadcast starts and stops, and [works.vme.streamer.StreamService]
 * is what keeps running with the screen locked. The site forgets a
 * stream 20 minutes after the last word about it (so a phone that dies
 * mid-match does not leave a LIVE badge up all evening), which means
 * something has to keep repeating "still live" -- and an activity
 * timer stops with the activity, while the service holds a wake lock
 * for exactly as long as the stream runs.
 *
 * Everything here is best effort and synchronous; callers are on a
 * background thread and never let a failure touch the stream.
 *
 * The three calls share one lock, network included. Without it a beat
 * that had read [current] just before Stop could land its "live" after
 * Stop's "not live", and the site would show a finished match as LIVE
 * for the next 20 minutes. Waiting up to a timeout on a background
 * thread is the cheaper problem.
 */
object SiteLive {
    private const val TAG = "VMESite"

    @Volatile
    var current: SiteClient.LiveAnnouncement? = null
        private set

    /** Announce a stream as live and remember it for the heartbeat.
     *  Throws on failure so the caller can log it; [current] is set
     *  either way, so a later beat can still get through. Returns
     *  false when no site is linked (nothing was sent). */
    @Synchronized
    fun start(ctx: Context, a: SiteClient.LiveAnnouncement): Boolean {
        val previous = current
        current = a
        val access = Settings.siteAccess(ctx) ?: return false
        // A different match still announced means some route out of
        // its live screen skipped Stop. One phone streams one match,
        // so that one is over: take its badge down rather than leave
        // it for the expiry.
        if (previous != null && previous.id != a.id) {
            runCatching { SiteClient.announceLive(access, previous, live = false) }
        }
        SiteClient.announceLive(access, a, live = true)
        return true
    }

    /** Repeat the live announcement. Called by the service every few
     *  minutes; failures only logged, the next beat will try again. */
    @Synchronized
    fun beat(ctx: Context) {
        val a = current ?: return
        val access = Settings.siteAccess(ctx) ?: return
        runCatching { SiteClient.announceLive(access, a, live = true) }
            .onSuccess { Log.i(TAG, "live heartbeat sent") }
            .onFailure { Log.w(TAG, "live heartbeat failed: ${it.message}") }
    }

    /** Say the stream is over. Throws on failure, like [start]; the
     *  announcement is forgotten either way -- the site's own expiry
     *  is the fallback for a "not live" that never arrived. Returns
     *  false when there was nothing to end. */
    @Synchronized
    fun end(ctx: Context, slug: String? = null): Boolean {
        val a = current ?: return false
        if (slug != null && a.id != slug) return false
        current = null
        val access = Settings.siteAccess(ctx) ?: return false
        SiteClient.announceLive(access, a, live = false)
        return true
    }
}
