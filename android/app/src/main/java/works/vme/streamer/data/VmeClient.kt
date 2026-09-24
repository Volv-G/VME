package works.vme.streamer.data

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Minimal HTTP client for the VME backend, scoped to the two endpoints
 * this app needs at *import* time (no live sync, by design — see the
 * plan doc). Uses `HttpURLConnection` from the platform to avoid pulling
 * OkHttp into the APK for two GETs.
 *
 * Every call is synchronous and must be invoked off the main thread.
 * Callers wrap it in `Thread { ... }` and marshal results back with
 * `runOnUiThread`; adding coroutines here would be more code than the
 * two callers benefit from.
 *
 * The base URL is passed in per-call. The app stores it in
 * `SharedPreferences` alongside the imports, so the second import goes
 * as a one-tap refresh.
 *
 * ### Authentication
 *
 * Optional HTTP Basic. The backend at $HOME is behind IIS Windows
 * Authentication, which negotiates NTLM/Kerberos by default;
 * Android's `HttpURLConnection` supports neither on its own (they are
 * OS-integrated on desktop JVMs, no-ops on Android). If the site
 * *also* has Basic enabled (a one-checkbox change in IIS), we can
 * authenticate with `Authorization: Basic base64(user:pass)`, which
 * is what this client sends when `Credentials` are supplied. Over
 * plain HTTP that is obviously not confidential -- but this is a
 * LAN-only import in the pre-match warm-up, not a session that lives
 * on the wire during play.
 *
 * NTLM/Kerberos support would mean pulling in `jcifs-ng` and reading
 * a lot of it; not worth it while the backend admin can flip Basic on.
 */
object VmeClient {

    /** Optional credentials for HTTP Basic. `domain\user` or
     *  `user@domain` are both valid `user` strings for IIS Basic --
     *  we pass them through verbatim. */
    data class Credentials(val user: String, val password: String)

    /** Ten seconds is enough for a gym Wi-Fi *and* short enough that a
     *  wrong URL surfaces as a failure, not a hang. */
    private const val TIMEOUT_MS = 10_000

    /** GET `/api/teams` — the list of team slugs on the server, used by
     *  the picker so the operator does not have to remember them. */
    fun listTeams(baseUrl: String, creds: Credentials? = null): List<TeamSummary> {
        val body = get("${normalize(baseUrl)}/api/teams", creds)
        val arr = JSONArray(body)
        return (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            TeamSummary(
                name = o.optString("name"),
                hasRoster = o.optBoolean("has_roster", false),
            )
        }
    }

    /** GET `/api/teams/{team}/roster` — the roster itself, parsed into
     *  the local shape. Throws if the server returns 4xx/5xx or if the
     *  JSON does not have the expected keys. */
    fun fetchRoster(baseUrl: String, teamSlug: String, creds: Credentials? = null): Roster {
        val body = get("${normalize(baseUrl)}/api/teams/$teamSlug/roster", creds)
        return JSONObject(body).toRoster()
    }

    /**
     * GET `/api/teams/{team}/players/{number}/photo` -- the player's
     * profile picture as raw bytes, or null when the server has none
     * (404) or the fetch fails.
     *
     * Returns null rather than throwing: a missing or corrupt photo
     * must never fail a roster import. The pop-up renderer already
     * falls back to a jersey-number disc, which is exactly what VME's
     * `message.py` does for a player with no photo.
     */
    fun fetchPlayerPhoto(
        baseUrl: String,
        teamSlug: String,
        number: Int,
        creds: Credentials? = null,
    ): ByteArray? {
        val url = "${normalize(baseUrl)}/api/teams/$teamSlug/players/$number/photo"
        return runCatching { getBytes(url, creds) }.getOrNull()
    }

    /**
     * GET `/api/teams/{team}/logo` -- the team badge as raw bytes, or
     * null when the server has none (404) or the fetch fails.
     *
     * Null rather than throwing, for the same reason as
     * [fetchPlayerPhoto]: a missing logo must never fail an import.
     * The overlays fall back to a coloured section with no badge,
     * which is what they drew before logos existed at all.
     */
    fun fetchTeamLogo(
        baseUrl: String,
        teamSlug: String,
        creds: Credentials? = null,
    ): ByteArray? {
        val url = "${normalize(baseUrl)}/api/teams/$teamSlug/logo"
        return runCatching { getBytes(url, creds) }.getOrNull()
    }

    /** One match as the fixture list shows it. */
    data class MatchSummary(
        val date: String,
        /** Folder leaf, the API identifier - e.g. `01_Bellevue`. */
        val name: String,
        val matchIndex: Int?,
        val opponent: String,
    ) {
        /** "M2 vs Bellevue" / "vs Bellevue" for the only match of a day. */
        fun label(): String =
            (if (matchIndex != null && matchIndex > 0) "M$matchIndex " else "") +
                "vs ${opponent.ifBlank { name }}"
    }

    /**
     * A match's fixture details, without its events or rosters.
     *
     * Deliberately metadata only: the phone is about to score this
     * match itself, so the editor's event log is not something to copy
     * in - it is the thing this phone will produce. What is worth
     * having is the part that is tedious and easy to get subtly wrong
     * courtside: the opponent's exact name as the library spells it,
     * their colour and badge, the date and the tournament folder. Get
     * those right and the export lands in the match that already
     * exists instead of creating a near-duplicate beside it.
     */
    data class MatchMeta(
        val tournament: String,
        val date: String,
        val name: String,
        val matchIndex: Int?,
        val opponent: String,
        val opponentColor: String?,
        val opponentLogo: ByteArray?,
    )

    /** Tournament folder names for a team, as VME lists them. */
    fun listTournaments(
        baseUrl: String,
        teamSlug: String,
        creds: Credentials? = null,
    ): List<String> {
        val url = "${normalize(baseUrl)}/api/teams/${enc(teamSlug)}/tournaments"
        val arr = JSONArray(get(url, creds))
        return (0 until arr.length()).mapNotNull {
            arr.getJSONObject(it).optString("name").ifBlank { null }
        }
    }

    /** Matches in one tournament, newest first. */
    fun listMatches(
        baseUrl: String,
        teamSlug: String,
        tournament: String,
        creds: Credentials? = null,
    ): List<MatchSummary> {
        val url = "${normalize(baseUrl)}/api/teams/${enc(teamSlug)}" +
            "/tournaments/${enc(tournament)}/matches"
        val arr = JSONArray(get(url, creds))
        return (0 until arr.length()).map {
            val o = arr.getJSONObject(it)
            MatchSummary(
                date = o.optString("date"),
                name = o.optString("name"),
                matchIndex = if (o.isNull("match_index")) null else o.optInt("match_index"),
                opponent = o.optString("opponent"),
            )
        }.sortedWith(compareByDescending<MatchSummary> { it.date }
            .thenBy { it.matchIndex ?: 0 })
    }

    /**
     * Fixture details for one match, badge included.
     *
     * The colour lives on the match's opponent roster rather than
     * anywhere team-wide: an opponent is per-match in VME, so the same
     * club can be a different shade in two tournaments and the one that
     * matters is this fixture's.
     */
    fun fetchMatchMeta(
        baseUrl: String,
        teamSlug: String,
        tournament: String,
        date: String,
        match: String,
        creds: Credentials? = null,
    ): MatchMeta {
        val base = "${normalize(baseUrl)}/api/teams/${enc(teamSlug)}" +
            "/tournaments/${enc(tournament)}/dates/${enc(date)}/matches/${enc(match)}"
        val o = JSONObject(get(base, creds))
        val opponentRoster = o.optJSONObject("opponent_roster")
        val color = opponentRoster?.optString("team_color")?.ifBlank { null }
        // Best effort: plenty of opponents have no badge on file, and a
        // missing one is not a reason to refuse the rest.
        val logo = runCatching { getBytes("$base/opponent-logo", creds) }.getOrNull()
        return MatchMeta(
            tournament = tournament,
            date = o.optString("date").ifBlank { date },
            name = match,
            matchIndex = if (o.isNull("match_index")) null else o.optInt("match_index"),
            opponent = o.optString("opponent").ifBlank {
                opponentRoster?.optString("team_name").orEmpty()
            },
            opponentColor = color,
            opponentLogo = logo,
        )
    }

    /** What the server made of an import. */
    data class ImportResult(
        val imported: Int,
        /** Events that happened while no clip was recording. Placed at the
         *  start of the next clip, which is the earliest frame that could
         *  show them. */
        val snapped: Int,
        val skipped: List<String>,
    )

    /**
     * POST this match's event log to VME, which projects each event onto
     * the uploaded clips by wall-clock time.
     *
     * The phone's own `clip_id` / `local_frame` are placeholders -- it
     * never saw the footage -- so only `_at` matters on the way over, and
     * the server ignores the rest.
     *
     * [replace] is how a re-import is meant to be done: without it a
     * second call adds every event a second time.
     */
    fun importEvents(
        baseUrl: String,
        team: String,
        tournament: String,
        date: String,
        match: String,
        events: JSONArray,
        liberos: Set<Int> = emptySet(),
        replace: Boolean = false,
        creds: Credentials? = null,
    ): ImportResult {
        val url = "${normalize(baseUrl)}/api/teams/${enc(team)}" +
            "/tournaments/${enc(tournament)}/dates/${enc(date)}" +
            "/matches/${enc(match)}/events/import"
        val body = JSONObject()
            .put("events", events)
            .put("liberos", JSONArray(liberos.sorted()))
            .put("replace", replace)
        val resp = JSONObject(post(url, body.toString(), creds))
        val skippedArr = resp.optJSONArray("skipped") ?: JSONArray()
        return ImportResult(
            imported = resp.optInt("imported"),
            snapped = resp.optInt("snapped"),
            skipped = (0 until skippedArr.length()).map { skippedArr.getString(it) },
        )
    }

    data class TeamSummary(val name: String, val hasRoster: Boolean)

    // ---- internals ---------------------------------------------------

    /** Trim trailing slashes so callers can stick "/api/…" on without
     *  producing "//". No smarter than that on purpose. */
    private fun normalize(baseUrl: String): String =
        baseUrl.trim().trimEnd('/')

    /** Percent-encode one path segment. Team and opponent names reach
     *  here with spaces in them often enough to matter. */
    private fun enc(segment: String): String =
        java.net.URLEncoder.encode(segment, "UTF-8").replace("+", "%20")

    private fun post(url: String, json: String, creds: Credentials?): String {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = TIMEOUT_MS
            // An import walks every event and rewrites match.json, so it
            // is slower than the reads above -- and it is worth waiting
            // for, because the alternative is re-tagging a match by hand.
            readTimeout = TIMEOUT_MS * 3
            doOutput = true
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
            if (creds != null) {
                val raw = "${creds.user}:${creds.password}"
                val e = Base64.encodeToString(
                    raw.toByteArray(Charsets.UTF_8), Base64.NO_WRAP,
                )
                setRequestProperty("Authorization", "Basic $e")
            }
        }
        try {
            conn.outputStream.use { it.write(json.toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.readText().orEmpty()
            if (code !in 200..299) {
                // FastAPI puts the useful sentence in `detail`; the rest
                // of the envelope is noise on a phone screen.
                val detail = runCatching {
                    JSONObject(text).optString("detail").ifBlank { text }
                }.getOrDefault(text)
                throw java.io.IOException("HTTP $code: $detail")
            }
            return text
        } finally {
            conn.disconnect()
        }
    }

    private fun get(url: String, creds: Credentials?): String {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = TIMEOUT_MS
            readTimeout = TIMEOUT_MS
            setRequestProperty("Accept", "application/json")
            if (creds != null) {
                val raw = "${creds.user}:${creds.password}"
                val enc = Base64.encodeToString(
                    raw.toByteArray(Charsets.UTF_8), Base64.NO_WRAP,
                )
                setRequestProperty("Authorization", "Basic $enc")
            }
        }
        try {
            val code = conn.responseCode
            if (code !in 200..299) {
                val err = runCatching {
                    conn.errorStream?.bufferedReader()?.readText().orEmpty()
                }.getOrDefault("")
                // Include the challenge header on 401 -- a courtside
                // operator will want to know whether the server is
                // asking for Basic (fixable here) or Negotiate/NTLM
                // (needs a backend config change).
                val hint = if (code == 401) {
                    val challenge = conn.getHeaderField("WWW-Authenticate").orEmpty()
                    if (challenge.isNotBlank()) " [WWW-Authenticate: $challenge]" else ""
                } else ""
                throw java.io.IOException(
                    "HTTP $code from $url$hint" + if (err.isNotBlank()) ": $err" else ""
                )
            }
            return conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    /** Binary sibling of [get], for images. Same auth and timeouts;
     *  throws on non-2xx so [fetchPlayerPhoto] can swallow it. */
    private fun getBytes(url: String, creds: Credentials?): ByteArray {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = TIMEOUT_MS
            readTimeout = TIMEOUT_MS
            setRequestProperty("Accept", "image/*")
            if (creds != null) {
                val raw = "${creds.user}:${creds.password}"
                val enc = Base64.encodeToString(
                    raw.toByteArray(Charsets.UTF_8), Base64.NO_WRAP,
                )
                setRequestProperty("Authorization", "Basic $enc")
            }
        }
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw java.io.IOException("HTTP $code from $url")
            return conn.inputStream.use { it.readBytes() }
        } finally {
            conn.disconnect()
        }
    }
}
