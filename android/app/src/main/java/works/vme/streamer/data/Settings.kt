package works.vme.streamer.data

import android.content.Context

/**
 * Where the VME backend lives and how to authenticate to it.
 *
 * Previously these three strings were owned by
 * `RosterImportActivity`: it drew the fields, read them, and wrote
 * them to SharedPreferences under keys only it knew. That was fine
 * while import was the only screen that talked to the backend, but
 * it stopped being fine as soon as photos were fetched too -- the
 * settings were buried inside a task flow, so changing a password
 * meant walking into Import and *not* importing.
 *
 * Now they live here, one screen edits them
 * ([works.vme.streamer.ui.SettingsActivity]), and everything else
 * reads.
 *
 * ### Storage
 *
 * Plain SharedPreferences, password included. This is a
 * single-purpose device that lives in a kit bag and streams one
 * club's matches; the threat model is "someone picks up the phone",
 * against which app-private storage is already the meaningful
 * boundary. EncryptedSharedPreferences would add a Jetpack
 * dependency and a keystore failure mode -- an operator locked out
 * courtside because a key migration failed is a worse outcome than
 * the one it prevents.
 */
object Settings {

    private const val PREFS = "vme"
    // Key names carried over verbatim from RosterImportActivity so
    // devices that already had a server configured keep it across
    // the move -- a silent wipe here would look like the setting
    // "didn't save".
    private const val KEY_BASE_URL = "vme.base_url"
    private const val KEY_USER = "vme.user"
    private const val KEY_PASS = "vme.pass"

    private fun prefs(ctx: Context) =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Base URL, scheme + host + port, no trailing slash. Blank when
     *  never configured. */
    fun baseUrl(ctx: Context): String =
        prefs(ctx).getString(KEY_BASE_URL, "").orEmpty().trim().trimEnd('/')

    fun user(ctx: Context): String = prefs(ctx).getString(KEY_USER, "").orEmpty()

    fun password(ctx: Context): String = prefs(ctx).getString(KEY_PASS, "").orEmpty()

    /**
     * Both fields empty -> null, meaning "send no Authorization
     * header at all", which is what a dev backend with no auth
     * wants. Anything else passes through as-is: IIS accepts
     * `DOMAIN\user`, `user@domain` and bare `user` as Basic.
     */
    fun credentials(ctx: Context): VmeClient.Credentials? {
        val u = user(ctx)
        val p = password(ctx)
        return if (u.isEmpty() && p.isEmpty()) null
               else VmeClient.Credentials(u, p)
    }

    /** True once there is at least a URL to talk to. */
    fun isConfigured(ctx: Context): Boolean = baseUrl(ctx).isNotBlank()

    fun save(ctx: Context, baseUrl: String, user: String, password: String) {
        prefs(ctx).edit()
            .putString(KEY_BASE_URL, baseUrl.trim().trimEnd('/'))
            .putString(KEY_USER, user)
            .putString(KEY_PASS, password)
            .apply()
    }
}
