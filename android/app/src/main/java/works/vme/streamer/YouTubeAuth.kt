package works.vme.streamer

import android.app.Activity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import com.google.android.gms.auth.api.identity.AuthorizationRequest
import com.google.android.gms.auth.api.identity.AuthorizationResult
import com.google.android.gms.auth.api.identity.Identity
import com.google.android.gms.common.api.Scope

/**
 * Gets a YouTube access token on the phone, with nothing secret in the APK.
 *
 * Why this shape and not AppAuth
 * ------------------------------
 * Google's own installed-app guide says plainly that **custom URI schemes are
 * no longer supported on Android**, and that loopback redirects are deprecated
 * for mobile. That removes both redirect mechanisms AppAuth would normally use
 * with Google as the provider. The supported route on Android is Play
 * Services' authorization API, which is what this uses.
 *
 * What it hands back is the useful part: a **short-lived access token and no
 * refresh token**. That matters for this app specifically -
 * docs/android-streaming-spike.md says no Google refresh token may ship in the
 * APK, and here there is none to ship or to leak. The token is minted on the
 * device, lives about an hour, and dies on its own.
 *
 * An hour is plenty: the token is needed only to create the broadcast at the
 * start of a match. Once RTMP bytes are flowing, YouTube authenticates them by
 * the stream key alone - the API is not in the path, so a two-hour match
 * outlives the token harmlessly.
 *
 * One-time setup (per signing certificate)
 * ----------------------------------------
 * The project must contain an **Android** OAuth client whose package name and
 * SHA-1 match this app; Google matches the caller by signature rather than by
 * a client id in the code, which is why there is no client id here. See the
 * README. A missing or mismatched registration surfaces as an
 * `ApiException` with status 10 (DEVELOPER_ERROR), which is otherwise
 * thoroughly unhelpful, so it is translated below.
 */
object YouTubeAuth {

    /** Manage the channel: create broadcasts, read stream keys, bind them. */
    private const val SCOPE_YOUTUBE = "https://www.googleapis.com/auth/youtube"

    /**
     * Ask for a token, prompting for consent only if needed.
     *
     * The first call on a device shows a Google account/consent sheet, which
     * arrives as a `PendingIntent` to launch rather than as a token. Later
     * calls return a token directly, with no UI - so pressing GO LIVE at a gym
     * does not mean signing in at a gym.
     */
    fun requestToken(
        activity: Activity,
        launcher: ActivityResultLauncher<IntentSenderRequest>,
        log: (String) -> Unit,
        onToken: (String) -> Unit,
    ) {
        val request = AuthorizationRequest.builder()
            .setRequestedScopes(listOf(Scope(SCOPE_YOUTUBE)))
            .build()

        Identity.getAuthorizationClient(activity)
            .authorize(request)
            .addOnSuccessListener { result ->
                if (result.hasResolution()) {
                    val pending = result.pendingIntent
                    if (pending == null) {
                        log("auth: needs consent but no pending intent - giving up")
                        return@addOnSuccessListener
                    }
                    log("auth: asking for consent...")
                    launcher.launch(IntentSenderRequest.Builder(pending.intentSender).build())
                } else {
                    deliver(result, log, onToken)
                }
            }
            .addOnFailureListener { exc ->
                log("auth: failed: ${explain(exc)}")
            }
    }

    /** Continue after the consent sheet returns. */
    fun handleResult(
        activity: Activity,
        data: android.content.Intent?,
        log: (String) -> Unit,
        onToken: (String) -> Unit,
    ) {
        if (data == null) {
            log("auth: consent cancelled")
            return
        }
        try {
            val result = Identity.getAuthorizationClient(activity)
                .getAuthorizationResultFromIntent(data)
            deliver(result, log, onToken)
        } catch (exc: Exception) {
            log("auth: could not read consent result: ${explain(exc)}")
        }
    }

    private fun deliver(
        result: AuthorizationResult,
        log: (String) -> Unit,
        onToken: (String) -> Unit,
    ) {
        val token = result.accessToken
        if (token.isNullOrEmpty()) {
            log("auth: no access token in the result")
            return
        }
        log("auth: got an access token (...${token.takeLast(6)})")
        onToken(token)
    }

    private fun explain(exc: Exception): String {
        val text = exc.toString()
        // Status 10 is DEVELOPER_ERROR, and it means one thing here: the
        // package name and signing certificate do not match any Android OAuth
        // client in the project. Worth saying, because the raw message names
        // neither.
        if (text.contains("10:") || text.contains("DEVELOPER_ERROR")) {
            return "$text\n" +
                "  This almost always means no Android OAuth client in the " +
                "Google Cloud project matches this app's package " +
                "(works.vme.streamer) and signing certificate. See " +
                "android/README.md."
        }
        return text
    }
}
