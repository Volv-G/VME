package works.vme.streamer.ui

import android.app.AlertDialog
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity

import androidx.core.content.FileProvider
import works.vme.streamer.RequestAlerts
import works.vme.streamer.data.Match
import works.vme.streamer.data.MatchStore
import works.vme.streamer.data.PhotoCache
import works.vme.streamer.data.ScheduleCache
import works.vme.streamer.data.Settings
import works.vme.streamer.data.SiteClient
import works.vme.streamer.data.Team
import works.vme.streamer.data.TeamStore
import works.vme.streamer.data.VmeClient
import works.vme.streamer.data.VmeOrigin
import works.vme.streamer.data.slugify
import works.vme.streamer.data.toJson
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

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
        // Join requests arrive as notifications (RequestAlerts), so ask
        // here rather than only when a match first goes live.
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 41)
        }
    }

    /** onResume — not onCreate — because a returning trip from the
     *  import screen needs to see the new team without a manual refresh. */
    override fun onResume() {
        super.onResume()
        refresh()
        refreshServerHint()
        refreshSchedule()
        checkRequests()
    }

    /** Make sure the 15-minute check is scheduled, and check now. */
    private fun checkRequests() {
        if (Settings.siteAccess(this) == null) return
        RequestAlerts.schedule(this)
        val app = applicationContext
        Thread {
            runCatching { RequestAlerts.check(app) }
                .onFailure { android.util.Log.i("VMERequests", "check failed: ${it.message}") }
        }.start()
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

        // Upcoming and Matches share one scroll. They are one list in
        // the operator's head -- what is coming, then what has been --
        // and a third scrolling pane on a phone screen would leave
        // each of them two rows tall.
        val listColumn = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        listColumn.addView(buildUpcomingSection())

        listColumn.addView(sectionLabel("Matches"))
        emptyMatchesHint = TextView(this).apply {
            text = "No matches yet. Tap \"New match\" to create one."
            setTextColor(Color.parseColor("#AAAAAA"))
            setPadding(0, 8, 0, 8)
        }
        listColumn.addView(emptyMatchesHint)
        matchesContainer = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        listColumn.addView(matchesContainer)
        root.addView(ScrollView(this).apply { addView(listColumn) },
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

        // The schedule is filtered by the teams on this phone, so it
        // is redrawn whenever they might have changed.
        renderUpcoming()
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
        row.addView(secondaryButton("Copy") { cloneTeam(team) })
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

    // ---- upcoming (from the site) ----------------------------------------
    //
    // The schedule comes from the public site rather than from VME,
    // because the site is reachable from the gym and VME usually is
    // not. VME publishes its fixtures there; changes made here go back
    // the other way as "pending", which VME applies within a minute or
    // so whenever the PC is on. Until it has, they show as (syncing).

    /** One line of the Upcoming list: a fixture VME has, or a change
     *  to the schedule still on its way to VME. */
    private data class UpcomingEntry(
        val team: String,
        val teamName: String,
        /** VME's `tournament/date/name`; null for an add VME has not
         *  applied yet, which therefore has no address to open or edit. */
        val key: String?,
        /** Folder name, the thing VME addresses a tournament by -- or,
         *  for a pending add, whatever was typed. */
        val tournament: String,
        val tournamentName: String,
        val date: String,
        val time: String?,
        val opponent: String,
        val syncing: Boolean,
        val removing: Boolean,
        val live: SiteClient.Live?,
    )

    private lateinit var upcomingSection: LinearLayout
    private lateinit var upcomingContainer: LinearLayout
    private lateinit var upcomingHint: TextView
    private lateinit var scheduleButton: Button
    /** Last schedule seen, from the network or the cached copy. */
    private var schedule: SiteClient.Schedule? = null
    /** Why the last refresh failed, or null. Shown so an operator
     *  looking at yesterday's copy knows that is what it is. */
    private var scheduleError: String? = null
    private var scheduleLoading = false

    private fun buildUpcomingSection(): View {
        upcomingSection = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL or Gravity.BOTTOM
        }
        header.addView(sectionLabel("Upcoming"), LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
        ))
        scheduleButton = secondaryButton("＋ Schedule match") { showScheduleDialog(null) }
        header.addView(scheduleButton)
        upcomingSection.addView(header)
        upcomingHint = TextView(this).apply {
            setTextColor(Color.parseColor("#AAAAAA"))
            textSize = 12f
            setPadding(0, 4, 0, 4)
        }
        upcomingSection.addView(upcomingHint)
        upcomingContainer = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        upcomingSection.addView(upcomingContainer)
        return upcomingSection
    }

    /**
     * Fetch the schedule in the background and redraw. The cached copy
     * is already on screen by then (see [renderUpcoming]), so a failed
     * fetch costs nothing but a hint line.
     */
    private fun refreshSchedule() {
        val access = Settings.siteAccess(this) ?: return
        if (scheduleLoading) return
        scheduleLoading = true
        val app = applicationContext
        Thread {
            val r = runCatching { SiteClient.fetchSchedule(access) }
            r.onSuccess { (_, raw) -> ScheduleCache.save(app, raw) }
            runOnUiThread {
                scheduleLoading = false
                r.onSuccess { (parsed, _) ->
                    schedule = parsed
                    scheduleError = null
                }.onFailure { scheduleError = it.message ?: it.toString() }
                if (!isDestroyed) renderUpcoming()
            }
        }.start()
    }

    private fun renderUpcoming() {
        val access = Settings.siteAccess(this)
        val sched = schedule ?: ScheduleCache.load(this)?.also { schedule = it }
        // Nothing linked and nothing remembered: the section would be
        // a header over an empty list, so leave it out altogether.
        // Settings says how to link.
        if (access == null && sched == null) {
            upcomingSection.visibility = View.GONE
            return
        }
        upcomingSection.visibility = View.VISIBLE
        val teams = teamStore.list()
        scheduleButton.isEnabled = access != null && teams.isNotEmpty()

        val entries = if (sched == null) emptyList()
            else upcomingEntries(sched, teams.map { it.slug }.toSet())
        val strays = if (sched == null) emptyList() else strayLive(sched, entries, teams)
        upcomingContainer.removeAllViews()
        strays.forEach { upcomingContainer.addView(liveRow(it)) }
        val showTeam = entries.map { it.team }.distinct().size > 1
        entries.forEach { upcomingContainer.addView(upcomingRow(it, showTeam)) }

        val err = scheduleError
        upcomingHint.text = when {
            sched == null && err != null -> "Could not load the schedule: $err"
            sched == null -> "Loading the schedule..."
            err != null -> "Offline - showing the last schedule seen"
            entries.isEmpty() && strays.isEmpty() ->
                "Nothing scheduled for your teams. Tap ＋ to add a match."
            else -> ""
        }
        upcomingHint.visibility = if (upcomingHint.text.isEmpty()) View.GONE else View.VISIBLE
    }

    /**
     * Fold the site's answer into one list for the teams on this phone.
     *
     * A pending update is shown with its new values, a pending delete
     * on the row it removes, and a pending add as a row of its own --
     * each marked, so the operator can see the change was taken without
     * mistaking it for one VME has made. Several pending changes to the
     * same match: the latest wins, as it will when VME applies them.
     */
    private fun upcomingEntries(
        sched: SiteClient.Schedule, teamSlugs: Set<String>,
    ): List<UpcomingEntry> {
        val out = mutableListOf<UpcomingEntry>()
        for (t in sched.teams) {
            if (t.team !in teamSlugs) continue
            fun tournamentName(slugOrTyped: String) = t.tournaments.firstOrNull {
                it.slug == slugOrTyped || it.name.equals(slugOrTyped, ignoreCase = true)
            }?.name ?: slugOrTyped.replace('_', ' ')
            val latestByKey = t.pending.filter { it.key != null }
                .sortedBy { it.submittedAt }
                .associateBy { it.key }
            for (u in t.upcoming) {
                val p = latestByKey[u.key]
                val live = sched.live.firstOrNull { it.team == t.team && it.matchKey == u.key }
                out += when (p?.action) {
                    "update" -> UpcomingEntry(
                        t.team, t.name, u.key,
                        u.tournament, u.tournamentName,
                        p.date.ifBlank { u.date }, p.time, p.opponent.ifBlank { u.opponent },
                        syncing = true, removing = false, live = live,
                    )
                    "delete" -> UpcomingEntry(
                        t.team, t.name, u.key, u.tournament, u.tournamentName,
                        u.date, u.time, u.opponent,
                        syncing = true, removing = true, live = live,
                    )
                    else -> UpcomingEntry(
                        t.team, t.name, u.key, u.tournament, u.tournamentName,
                        u.date, u.time, u.opponent,
                        syncing = false, removing = false, live = live,
                    )
                }
            }
            for (p in t.pending) {
                if (p.action != "add") continue
                out += UpcomingEntry(
                    t.team, t.name, null, p.tournament, tournamentName(p.tournament),
                    p.date, p.time, p.opponent,
                    syncing = true, removing = false, live = null,
                )
            }
        }
        // Date, then time; an unknown time sorts last on its day, since
        // "some time that day" is the less pressing thing to see first.
        return out.sortedWith(compareBy<UpcomingEntry> { it.date }
            .thenBy { it.time ?: "99:99" }
            .thenBy { it.opponent.lowercase() })
    }

    /** Live streams for this phone's teams that no Upcoming row
     *  carries -- a match typed in on a phone, say -- so they are not
     *  missed just because they are not on VME's schedule. */
    private fun strayLive(
        sched: SiteClient.Schedule, entries: List<UpcomingEntry>, teams: List<Team>,
    ): List<SiteClient.Live> {
        val slugs = teams.map { it.slug }.toSet()
        val shown = entries.mapNotNull { it.live }.toSet()
        return sched.live.filter { it.team in slugs && it !in shown }
    }

    private fun upcomingRow(e: UpcomingEntry, showTeam: Boolean): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 10, 0, 10)
            isClickable = true
            isLongClickable = true
            setOnClickListener { openUpcoming(e) }
            setOnLongClickListener { upcomingActions(e); true }
        }
        row.addView(TextView(this).apply {
            text = buildString {
                if (e.live != null) append("🔴 LIVE  ")
                append(whenLabel(e.date, e.time))
                append(" — vs ")
                append(e.opponent)
                append(" · ")
                append(e.tournamentName)
                if (e.removing) append("  (removing)")
                else if (e.syncing) append("  (syncing)")
            }
            setTextColor(if (e.removing) Color.parseColor("#777777") else Color.WHITE)
            if (e.removing) paintFlags = paintFlags or android.graphics.Paint.STRIKE_THRU_TEXT_FLAG
            textSize = 14f
        })
        if (showTeam) {
            row.addView(TextView(this).apply {
                text = e.teamName
                setTextColor(Color.parseColor("#8A8A8A"))
                textSize = 11f
            })
        }
        return row
    }

    /** A live stream with no schedule row; tapping it opens the
     *  stream, which is the one thing there is to do with it. */
    private fun liveRow(l: SiteClient.Live): View = TextView(this).apply {
        text = "🔴 LIVE — vs ${l.opponent.ifBlank { "?" }}"
        setTextColor(Color.WHITE)
        textSize = 14f
        setPadding(0, 10, 0, 10)
        isClickable = true
        setOnClickListener {
            if (l.url.isNotBlank()) runCatching {
                startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(l.url)))
            }
        }
    }

    /** "Wed Dec 2 · 6:30 PM", or the day alone when no time is set.
     *  The clock follows the phone's 12/24-hour setting. */
    private fun whenLabel(date: String, time: String?): String {
        val d = runCatching { SimpleDateFormat("yyyy-MM-dd", Locale.US).parse(date) }.getOrNull()
        val day = d?.let { SimpleDateFormat("EEE MMM d", Locale.US).format(it) } ?: date
        val t = time?.let { formatTime(it) } ?: return day
        return "$day · $t"
    }

    private fun formatTime(hhmm: String): String {
        if (android.text.format.DateFormat.is24HourFormat(this)) return hhmm
        val t = runCatching { SimpleDateFormat("HH:mm", Locale.US).parse(hhmm) }.getOrNull()
            ?: return hhmm
        return SimpleDateFormat("h:mm a", Locale.US).format(t)
    }

    /** VME's address for an upcoming entry, from its key: the last
     *  segment is the match folder, the two before it the tournament
     *  and the date. The entry's own fields fill in a short key. */
    private fun originOf(e: UpcomingEntry): VmeOrigin? {
        val key = e.key ?: return null
        val parts = key.split('/')
        return if (parts.size >= 3) VmeOrigin(
            team = e.team,
            tournament = parts[parts.size - 3],
            date = parts[parts.size - 2],
            name = parts.last(),
        ) else VmeOrigin(e.team, e.tournament, e.date, key.substringAfterLast('/'))
    }

    /**
     * Open the phone match for this fixture, making it first if there
     * is none. "The one for this fixture" is the one carrying the same
     * VME address, so a second tap -- or a tap after the match was
     * started from New match with the fixture imported -- reopens it
     * rather than starting a duplicate beside it.
     */
    private fun openUpcoming(e: UpcomingEntry) {
        val origin = originOf(e)
        if (origin == null) {
            toast("Waiting for VME to add it - try again in a minute")
            return
        }
        matchStore.list().firstOrNull { it.vmeOrigin == origin }?.let {
            openMatch(it)
            return
        }
        if (e.removing) {
            toast("This match is being removed from the schedule")
            return
        }
        val team = teamStore.load(e.team) ?: run {
            toast("Import ${e.teamName} first")
            return
        }
        createFromUpcoming(team, origin, e.opponent)
    }

    /**
     * Make the phone match for an upcoming fixture -- what New match
     * does after "Import fixture from VME", in one tap.
     *
     * VME's own fixture details (the opponent as the library spells it,
     * their colour and badge) are asked for if VME is configured, but
     * waited on for [META_WAIT_MS] at most: this is usually tapped at
     * the gym, where the home PC is out of reach and a connect timeout
     * would be ten seconds of nothing. Whichever comes first wins; a
     * late answer is dropped. Without it, an opponent played before on
     * this phone still brings their colour, badge and players along,
     * as in MatchSetup.
     */
    private fun createFromUpcoming(team: Team, origin: VmeOrigin, opponent: String) {
        val done = AtomicBoolean(false)
        fun finish(meta: VmeClient.MatchMeta?) {
            if (!done.compareAndSet(false, true)) return
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                val m = buildMatchFromUpcoming(team, origin, opponent, meta)
                refresh()
                openMatch(m)
            }
        }
        val base = Settings.baseUrl(this)
        if (base.isBlank()) { finish(null); return }
        val creds = Settings.credentials(this)
        toast("Setting up vs $opponent...")
        Thread {
            val meta = runCatching {
                VmeClient.fetchMatchMeta(
                    base, origin.team, origin.tournament, origin.date, origin.name, creds,
                )
            }.getOrNull()
            finish(meta)
        }.start()
        window.decorView.postDelayed({ finish(null) }, META_WAIT_MS)
    }

    private fun buildMatchFromUpcoming(
        team: Team, origin: VmeOrigin, opponent: String, meta: VmeClient.MatchMeta?,
    ): Match {
        val name = meta?.opponent?.ifBlank { null } ?: opponent
        // Before createMatch, which does not save, so the list is the
        // phone's earlier matches only.
        val reuse = matchStore.knownOpponents().firstOrNull {
            MatchStore.opponentKey(it.teamName) == MatchStore.opponentKey(name)
        }
        var match = matchStore.createMatch(
            homeTeam = team, opponentName = name,
            date = origin.date, tournament = origin.tournament,
            vmeOrigin = origin,
        )
        // Same precedence as MatchSetup: this fixture's own branding
        // from VME first, then whatever the opponent wore last time.
        // The badge is copied, so deleting the earlier match does not
        // blank this one.
        val color = meta?.opponentColor ?: reuse?.teamColor
        val logo = meta?.opponentLogo
            ?: reuse?.localLogoPath?.let { runCatching { java.io.File(it).readBytes() }.getOrNull() }
        val players = reuse?.players.orEmpty()
        if (color != null || logo != null || players.isNotEmpty()) {
            val logoPath = logo?.let {
                PhotoCache.storeLogo(this, PhotoCache.opponentKey(match.slug), it)
            }
            match = match.copy(
                opponentRoster = match.opponentRoster.copy(
                    teamColor = color ?: match.opponentRoster.teamColor,
                    localLogoPath = logoPath ?: match.opponentRoster.localLogoPath,
                    players = players,
                ),
            )
        }
        matchStore.save(match)
        return match
    }

    /** Long-press: change or remove the fixture. Only for one VME has
     *  -- a pending add has no key to name it by yet. */
    private fun upcomingActions(e: UpcomingEntry) {
        if (Settings.siteAccess(this) == null) {
            toast("Not linked to the site - see Settings")
            return
        }
        if (e.key == null) {
            toast("Still syncing to VME - it can be changed once VME has it")
            return
        }
        AlertDialog.Builder(this)
            .setTitle("vs ${e.opponent} · ${whenLabel(e.date, e.time)}")
            .setItems(arrayOf("Edit date, time, opponent", "Delete from schedule")) { _, i ->
                if (i == 0) showScheduleDialog(e) else confirmDeleteUpcoming(e)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun confirmDeleteUpcoming(e: UpcomingEntry) {
        AlertDialog.Builder(this)
            .setTitle("Delete from the schedule?")
            .setMessage("vs ${e.opponent}, ${whenLabel(e.date, e.time)} · " +
                "${e.tournamentName}.\n\nVME removes it the next time it syncs.")
            .setPositiveButton("Delete") { _, _ ->
                submitSchedule(e.team, "delete", e.key, e.tournament, e.date, e.time, e.opponent)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /**
     * Add a fixture ([existing] null) or change one.
     *
     * Adding asks for the team and tournament too; editing keeps both,
     * because moving a match between tournaments or teams is a
     * different thing in VME (a folder move) and not what anyone means
     * by "the time changed".
     *
     * The tournament is picked from the team's, or typed: the first
     * match of a new league has nothing to pick from, and VME creates
     * a tournament it has not seen. A typed name that matches an
     * existing one is sent as that one, so "fall league" does not
     * become a second folder beside "Fall_League".
     */
    private fun showScheduleDialog(existing: UpcomingEntry?) {
        val access = Settings.siteAccess(this) ?: run {
            toast("Not linked to the site - see Settings")
            return
        }
        val teams = teamStore.list()
        if (teams.isEmpty()) { toast("Import a roster first"); return }
        val sched = schedule
        val onSite = sched?.teams?.map { it.team }?.toSet().orEmpty()

        var team: Team = existing?.let { e -> teams.firstOrNull { it.slug == e.team } }
            ?: teams.firstOrNull { it.slug in onSite } ?: teams.first()
        var date = existing?.date ?: SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
        var time: String? = existing?.time

        fun tournamentsOf(t: Team) =
            sched?.teams?.firstOrNull { it.team == t.slug }?.tournaments.orEmpty()

        val d = resources.displayMetrics.density
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding((20 * d).toInt(), (8 * d).toInt(), (20 * d).toInt(), 0)
        }
        fun formLabel(text: String) = TextView(this).apply {
            this.text = text
            textSize = 12f
            setPadding(0, (10 * d).toInt(), 0, 0)
        }

        val tournamentInput = EditText(this).apply {
            hint = "e.g. Fall League"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_WORDS
            setSingleLine()
            setText(tournamentsOf(team).firstOrNull()?.name.orEmpty())
        }
        if (existing == null) {
            form.addView(formLabel("Team"))
            val teamButton = Button(this)
            teamButton.text = team.roster.teamName.ifBlank { team.slug }
            teamButton.setOnClickListener {
                val names = teams.map { it.roster.teamName.ifBlank { it.slug } }.toTypedArray()
                AlertDialog.Builder(this)
                    .setTitle("Team")
                    .setItems(names) { _, i ->
                        team = teams[i]
                        teamButton.text = names[i]
                        tournamentInput.setText(tournamentsOf(team).firstOrNull()?.name.orEmpty())
                    }
                    .show()
            }
            form.addView(teamButton)

            form.addView(formLabel("Tournament"))
            val tRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            tRow.addView(tournamentInput, LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
            ))
            tRow.addView(Button(this).apply {
                text = "Pick"
                setOnClickListener {
                    val options = tournamentsOf(team)
                    if (options.isEmpty()) {
                        toast("No tournaments yet for this team - type one")
                        return@setOnClickListener
                    }
                    AlertDialog.Builder(this@HomeActivity)
                        .setTitle("Tournament")
                        .setItems(options.map { it.name }.toTypedArray()) { _, i ->
                            tournamentInput.setText(options[i].name)
                        }
                        .show()
                }
            })
            form.addView(tRow)
        } else {
            form.addView(formLabel("${existing.teamName} · ${existing.tournamentName}"))
        }

        form.addView(formLabel("Date"))
        val dateButton = Button(this)
        fun showDate() { dateButton.text = whenLabel(date, null) }
        showDate()
        dateButton.setOnClickListener {
            val cal = Calendar.getInstance()
            runCatching { SimpleDateFormat("yyyy-MM-dd", Locale.US).parse(date) }
                .getOrNull()?.let { cal.time = it }
            DatePickerDialog(this, { _, y, m, day ->
                date = "%04d-%02d-%02d".format(y, m + 1, day)
                showDate()
            }, cal.get(Calendar.YEAR), cal.get(Calendar.MONTH), cal.get(Calendar.DAY_OF_MONTH))
                .show()
        }
        form.addView(dateButton)

        // Time is optional: plenty of fixtures are on the schedule
        // before the hall has said when, and a made-up 18:00 would be
        // worse than "time not known".
        form.addView(formLabel("Time (optional)"))
        val timeRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val timeButton = Button(this)
        fun showTime() { timeButton.text = time?.let { formatTime(it) } ?: "No time set" }
        showTime()
        timeButton.setOnClickListener {
            val (h, m) = time?.split(':')?.mapNotNull { it.toIntOrNull() }
                ?.takeIf { it.size == 2 }?.let { it[0] to it[1] } ?: (18 to 0)
            TimePickerDialog(this, { _, hh, mm ->
                time = "%02d:%02d".format(hh, mm)
                showTime()
            }, h, m, android.text.format.DateFormat.is24HourFormat(this)).show()
        }
        timeRow.addView(timeButton, LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f,
        ))
        timeRow.addView(Button(this).apply {
            text = "Clear"
            setOnClickListener { time = null; showTime() }
        })
        form.addView(timeRow)

        form.addView(formLabel("Opponent"))
        val opponentInput = EditText(this).apply {
            hint = "e.g. Juanita"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_WORDS
            setSingleLine()
            setText(existing?.opponent.orEmpty())
        }
        form.addView(opponentInput)

        val dialog = AlertDialog.Builder(this)
            .setTitle(if (existing == null) "Schedule a match" else "Edit match")
            .setView(ScrollView(this).apply { addView(form) })
            .setPositiveButton(if (existing == null) "Schedule" else "Save", null)
            .setNegativeButton("Cancel", null)
            .create()
        // The positive button is wired after show() so a missing field
        // keeps the dialog open with what was typed, instead of closing
        // it and losing the lot.
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val opponent = opponentInput.text.toString().trim()
                if (opponent.isBlank()) { toast("Enter the opponent"); return@setOnClickListener }
                val tournament = if (existing != null) existing.tournament else {
                    val typed = tournamentInput.text.toString().trim()
                    if (typed.isBlank()) { toast("Enter the tournament"); return@setOnClickListener }
                    tournamentsOf(team).firstOrNull {
                        it.slug.equals(typed, ignoreCase = true) ||
                            it.name.equals(typed, ignoreCase = true)
                    }?.slug ?: typed
                }
                dialog.dismiss()
                submitSchedule(
                    team.slug, if (existing == null) "add" else "update",
                    existing?.key, tournament, date, time, opponent, access,
                )
            }
        }
        dialog.show()
    }

    private fun submitSchedule(
        team: String, action: String, key: String?,
        tournament: String, date: String, time: String?, opponent: String,
        access: SiteClient.Access? = Settings.siteAccess(this),
    ) {
        if (access == null) { toast("Not linked to the site - see Settings"); return }
        Thread {
            val r = runCatching {
                SiteClient.submitSchedule(access, team, action, key, tournament, date, time, opponent)
            }
            runOnUiThread {
                r.onSuccess {
                    toast("Sent - VME picks it up within a minute when the PC is on")
                    refreshSchedule()
                }.onFailure {
                    AlertDialog.Builder(this)
                        .setTitle("Could not update the schedule")
                        .setMessage(it.message ?: it.toString())
                        .setPositiveButton("OK", null)
                        .show()
                }
            }
        }.start()
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
                // Where VME keeps this match. For a fixture imported
                // from VME that is the address it came from, kept
                // verbatim; for a match created on the phone it is the
                // one these fields describe. Rebuilding it from the
                // phone's own numbering sent imported fixtures to a
                // match that does not exist - the phone counts a day's
                // matches from 01, VME from 00.
                val origin = match.vmeOrigin
                VmeClient.importEvents(
                    baseUrl = base,
                    team = origin?.team ?: match.team,
                    tournament = origin?.tournament ?: match.tournament,
                    date = origin?.date ?: match.date,
                    match = origin?.name ?: match.name,
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
                    val msg = exc.message ?: exc.toString()
                    // "The server has no such match" is the one failure
                    // the operator can actually fix from here, so it
                    // gets a way out rather than an error to write down.
                    // Matches created before the fixture address was
                    // recorded land here, as does anything renamed in
                    // VME since.
                    if (msg.contains("404") || msg.contains("Match not found")) {
                        AlertDialog.Builder(this)
                            .setTitle("No such match in VME")
                            .setMessage("$msg\n\nPick the match these events belong to?")
                            .setPositiveButton("Pick match...") { _, _ ->
                                pickVmeTarget(match, replace)
                            }
                            .setNegativeButton("Cancel", null)
                            .show()
                    } else {
                        AlertDialog.Builder(this)
                            .setTitle("Send failed")
                            .setMessage(msg)
                            .setPositiveButton("OK", null)
                            .show()
                    }
                }
            }
        }.start()
    }

    /**
     * Point a match at the VME fixture its events belong to, then send.
     *
     * Two pickers - tournament, then match - because that is how VME
     * addresses a match and the phone cannot infer either one. The
     * choice is saved on the match ([Match.vmeOrigin]), so a re-send or
     * a later correction goes to the same place without asking again.
     */
    private fun pickVmeTarget(match: Match, replace: Boolean) {
        val base = Settings.baseUrl(this)
        val creds = Settings.credentials(this)
        val team = match.vmeOrigin?.team ?: match.team
        toast("Loading tournaments...")
        Thread {
            val tours = runCatching { VmeClient.listTournaments(base, team, creds) }
            runOnUiThread {
                tours.onSuccess { list ->
                    if (list.isEmpty()) { toast("No tournaments for $team"); return@onSuccess }
                    AlertDialog.Builder(this)
                        .setTitle("Tournament")
                        .setItems(list.toTypedArray()) { _, i ->
                            pickVmeMatch(match, replace, team, list[i])
                        }
                        .setNegativeButton("Cancel", null)
                        .show()
                }.onFailure { toast("Could not list tournaments: ${it.message}") }
            }
        }.start()
    }

    private fun pickVmeMatch(
        match: Match, replace: Boolean, team: String, tournament: String,
    ) {
        val base = Settings.baseUrl(this)
        val creds = Settings.credentials(this)
        Thread {
            val res = runCatching { VmeClient.listMatches(base, team, tournament, creds) }
            runOnUiThread {
                res.onSuccess { list ->
                    if (list.isEmpty()) { toast("No matches in $tournament"); return@onSuccess }
                    // The date is part of the label rather than a
                    // filter: a match tagged past midnight, or a
                    // fixture VME files under the day it was played,
                    // would otherwise be hidden by an exact match.
                    val labels = list.map { it.pickerLabel() }.toTypedArray()
                    AlertDialog.Builder(this)
                        .setTitle(tournament)
                        .setItems(labels) { _, i ->
                            val chosen = list[i]
                            val updated = match.copy(
                                vmeOrigin = works.vme.streamer.data.VmeOrigin(
                                    team = team,
                                    tournament = tournament,
                                    date = chosen.date,
                                    name = chosen.name,
                                ),
                            )
                            matchStore.save(updated)
                            refresh()
                            sendMatch(updated, replace)
                        }
                        .setNegativeButton("Cancel", null)
                        .show()
                }.onFailure { toast("Could not list matches: ${it.message}") }
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

    /**
     * Duplicate a team, photos and all.
     *
     * For trying things against a real roster without risking it --
     * renaming players, rewriting the stream templates, running a
     * throwaway match -- and for the club that fields two sides from
     * one squad, where the second roster starts as the first.
     *
     * The copy is independent on disk: its own team folder and its own
     * photo directory, with the roster's absolute photo paths rewritten
     * to point inside it. Sharing the image files would have meant a
     * re-import of either team wiping the other's faces.
     *
     * Matches are not copied. A match already holds its own snapshot of
     * the roster it was created with, so nothing here is left dangling,
     * and cloning a season's worth of matches is not what "copy this
     * team" asks for.
     */
    private fun cloneTeam(team: Team) {
        val sourceName = team.roster.teamName.ifBlank { team.slug }
        val input = android.widget.EditText(this).apply {
            setText("$sourceName (copy)")
            setSelection(text.length)
        }
        AlertDialog.Builder(this)
            .setTitle("Copy team")
            .setMessage("A new team with the same roster, colours, photos and stream settings.")
            .setView(input)
            .setPositiveButton("Copy") { _, _ ->
                val name = input.text.toString().trim().ifBlank { "$sourceName (copy)" }
                doCloneTeam(team, name)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun doCloneTeam(team: Team, newName: String) {
        val slug = freeSlug(slugify(newName))
        // Photos first: the rewritten roster paths have to point at
        // files that already exist, or a failed copy would leave a
        // roster claiming faces that are not there.
        val photoDir = PhotoCache.copy(this, team.slug, slug)
        fun remap(path: String?): String? =
            path?.let { java.io.File(photoDir, java.io.File(it).name).absolutePath }
        val roster = team.roster.copy(
            teamName = newName,
            localLogoPath = remap(team.roster.localLogoPath),
            players = team.roster.players.map { p ->
                p.copy(localPhotoPath = remap(p.localPhotoPath))
            },
        )
        teamStore.save(
            team.copy(
                slug = slug,
                roster = roster,
                // Not imported from anywhere: this one came off the
                // phone, and claiming a source URL would make a later
                // re-import look like a refresh of the original.
                sourceUrl = "",
                importedAt = 0L,
            )
        )
        refresh()
        toast("Copied to $newName")
    }

    /** `slug`, or `slug_2`, `slug_3`... - the first one no team is
     *  using. Two copies of the same team is a normal thing to want. */
    private fun freeSlug(base: String): String {
        if (teamStore.load(base) == null) return base
        var n = 2
        while (teamStore.load("${base}_$n") != null) n++
        return "${base}_$n"
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

    private companion object {
        /** How long a tap on an upcoming fixture waits for VME's
         *  details before making the match without them. */
        const val META_WAIT_MS = 4_000L
    }
}
