package works.vme.streamer.ui

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.text.method.PasswordTransformationMethod
import android.text.method.ScrollingMovementMethod
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import works.vme.streamer.data.PhotoCache
import works.vme.streamer.data.Roster
import works.vme.streamer.data.Team
import works.vme.streamer.data.TeamStore
import works.vme.streamer.data.Settings
import works.vme.streamer.data.VmeClient

/**
 * Import a roster from a running VME backend.
 *
 * Flow:
 *   1. Type a base URL (e.g. `http://192.168.1.20:8000` or
 *      `https://vme.example`). Stored in SharedPreferences so the
 *      second import is one tap.
 *   2. Tap **List** to fetch `/api/teams`. The response is shown as a
 *      column of buttons.
 *   3. Tap a team to fetch its roster and save it.
 *
 * No auth: VME's roster endpoint is a read of publicly-visible data,
 * and any credential model on the phone would want a matching
 * revocation path that this app has no story for.
 *
 * The whole screen is a scrolling log rather than pretty widgets,
 * because the failure modes here (wrong URL, wrong network, backend
 * down, roster missing) all want a specific error line more than they
 * want a nice UI.
 */
class RosterImportActivity : ComponentActivity() {

    private lateinit var serverView: TextView
    private lateinit var teamsContainer: LinearLayout
    private lateinit var logView: TextView
    private lateinit var store: TeamStore

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = TeamStore(this)
        val root = buildUi()
        setContentView(root)
        root.applySystemBarInsets()
        log("VME roster import")
        if (!Settings.isConfigured(this)) {
            log("No VME server configured yet -- set one in Settings.")
        }
    }

    override fun onResume() {
        super.onResume()
        val base = Settings.baseUrl(this)
        serverView.text = if (base.isBlank()) "No server configured - open Settings"
            else "Server: $base" + (Settings.credentials(this)
                ?.let { "  (auth: ${it.user})" } ?: "  (no auth)")
    }

    // ---- UI --------------------------------------------------------------

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
            setPadding(24, 24, 24, 24)
        }

        root.addView(TextView(this).apply {
            text = "Import roster from VME"
            setTextColor(Color.WHITE)
            textSize = 20f
            typeface = Typeface.DEFAULT_BOLD
        })

        // URL and credentials moved to Settings. Import used to own
        // them, which meant changing a password required walking into
        // a task flow and not completing it. Here we only show where
        // we are pointed.
        serverView = TextView(this).apply {
            setTextColor(Color.parseColor("#9E9E9E"))
            textSize = 12f
            setPadding(0, 8, 0, 8)
        }
        root.addView(serverView)

        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 8, 0, 8)
        }
        actions.addView(Button(this).apply {
            text = "List teams"
            setOnClickListener { onList() }
        })
        actions.addView(Button(this).apply {
            text = "Clear log"
            setOnClickListener { logView.text = "" }
        })
        root.addView(actions)

        root.addView(TextView(this).apply {
            text = "Teams"
            setTextColor(Color.parseColor("#8FE38F"))
            textSize = 12f
            setPadding(0, 16, 0, 4)
        })

        teamsContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        root.addView(ScrollView(this).apply { addView(teamsContainer) },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0
            ).apply { weight = 1f }
        )

        logView = TextView(this).apply {
            setTextColor(Color.parseColor("#8FE38F"))
            textSize = 11f
            typeface = Typeface.MONOSPACE
            movementMethod = ScrollingMovementMethod()
            setPadding(8, 8, 8, 8)
        }
        root.addView(ScrollView(this).apply { addView(logView) },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0
            ).apply { weight = 1f }
        )

        return root
    }

    // ---- actions ---------------------------------------------------------

    private fun onList() {
        val base = Settings.baseUrl(this)
        if (base.isBlank()) {
            log("No server configured -- open Settings first.")
            return
        }
        val creds = Settings.credentials(this)
        log("GET $base/api/teams ${if (creds != null) "(auth: ${creds.user})" else "(no auth)"} ...")
        // Off the main thread; ImportActivity is only ever foreground so
        // a bare Thread is fine and does not need a lifecycle-aware
        // scope. Errors marshal back with runOnUiThread.
        Thread {
            try {
                val teams = VmeClient.listTeams(base, creds)
                runOnUiThread {
                    log("received ${teams.size} team${if (teams.size == 1) "" else "s"}")
                    teamsContainer.removeAllViews()
                    teams.forEach { summary ->
                        teamsContainer.addView(Button(this).apply {
                            text = if (summary.hasRoster) summary.name
                                   else "${summary.name}  (no roster yet)"
                            isEnabled = summary.hasRoster
                            setOnClickListener { onImport(base, summary.name) }
                        })
                    }
                }
            } catch (exc: Exception) {
                runOnUiThread { log("failed: ${exc.message ?: exc.toString()}") }
            }
        }.start()
    }

    private fun onImport(base: String, teamSlug: String) {
        val creds = Settings.credentials(this)
        log("GET $base/api/teams/$teamSlug/roster ...")
        Thread {
            try {
                val roster: Roster = VmeClient.fetchRoster(base, teamSlug, creds)
                // If the server left `team_name` blank (older exports),
                // fall back to the slug — the user can rename on
                // re-import if they care.
                val cleaned = if (roster.teamName.isBlank())
                    roster.copy(teamName = teamSlug) else roster

                // Pull every player's profile picture now, while we
                // are already on a background thread with the server
                // in reach. The pop-up renderer runs on the scoring
                // path and must never do network I/O; see PhotoCache.
                //
                // A re-import clears the old photos first so a player
                // who left the squad does not leave their face in the
                // cache forever.
                PhotoCache.clear(this, teamSlug)
                var fetched = 0
                val withPhotos = cleaned.copy(
                    players = cleaned.players.map { p ->
                        runOnUiThread { log("photo #${p.number} ...") }
                        val bytes = VmeClient.fetchPlayerPhoto(
                            base, teamSlug, p.number, creds)
                        val local = bytes?.let {
                            PhotoCache.store(this, teamSlug, p.number, it)
                        }
                        if (local != null) fetched++
                        p.copy(localPhotoPath = local)
                    }
                )

                // The badge, on the same trip. Drawn on the
                // scoreboard and on every centre card, so it is worth
                // the one extra GET here rather than a fetch on the
                // scoring path.
                runOnUiThread { log("team logo ...") }
                val logoBytes = VmeClient.fetchTeamLogo(base, teamSlug, creds)
                val withLogo = withPhotos.copy(
                    localLogoPath = logoBytes?.let {
                        PhotoCache.storeLogo(this, teamSlug, it)
                    },
                )

                store.save(Team(
                    slug = teamSlug,
                    roster = withLogo,
                    sourceUrl = base,
                    importedAt = System.currentTimeMillis() / 1000L,
                ))
                runOnUiThread {
                    log("saved \"$teamSlug\" with ${withLogo.players.size} players, " +
                        "$fetched photo(s), " +
                        "${if (withLogo.localLogoPath != null) "logo" else "no logo"}.")
                }
            } catch (exc: Exception) {
                runOnUiThread { log("failed: ${exc.message ?: exc.toString()}") }
            }
        }.start()
    }

    // ---- helpers ---------------------------------------------------------


    private fun log(msg: String) {
        runOnUiThread {
            logView.append(msg)
            logView.append("\n")
        }
    }

}
