package works.vme.streamer.ui

import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import works.vme.streamer.data.MatchStore
import works.vme.streamer.data.Team
import works.vme.streamer.data.TeamStore
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Two decisions per new match: which of the imported home teams is
 * playing, and who they are playing. Date defaults to today. Tournament
 * defaults to "Streamed" — the same string the export uses for the
 * folder name so a courtside-created match imports into a discoverable
 * subfolder in VME rather than the tournament root.
 *
 * There is no roster editor here for the opponent. The opponent's
 * roster starts empty; players can be added on the fly from the live
 * screen when a substitution or per-player action needs one.
 */
class MatchSetupActivity : ComponentActivity() {

    private lateinit var teams: List<Team>
    private var selectedTeam: Team? = null

    private lateinit var teamButton: Button
    private lateinit var opponentInput: EditText
    private lateinit var dateInput: EditText
    private lateinit var tournamentInput: EditText

    /**
     * Tournaments this phone has already used, most recent first.
     *
     * Derived from the matches on disk rather than kept as its own
     * list: a tournament exists precisely because a match is in it, so
     * there is nothing to keep in step and nothing to clean up when the
     * last match of one is deleted.
     */
    private val knownTournaments: List<String> by lazy {
        MatchStore(this).list()
            .sortedByDescending { it.date }
            .map { it.tournament.trim() }
            .filter { it.isNotBlank() }
            .distinct()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        teams = TeamStore(this).list()
        if (teams.isEmpty()) {
            // Should not happen — HomeActivity only shows the New Match
            // button when at least one team exists — but bail gracefully
            // if it does.
            AlertDialog.Builder(this)
                .setMessage("Import a team roster first.")
                .setPositiveButton("OK") { _, _ -> finish() }
                .show()
            return
        }
        selectedTeam = teams.first()
        val root = buildUi()
        setContentView(root)
        root.applySystemBarInsets()
    }

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.BLACK)
            setPadding(24, 24, 24, 24)
        }

        root.addView(TextView(this).apply {
            text = "New match"
            setTextColor(Color.WHITE)
            textSize = 22f
            typeface = Typeface.DEFAULT_BOLD
        })

        root.addView(label("Home team"))
        teamButton = Button(this).apply {
            text = selectedTeam?.roster?.teamName ?: "Select..."
            setOnClickListener { pickTeam() }
        }
        root.addView(teamButton)

        root.addView(label("Opponent"))
        opponentInput = EditText(this).apply {
            hint = "e.g. Bellevue"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_WORDS
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
        }
        root.addView(opponentInput)

        root.addView(label("Date"))
        dateInput = EditText(this).apply {
            setText(SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date()))
            inputType = InputType.TYPE_CLASS_TEXT
            setTextColor(Color.WHITE)
        }
        root.addView(dateInput)

        root.addView(label("Tournament"))
        // Typed or picked. Typing stays possible because the first
        // match of a new tournament has nothing to pick from, but by
        // the second match of the weekend the name is already in the
        // list -- and a tournament typed two ways is two folders in
        // VME, which is only discovered later when the matches are
        // split across them.
        tournamentInput = EditText(this).apply {
            setText(knownTournaments.firstOrNull() ?: DEFAULT_TOURNAMENT)
            inputType = InputType.TYPE_CLASS_TEXT
            setTextColor(Color.WHITE)
        }
        val tournamentRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        tournamentRow.addView(tournamentInput, LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
        ))
        if (knownTournaments.isNotEmpty()) {
            tournamentRow.addView(Button(this).apply {
                text = "Pick"
                setOnClickListener { pickTournament() }
            })
        }
        root.addView(tournamentRow)

        // Push actions to the bottom so the New-match button is under
        // the thumb, not scrolled off.
        root.addView(View(this), LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0
        ).apply { weight = 1f })

        val actions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        actions.addView(Button(this).apply {
            text = "Cancel"
            setOnClickListener { finish() }
        })
        actions.addView(Button(this).apply {
            text = "Create + open"
            setOnClickListener { create() }
        })
        root.addView(actions)

        return root
    }

    private fun label(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.parseColor("#8FE38F"))
        textSize = 12f
        setPadding(0, 16, 0, 4)
    }

    private fun pickTeam() {
        val names = teams.map { it.roster.teamName.ifBlank { it.slug } }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle("Home team")
            .setItems(names) { _, which ->
                selectedTeam = teams[which]
                teamButton.text = names[which]
            }
            .show()
    }

    private fun create() {
        val team = selectedTeam ?: return
        val opponent = opponentInput.text.toString().trim()
        if (opponent.isBlank()) {
            AlertDialog.Builder(this)
                .setMessage("Enter an opponent name.")
                .setPositiveButton("OK", null).show()
            return
        }
        val date = dateInput.text.toString().trim()
        val tournament = tournamentInput.text.toString().trim()
            .ifBlank { DEFAULT_TOURNAMENT }
        val match = MatchStore(this).createMatch(
            homeTeam = team, opponentName = opponent,
            date = date, tournament = tournament,
        )
        MatchStore(this).save(match)
        // Jump straight into the live screen — this button is called
        // "Create + open" and the two things reliably happen together.
        startActivity(Intent(this, MatchLiveActivity::class.java).apply {
            putExtra(MatchLiveActivity.EXTRA_SLUG, match.slug)
        })
        finish()
    }

    /** Choose from the tournaments already on this phone. */
    private fun pickTournament() {
        val options = knownTournaments.toTypedArray()
        android.app.AlertDialog.Builder(this)
            .setTitle("Tournament")
            .setItems(options) { _, which -> tournamentInput.setText(options[which]) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    companion object {
        /** Matches the folder the export lands in, so a courtside match
         *  imports into a discoverable subfolder rather than the
         *  tournament root. */
        private const val DEFAULT_TOURNAMENT = "Streamed"
    }
}
