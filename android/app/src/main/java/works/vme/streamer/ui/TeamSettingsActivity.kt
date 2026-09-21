package works.vme.streamer.ui

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import works.vme.streamer.data.StreamConfig
import works.vme.streamer.data.Team
import works.vme.streamer.data.TeamStore

/**
 * Per-team broadcast settings: how a stream is named, where it is
 * filed, and who gets told it has started.
 *
 * Reached by tapping a team on the home screen. Separate from
 * [SettingsActivity], which holds the one server this phone talks to --
 * that is a property of the device, these are properties of a club, and
 * a phone that covers two teams needs two sets.
 *
 * The naming fields deliberately mirror VME's per-team `youtube` block
 * (`title_template`, `description_template`, `playlist_id`), including
 * the placeholder names, so a live stream and the later upload of the
 * same match are named the same way. They arrive filled in from the
 * server at roster import; editing here is for the cases where the
 * server is not reachable, which is most of them at a gym.
 */
class TeamSettingsActivity : ComponentActivity() {

    private lateinit var store: TeamStore
    private lateinit var team: Team

    private lateinit var titleInput: EditText
    private lateinit var descriptionInput: EditText
    private lateinit var playlistInput: EditText
    private lateinit var announceInput: EditText
    private lateinit var subjectInput: EditText
    private lateinit var bodyInput: EditText
    private lateinit var preview: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val slug = intent.getStringExtra(EXTRA_SLUG) ?: run { finish(); return }
        store = TeamStore(this)
        team = store.load(slug) ?: run { finish(); return }
        val cfg = team.stream

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 32, 32, 32)
            setBackgroundColor(Color.BLACK)
        }

        root.addView(TextView(this).apply {
            text = team.roster.teamName.ifBlank { team.slug }
            setTextColor(Color.WHITE)
            textSize = 24f
            typeface = Typeface.DEFAULT_BOLD
        })
        root.addView(hint(
            "Placeholders: {team}  {opponent}  {date}  {tournament}"
        ))

        root.addView(label("Stream title"))
        titleInput = field(cfg.titleTemplate, lines = 2)
        root.addView(titleInput)

        root.addView(label("Description"))
        descriptionInput = field(cfg.descriptionTemplate, lines = 4)
        root.addView(descriptionInput)

        // Rendered against a stand-in fixture, because a template is
        // hard to check in the abstract and impossible to check at
        // courtside speed.
        preview = TextView(this).apply {
            setTextColor(Color.parseColor("#8FE38F"))
            textSize = 12f
            setPadding(0, 12, 0, 4)
        }
        root.addView(preview)
        root.addView(label("Playlist ID"))
        playlistInput = field(cfg.playlistId, lines = 1)
        root.addView(playlistInput)
        root.addView(hint(
            "Each broadcast is added to this playlist. The ID is the " +
            "list= part of a playlist URL. Leave blank for none."
        ))

        root.addView(label("Email subject"))
        subjectInput = field(cfg.emailSubjectTemplate, lines = 2)
        root.addView(subjectInput)

        root.addView(label("Email body"))
        bodyInput = field(cfg.emailBodyTemplate, lines = 5)
        root.addView(bodyInput)
        root.addView(hint(
            "These two additionally take {title} - the stream title " +
            "rendered above - and {url}, the YouTube watch link."
        ))

        root.addView(label("Announce to"))
        announceInput = field(cfg.announceTo, lines = 3)
        root.addView(announceInput)
        root.addView(hint(
            "One address per line, emailed by VME when the broadcast " +
            "starts. A Google Group works best here: membership is then " +
            "managed in Google rather than on this phone."
        ))

        root.addView(View(this), LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 24,
        ))

        val actions = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }
        actions.addView(Button(this).apply {
            text = "Cancel"
            setOnClickListener { finish() }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        actions.addView(Button(this).apply {
            text = "Save"
            setOnClickListener { save() }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(actions)

        val scroll = ScrollView(this).apply { addView(root) }
        setContentView(scroll)
        scroll.applySystemBarInsets()
        // Registered after every field exists: these are lateinit,
        // and the preview reads both.
        for (f in listOf(titleInput, subjectInput)) {
            f.addTextChangedListener(object : android.text.TextWatcher {
                override fun afterTextChanged(s: android.text.Editable?) = refreshPreview()
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            })
        }

        refreshPreview()
    }

    /**
     * Render the templates against a stand-in fixture.
     *
     * The title is previewed, then fed into the subject preview as
     * `{title}` -- which is exactly what happens at go-live, so a
     * subject that references it is shown resolved rather than as a
     * placeholder the operator has to hold in their head.
     */
    private fun refreshPreview() {
        val cfg = StreamConfig()
        val home = team.roster.teamName.ifBlank { team.slug }
        val title = cfg.render(
            titleInput.text.toString(),
            StreamConfig.vars(home, "Bellevue", "2026-09-20", "Fall Classic"),
        )
        val full = StreamConfig.vars(
            home, "Bellevue", "2026-09-20", "Fall Classic",
            title = title, url = "https://youtu.be/xxxxxxxxxxx",
        )
        preview.text = buildString {
            append("Title:    ").append(title)
            // Built before the subject field exists on the first pass.
            if (this@TeamSettingsActivity::subjectInput.isInitialized) {
                append("\nSubject:  ")
                append(cfg.render(subjectInput.text.toString(), full))
            }
        }
    }

    private fun save() {
        val cfg = StreamConfig(
            titleTemplate = titleInput.text.toString().trim()
                .ifBlank { StreamConfig.DEFAULT_TITLE },
            descriptionTemplate = descriptionInput.text.toString().trim()
                .ifBlank { StreamConfig.DEFAULT_DESCRIPTION },
            playlistId = playlistInput.text.toString().trim(),
            announceTo = announceInput.text.toString().trim(),
            emailSubjectTemplate = subjectInput.text.toString().trim()
                .ifBlank { StreamConfig.DEFAULT_EMAIL_SUBJECT },
            emailBodyTemplate = bodyInput.text.toString().trim()
                .ifBlank { StreamConfig.DEFAULT_EMAIL_BODY },
        )
        store.save(team.copy(stream = cfg))
        Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
        finish()
    }

    // ---- widgets ------------------------------------------------------

    private fun label(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.parseColor("#8FE38F"))
        textSize = 12f
        typeface = Typeface.DEFAULT_BOLD
        setPadding(0, 20, 0, 4)
    }

    private fun hint(text: String) = TextView(this).apply {
        this.text = text
        setTextColor(Color.parseColor("#909090"))
        textSize = 11f
        setPadding(0, 4, 0, 0)
    }

    private fun field(value: String, lines: Int) = EditText(this).apply {
        setText(value)
        setTextColor(Color.WHITE)
        textSize = 14f
        inputType = if (lines > 1)
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
        else InputType.TYPE_CLASS_TEXT
        setSingleLine(lines == 1)
        minLines = lines
    }

    companion object {
        private const val EXTRA_SLUG = "team_slug"

        fun open(context: Context, slug: String) {
            context.startActivity(
                Intent(context, TeamSettingsActivity::class.java)
                    .putExtra(EXTRA_SLUG, slug)
            )
        }
    }
}
