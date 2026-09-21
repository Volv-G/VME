package works.vme.streamer

import android.util.Base64
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * Sends the "we are live" announcement from the phone, on the phone's
 * own Google grant.
 *
 * ### Why not from the server
 *
 * It was, briefly. The server already holds a Google refresh token, so
 * relaying through VME meant no mail credentials on a device that lives
 * in a kit bag. Two things made it the wrong shape:
 *
 * 1.  The announcement matters most at a venue, which is exactly where
 *     the gym's Wi-Fi is least likely to reach a machine at home. A
 *     broadcast that starts fine and an email that never leaves is the
 *     worst combination.
 * 2.  The consent belongs where the operator is standing. Granting it
 *     on the server meant running a PowerShell script on another
 *     machine to enable a button on this one.
 *
 * The phone already authorises against Google for `liveBroadcasts`, so
 * `gmail.send` rides along on the same consent sheet (see
 * [YouTubeAuth.SCOPES]) and there is no second credential anywhere.
 *
 * ### Quota
 *
 * `messages.send` is 100 units against a Gmail daily quota measured in
 * the billions. Unlike the YouTube limits this app works around, this
 * one will never be reached by a volleyball club.
 */
object Mail {

    private const val SEND_URL =
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    /**
     * Send one plain-text message to [to].
     *
     * One recipient per call, and the caller loops: the list is usually
     * a single Google Group, but when it is not, the members are
     * parents' addresses and putting them in each other's `To` header
     * is not ours to do.
     *
     * Throws on failure so the caller can report which address failed.
     */
    fun send(token: String, to: String, subject: String, body: String) {
        val raw = Base64.encodeToString(
            rfc822(to, subject, body).toByteArray(Charsets.UTF_8),
            Base64.URL_SAFE or Base64.NO_WRAP,
        )
        val conn = (URL(SEND_URL).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connectTimeout = 15_000
            readTimeout = 20_000
            doOutput = true
        }
        try {
            val payload = JSONObject().put("raw", raw).toString()
            conn.outputStream.use { it.write(payload.toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            if (code !in 200..299) {
                val text = conn.errorStream
                    ?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
                throw RuntimeException(explain(code, text))
            }
        } finally {
            conn.disconnect()
        }
    }

    /**
     * A minimal RFC 822 message.
     *
     * Subject and body are both base64 in UTF-8 rather than written
     * literally: team names carry accents often enough, and a header
     * with a raw non-ASCII byte in it is a malformed message rather
     * than a mildly wrong one.
     */
    private fun rfc822(to: String, subject: String, body: String): String {
        val encodedSubject = "=?UTF-8?B?" + Base64.encodeToString(
            subject.toByteArray(Charsets.UTF_8), Base64.NO_WRAP,
        ) + "?="
        val encodedBody = Base64.encodeToString(
            body.toByteArray(Charsets.UTF_8), Base64.NO_WRAP,
        )
        return buildString {
            append("To: ").append(to).append("\r\n")
            append("Subject: ").append(encodedSubject).append("\r\n")
            append("MIME-Version: 1.0\r\n")
            append("Content-Type: text/plain; charset=\"UTF-8\"\r\n")
            append("Content-Transfer-Encoding: base64\r\n")
            append("\r\n")
            append(encodedBody)
        }
    }

    /**
     * Turn a Gmail error into something readable on a phone screen.
     *
     * 403 is the one worth naming: it is what a token granted before
     * `gmail.send` joined the scope list comes back with, and the raw
     * body calls it "insufficient authentication scopes", which reads
     * like a bug rather than "sign in again".
     */
    private fun explain(code: Int, text: String): String {
        val message = runCatching {
            JSONObject(text).optJSONObject("error")?.optString("message").orEmpty()
        }.getOrDefault("")
        if (code == 403 && message.contains("scope", ignoreCase = true)) {
            return "Google has not granted permission to send mail. " +
                "Stop and start the stream to sign in again."
        }
        return "HTTP $code" + if (message.isNotBlank()) ": $message" else ""
    }
}
