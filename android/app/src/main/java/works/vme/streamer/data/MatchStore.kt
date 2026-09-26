package works.vme.streamer.data

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * On-disk index of matches. Layout under `context.filesDir`:
 *
 * ```
 * matches/
 *     <slug>/
 *         match.json    # VME-compatible; see Match.toJson()
 * ```
 *
 * The slug is the sortable identifier. It doubles as a folder name and
 * as the URL identifier in a future browsing UI — same convention as
 * VME's per-match folders.
 *
 * Saves are atomic via temp-file + rename. Half-written JSON survives
 * an app kill and would make the next launch impossible to trust; the
 * two extra lines of code for renameTo are cheap insurance.
 */
class MatchStore(context: Context) {

    private val root: File = File(context.filesDir, "matches").apply { mkdirs() }

    fun list(): List<Match> = (root.listFiles() ?: emptyArray())
        .asSequence()
        .filter { it.isDirectory }
        .mapNotNull { load(it.name) }
        // Newest first: date descending, then match_index descending so
        // the last match of a busy day is at the top.
        .sortedWith(compareByDescending<Match> { it.date }
            .thenByDescending { it.matchIndex ?: 0 })
        .toList()

    fun load(slug: String): Match? {
        val file = File(root, "$slug/match.json")
        if (!file.exists()) return null
        return runCatching { matchFromJson(slug, JSONObject(file.readText())) }
            .getOrNull()
    }

    /** Writes `match.json` atomically. Called after every event so a
     *  killed process loses at most the tap in flight. */
    fun save(match: Match) {
        val dir = File(root, match.slug).apply { mkdirs() }
        val out = File(dir, "match.json")
        val tmp = File(dir, "match.json.tmp")
        tmp.writeText(match.toJson().toString(2))
        if (!tmp.renameTo(out)) {
            out.writeText(tmp.readText())
            tmp.delete()
        }
    }

    fun delete(slug: String) {
        File(root, slug).deleteRecursively()
    }

    /** Return the on-disk path of a match's JSON, for a Share intent. */
    fun jsonFile(slug: String): File = File(root, "$slug/match.json")

    /**
     * Build a fresh Match for the given team + opponent + date.
     *
     * Slug is `<yyyyMMdd>_<NN>_<Opponent>` — chronological when sorted
     * as a string, so `ls` in a file manager shows a day in order.
     * `match_index` counts up per-day so two matches on the same day
     * against the same opponent do not collide.
     */
    fun createMatch(
        homeTeam: Team,
        opponentName: String,
        date: String = today(),
        tournament: String = "Streamed",
        /** Set when this fixture came from VME, so events can be sent
         *  back to the match they came from rather than to a path
         *  rebuilt from these fields - which would not agree (see
         *  [Match.vmeOrigin]). */
        vmeOrigin: VmeOrigin? = null,
    ): Match {
        val sameDayCount = list().count { it.date == date && it.team == homeTeam.slug }
        val matchIndex = sameDayCount + 1
        val safeOpponent = slugify(opponentName.ifBlank { "Opponent" })
        val dateSlug = date.replace("-", "")
        val slug = "${dateSlug}_%02d_%s".format(matchIndex, safeOpponent)
        val displayName = "%02d_%s".format(matchIndex, safeOpponent)

        return Match(
            slug = slug,
            team = homeTeam.slug,
            tournament = tournament,
            date = date,
            name = displayName,
            matchIndex = matchIndex,
            opponent = opponentName.trim().ifBlank { "Opponent" },
            fps = 30,
            homeRoster = homeTeam.roster,
            opponentRoster = Roster(
                teamName = opponentName.trim().ifBlank { "Opponent" },
                players = emptyList(),
            ),
            vmeOrigin = vmeOrigin,
        )
    }

    private fun today(): String =
        SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
}
