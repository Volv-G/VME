package works.vme.streamer

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * The three YouTube Live calls this app needs, over plain HTTP.
 *
 * No client library: google-api-client for Android is a large dependency for
 * three requests, and the REST shape is stable and documented. `HttpURLConnection`
 * plus `org.json` are both in the platform.
 *
 * Everything here blocks, and every method must be called off the main thread.
 *
 * Why the app creates the broadcast at all
 * ----------------------------------------
 * A persistent stream key makes YouTube auto-create a broadcast when bytes
 * arrive, inheriting one saved title - so a season becomes a column of
 * identically named videos. Creating it here means the title is decided at the
 * gym, where the opponent is actually known.
 *
 * Three settings are not preferences (same reasoning as the backend's
 * `app/upload/live.py`):
 *   - `enableAutoStop=false`   - a dropout must not end the match permanently.
 *   - `monitorStream=false`    - no "testing" phase; nobody is at a computer.
 *   - `selfDeclaredMadeForKids`- required by the API, not optional.
 */
object YouTubeLive {

    private const val API = "https://www.googleapis.com/youtube/v3"

    /** Primary RTMPS ingest. Port 443 survives networks that block 1935. */
    const val INGEST_PRIMARY = "rtmps://a.rtmps.youtube.com:443/live2"

    data class Stream(val id: String, val key: String, val title: String)

    data class Broadcast(val id: String, val title: String, val watchUrl: String)

    class ApiError(val status: Int, val reason: String, message: String) : RuntimeException(message)

    /**
     * Reusable ingest endpoints on the channel.
     *
     * Returns the **full** key, unlike the backend's equivalent: the phone is
     * the one device that legitimately needs it, since it is what it streams
     * with.
     */
    fun listStreams(token: String): List<Stream> {
        val body = get("$API/liveStreams?part=id,snippet,cdn&mine=true&maxResults=50", token)
        val items = body.optJSONArray("items") ?: JSONArray()
        val out = ArrayList<Stream>(items.length())
        for (i in 0 until items.length()) {
            val item = items.getJSONObject(i)
            val cdn = item.optJSONObject("cdn") ?: JSONObject()
            val info = cdn.optJSONObject("ingestionInfo") ?: JSONObject()
            out.add(
                Stream(
                    id = item.optString("id"),
                    key = info.optString("streamName"),
                    title = (item.optJSONObject("snippet") ?: JSONObject()).optString("title"),
                )
            )
        }
        return out
    }

    /**
     * Pick the stream to use.
     *
     * `preferKeyTail` exists because a channel can have several keys - this one
     * has five, three of them belonging to another service. Binding the wrong
     * one sends the match somewhere nobody is watching, and nothing about that
     * failure is visible from the phone. So when there is more than one and no
     * hint, this refuses rather than guesses.
     */
    fun pickStream(streams: List<Stream>, preferKeyTail: String?): Stream {
        if (streams.isEmpty()) {
            throw RuntimeException(
                "This channel has no stream key. Create one in YouTube Studio " +
                    "(Create > Go live > Stream)."
            )
        }
        if (!preferKeyTail.isNullOrEmpty()) {
            val tail = preferKeyTail.takeLast(4)
            val hits = streams.filter { it.key.endsWith(tail) }
            if (hits.size == 1) return hits[0]
            if (hits.size > 1) {
                throw RuntimeException("${hits.size} stream keys end in '$tail'.")
            }
            throw RuntimeException(
                "No stream key on this channel ends in '$tail'. Signed in to the " +
                    "wrong account?"
            )
        }
        if (streams.size > 1) {
            val names = streams.joinToString(", ") { "${it.title} (...${it.key.takeLast(4)})" }
            throw RuntimeException(
                "This channel has ${streams.size} stream keys and none was chosen: $names"
            )
        }
        return streams[0]
    }

    /** `liveBroadcasts.insert` - 50 quota units. */
    fun createBroadcast(
        token: String,
        title: String,
        description: String,
        privacyStatus: String,
    ): Broadcast {
        // Required by the API. There is nothing to schedule - the broadcast
        // starts when the encoder connects - so it is "now plus a cushion",
        // because a start time already in the past is rejected.
        val start = java.time.OffsetDateTime.now().plusMinutes(2)
            .format(java.time.format.DateTimeFormatter.ISO_OFFSET_DATE_TIME)

        val body = JSONObject()
            .put(
                "snippet",
                JSONObject()
                    .put("title", title)
                    .put("description", description)
                    .put("scheduledStartTime", start)
            )
            .put(
                "status",
                JSONObject()
                    .put("privacyStatus", privacyStatus)
                    .put("selfDeclaredMadeForKids", false)
            )
            .put(
                "contentDetails",
                JSONObject()
                    .put("enableAutoStart", true)
                    .put("enableAutoStop", false)
                    .put("enableDvr", true)
                    .put("recordFromStart", true)
                    .put("latencyPreference", "low")
                    .put("monitorStream", JSONObject().put("enableMonitorStream", false))
            )

        val resp = post(
            "$API/liveBroadcasts?part=id,snippet,status,contentDetails", token, body
        )
        val id = resp.optString("id")
        return Broadcast(
            id = id,
            title = (resp.optJSONObject("snippet") ?: JSONObject()).optString("title"),
            watchUrl = "https://www.youtube.com/watch?v=$id",
        )
    }

    /** `liveBroadcasts.bind` - 50 quota units. Points the broadcast at the key. */
    fun bind(token: String, broadcastId: String, streamId: String) {
        post(
            "$API/liveBroadcasts/bind?id=$broadcastId&streamId=$streamId&part=id,contentDetails",
            token,
            null,
        )
    }

    // ---- plumbing ---------------------------------------------------------

    private fun get(url: String, token: String): JSONObject = call("GET", url, token, null)

    private fun post(url: String, token: String, body: JSONObject?): JSONObject =
        call("POST", url, token, body)

    private fun call(method: String, url: String, token: String, body: JSONObject?): JSONObject {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Accept", "application/json")
            connectTimeout = 15_000
            readTimeout = 20_000
        }
        try {
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            } else if (method == "POST") {
                // bind() has no body, but a POST with no Content-Length can
                // confuse HttpURLConnection into buffering forever.
                conn.doOutput = true
                conn.setFixedLengthStreamingMode(0)
                conn.outputStream.close()
            }

            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use(BufferedReader::readText).orEmpty()

            if (code !in 200..299) throw translate(code, text)
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }

    /**
     * Turn an error body into something readable on a phone screen.
     *
     * The live API's failures are their own vocabulary, and the raw JSON
     * explains none of them - `liveStreamingNotEnabled` in particular looks
     * like an app bug when it is a one-time switch on the channel.
     */
    private fun translate(code: Int, text: String): ApiError {
        var reason = ""
        var message = text
        try {
            val err = JSONObject(text).optJSONObject("error")
            if (err != null) {
                message = err.optString("message", text)
                val errors = err.optJSONArray("errors")
                if (errors != null && errors.length() > 0) {
                    reason = errors.getJSONObject(0).optString("reason", "")
                }
            }
        } catch (_: Exception) {
            // Not JSON; the raw body is the best we have.
        }

        val advice = when {
            reason == "liveStreamingNotEnabled" ->
                "\n  Live streaming is not enabled on this channel. Turn it on at " +
                    "youtube.com/livestreaming - it needs phone verification and up " +
                    "to 24 hours the first time."
            reason == "insufficientLivePermissions" || reason == "livePermissionDenied" ->
                "\n  This account may not manage live broadcasts on that channel. " +
                    "Signed in as the channel owner?"
            reason == "invalidScheduledStartTime" ->
                "\n  YouTube refused the start time - check the phone's clock."
            code == 401 ->
                "\n  The access token was refused. Press SIGN IN again."
            code == 403 && reason == "quotaExceeded" ->
                "\n  The project's daily API quota is exhausted; it resets at " +
                    "midnight Pacific."
            else -> ""
        }
        return ApiError(code, reason, "HTTP $code${if (reason.isEmpty()) "" else " ($reason)"}: $message$advice")
    }
}
