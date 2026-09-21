package works.vme.streamer.ui

import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity

import androidx.core.content.FileProvider
import works.vme.streamer.data.Match
import works.vme.streamer.data.MatchStore
import works.vme.streamer.data.Settings
import works.vme.streamer.data.Team
import works.vme.streamer.data.TeamStore
import works.vme.streamer.data.VmeClient
import works.vme.streamer.data.toJson

/**
 * The app's launcher screen: a list of imported teams and the entry
 * points to import another, create a match, or drop into the original
 * spike harness.
 *
 * Deliberately built with plain views rather than Compose. Compose
 * would flatten some of the boilerplate below, but the spike targets
 * Kotlin 2.3.21 to match RootEncoder's `.kotlin_module` version, and
 * chasing a matching Compose compiler is exactly the sort of yak-shave
 * that killed the first day of this project (see the spike README's
 * "versions are not negotiable"). The screens here are short enough
 * that plain views cost less than a version fight.
 */
class HomeActivity : ComponentActivity() {

    private lateinit var teamsContainer: LinearLayout
    private lateinit var matchesContainer: LinearLayout
    private lateinit var emptyTeamsHint: TextView
    private lateinit var emptyMatchesHint: TextView
    private lateinit var newMatchButton: Button
    private lateinit var serverHint: TextView
    private lateinit var teamStore: TeamStore
    private lateinit var matchStore: MatchStore

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        teamStore = TeamStore(this)
        matchStore = MatchStore(this)
        val root = buildUi()
        setContentView(root)
        root.applySystemBarInsets()
    }

    /** onResume — not onCreate — because a returning trip from the
     *  import screen needs to see the new team without a manual refresh. */
    override fun onResume() {
        super.onResume()
        refresh()
        refreshServerHint()
    }

    private fun refreshServerHint() {
        val base = Settings.baseUrl(this)
        if (base.isBlank()) {
            serverHint.text = "No VME server configured - tap to set one"
            serverHint.setTextColor(Color.parseColor("#FFC107"))
        } else {
            serverHint.text = base
            serverHint.setTextColor(Color.parseColor("#6E6E6E"))
        }
    }

    // ---- UI --------------------------------------------------------------

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
            setPadding(24, 24, 24, 24)
        }

        // Title on the left, Settings tucked to the right of it --
        // a once-per-device setting has no business competing with
        // the task buttons below.
        val titleRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val titleCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        titleCol.addView(header("VME"))
        titleCol.addView(subheader("courtside streamer"))
        titleRow.addView(titleCol, LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
        ))
        titleRow.addView(secondaryButton("Settings") {
            startActivity(Intent(this, SettingsActivity::class.java))
        })
        root.addView(titleRow)

        // Surfaced on Home rather than left for Import to discover,
        // because an unconfigured server makes both actions below
        // fail, and the fix is not on the screen they fail on.
        serverHint = TextView(this).apply {
            textSize = 12f
            setPadding(0, 4, 0, 0)
            setOnClickListener {
                startActivity(Intent(this@HomeActivity, SettingsActivity::class.java))
            }
        }
        root.addView(serverHint)

        // Action row across the top: the two things that move the app
        // forward at all live here, so they never scroll off screen.
        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 16, 0, 16)
        }
        actions.addView(actionButton("Import roster") {
            startActivity(Intent(this, RosterImportActivity::class.java))
        })
        newMatchButton = actionButton("New match") {
            startActivity(Intent(this, MatchSetupActivity::class.java))
        }
        actions.addView(newMatchButton)
        root.addView(actions)

        root.addView(sectionLabel("Matches"))
        emptyMatchesHint = TextView(this).apply {
            text = "No matches yet. Tap \"New match\" to create one."
            setTextColor(Color.parseColor("#AAAAAA"))
            setPadding(0, 8, 0, 8)
        }
        root.addView(emptyMatchesHint)
        matchesContainer = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(ScrollView(this).apply { addView(matchesContainer) },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0
            ).apply { weight = 2f }
        )

        root.addView(sectionLabel("Teams"))
        emptyTeamsHint = TextView(this).apply {
            text = "No teams yet. Tap \"Import roster\" to fetch one from VME."
            setTextColor(Color.parseColor("#AAAAAA"))
            setPadding(0, 8, 0, 8)
        }
        root.addView(emptyTeamsHint)

        teamsContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        root.addView(ScrollView(this).apply { addView(teamsContainer) },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0
            ).apply { weight = 1f }
        )


        return root
    }

    private fun refresh() {
        val teams = teamStore.list()
        teamsContainer.removeAllViews()
        emptyTeamsHint.visibility = if (teams.isEmpty()) View.VISIBLE else View.GONE
        teams.forEach { teamsContainer.addView(teamRow(it)) }

        val matches = matchStore.list()
        matchesContainer.removeAllViews()
        emptyMatchesHint.visibility = if (matches.isEmpty()) View.VISIBLE else View.GONE
        matches.forEach { matchesContainer.addView(matchRow(it)) }

        // Creating a match requires at least one team; make the missing
        // dependency visible rather than crashing inside MatchSetup.
        newMatchButton.isEnabled = teams.isNotEmpty()
    }

    private fun teamRow(team: Team): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 12, 0, 12)
            gravity = Gravity.CENTER_VERTICAL
            // Same gesture as a match row: tapping the thing opens the
            // thing. The buttons on the row keep their own taps.
            isClickable = true
            setOnClickListener { TeamSettingsActivity.open(this@HomeActivity, team.slug) }
        }
        val label = TextView(this).apply {
            text = buildString {
                append(team.roster.teamName.ifBlank { team.slug })
                val count = team.roster.players.size
                append("  ")
                append("($count player${if (count == 1) "" else "s"})")
            }
            setTextColor(Color.WHITE)
            textSize = 16f
        }
        row.addView(label, LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ))
        row.addView(deleteButton { confirmDeleteTeam(team) })
        return row
    }

    private fun matchRow(match: Match): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 12, 0, 12)
            gravity = Gravity.CENTER_VERTICAL
            // The row itself opens the match. "Open" was a button
            // for the one action the whole row already implies --
            // tapping a list item to open it is what every other
            // app does, and it frees the slot for Export.
            isClickable = true
            setOnClickListener { openMatch(match) }
        }
        val label = TextView(this).apply {
            text = buildString {
                append(match.date); append("  ")
                append(match.homeRoster.teamName.ifBlank { match.team })
                append(" vs ")
                append(match.opponent)
                append("  ("); append(match.events.size); append(" events)")
            }
            setTextColor(Color.WHITE)
            textSize = 14f
        }
        row.addView(label, LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ))
        // Export lives here rather than inside the live screen.
        // Exporting is something you do *after* a match, from the
        // list, possibly days later -- not while scoring one. Stop
        // no longer prompts for it either (it ends the stream, not
        // the match), so this is now the only route.
        row.addView(secondaryButton("Send") { confirmSend(match) })
        row.addView(secondaryButton("Export") { exportMatch(match) })
        row.addView(deleteButton { confirmDeleteMatch(match) })
        return row
    }

    // ---- widgets ---------------------------------------------------------

    private fun header(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.WHITE)
        textSize = 28f
        typeface = Typeface.DEFAULT_BOLD
    }

    private fun subheader(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.parseColor("#8FE38F"))
        textSize = 14f
    }

    private fun sectionLabel(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.parseColor("#8FE38F"))
        textSize = 12f
        setPadding(0, 24, 0, 4)
    }

    private fun actionButton(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        setOnClickListener { onClick() }
    }.also { btn ->
        (btn.layoutParams as? LinearLayout.LayoutParams)?.rightMargin = 16
    }

    private fun secondaryButton(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        setOnClickListener { onClick() }
    }

    /**
     * Small destructive button.
     *
     * A full-size Delete sat at the same visual weight as Open and
     * Export, which is backwards: it is the one action on the row
     * nobody wants to hit by accident. Shrunk to roughly a third by
     * zeroing the platform's 88dp `minWidth` and 48dp `minHeight`
     * (which is what actually sets a Button's floor -- padding
     * alone does nothing until those are gone) and dimmed to a
     * muted red.
     *
     * It stays above the ~32dp mark rather than going smaller still,
     * because a genuinely tiny target gets mis-hit in the other
     * direction -- aiming for it and missing onto the row, which now
     * opens the match.
     */
    private fun deleteButton(onClick: () -> Unit) = Button(this).apply {
        text = "\u00D7"
        textSize = 16f
        setTextColor(Color.parseColor("#B03A3A"))
        minWidth = 0
        minimumWidth = 0
        minHeight = 0
        minimumHeight = 0
        val d = resources.displayMetrics.density
        setPadding((10 * d).toInt(), 0, (10 * d).toInt(), 0)
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, (34 * d).toInt(),
        )
    }

    // ---- row actions -----------------------------------------------------

    private fun openMatch(match: Match) {
        startActivity(Intent(this, MatchLiveActivity::class.java).apply {
            putExtra(MatchLiveActivity.EXTRA_SLUG, match.slug)
        })
    }

    /**
     * Share `match.json`. Moved here from the live screen; see
     * [matchRow]. Reads the file the store already wrote after every
     * event, so there is nothing to save first.
     */
    private fun exportMatch(match: Match) {
        val file = matchStore.jsonFile(match.slug)
        if (!file.exists()) {
            toast("Nothing to export yet")
            return
        }
        val uri = FileProvider.getUriForFile(
            this, "$packageName.fileprovider", file,
        )
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "application/json"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, "${match.name} - VME export")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(send, "Share match.json"))
    }

    // ---- send to VME -----------------------------------------------------

    /**
     * Push this match's events into the VME match of the same name.
     *
     * Confirms first, and says plainly that it adds rather than
     * replaces: the import is not idempotent, and a double tap on a
     * flaky gym connection would otherwise silently double every event
     * in the match.
     */
    private fun confirmSend(match: Match) {
        val base = Settings.baseUrl(this)
        if (base.isBlank()) {
            toast("Set the VME address in Settings first")
            return
        }
        if (match.events.isEmpty()) {
            toast("No events to send")
            return
        }
        val where = "${match.team} / ${match.tournament} / ${match.date} / ${match.name}"
        AlertDialog.Builder(this)
            .setTitle("Send ${match.events.size} events?")
            .setMessage(
                "To $where on\n$base\n\n" +
                "The clips must already be uploaded there -- each event is " +
                "placed by the time it happened.\n\n" +
                "Choose Replace if you have sent this match before, or the " +
                "events will be added a second time."
            )
            .setPositiveButton("Add") { _, _ -> sendMatch(match, replace = false) }
            .setNeutralButton("Replace") { _, _ -> sendMatch(match, replace = true) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun sendMatch(match: Match, replace: Boolean) {
        val base = Settings.baseUrl(this)
        val creds = Settings.credentials(this)
        val events = match.toJson().optJSONArray("events") ?: return
        toast("Sending ${events.length()} events...")
        Thread {
            val result = runCatching {
                VmeClient.importEvents(
                    baseUrl = base,
                    team = match.team,
                    tournament = match.tournament,
                    date = match.date,
                    match = match.name,
                    events = events,
                    liberos = match.liberos,
                    replace = replace,
                    creds = creds,
                )
            }
            runOnUiThread {
                result.onSuccess { r ->
                    val parts = mutableListOf("${r.imported} imported")
                    // Worth surfacing: these happened while the camera was
                    // stopped, so they sit at a clip start rather than at
                    // the moment they were tagged.
                    if (r.snapped > 0) parts.add("${r.snapped} moved to a clip start")
                    if (r.skipped.isNotEmpty()) parts.add("${r.skipped.size} skipped")
                    toast(parts.joinToString(", "))
                }.onFailure { exc ->
                    AlertDialog.Builder(this)
                        .setTitle("Send failed")
                        .setMessage(exc.message ?: exc.toString())
                        .setPositiveButton("OK", null)
                        .show()
                }
            }
        }.start()
    }

    // Both deletes confirm. They were bare before, which was
    // survivable when Delete was a deliberate full-size button; next
    // to a row that now opens on tap, an unconfirmed destructive
    // action is a trap.
    private fun confirmDeleteMatch(match: Match) {
        AlertDialog.Builder(this)
            .setTitle("Delete match?")
            .setMessage("${match.name} - ${match.events.size} event(s). " +
                "This cannot be undone. Export first if you need the data.")
            .setPositiveButton("Delete") { _, _ ->
                matchStore.delete(match.slug)
                refresh()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun confirmDeleteTeam(team: Team) {
        AlertDialog.Builder(this)
            .setTitle("Delete team?")
            .setMessage("${team.roster.teamName.ifBlank { team.slug }} - " +
                "${team.roster.players.size} player(s). Matches already " +
                "created keep their own copy of the roster.")
            .setPositiveButton("Delete") { _, _ ->
                teamStore.delete(team.slug)
                refresh()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun toast(msg: String) =
        android.widget.Toast.makeText(this, msg, android.widget.Toast.LENGTH_SHORT).show()
}
