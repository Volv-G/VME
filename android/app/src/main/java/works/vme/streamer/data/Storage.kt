package works.vme.streamer.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * The phone's on-disk index of teams and their rosters.
 *
 * Layout under `context.filesDir`:
 *
 * ```
 * teams/
 *     <slug>/
 *         roster.json     # RosterOut-compatible, plus source/importedAt
 * ```
 *
 * One folder per team keeps the door open for per-team assets
 * (logos, cached photos) without another migration, and mirrors the
 * VME editor's on-disk layout closely enough that a copy-paste
 * onto/off the phone remains recognisable.
 *
 * There is no database and no coroutine layer. A roster is a few kB
 * and the whole store loads in a millisecond; adding Room here would
 * be more code than the feature it supports.
 *
 * All writes go through a temp file and `renameTo`. Half-written JSON
 * is worse than no JSON, because it survives the app restart that
 * would otherwise fix it.
 */
class TeamStore(context: Context) {

    private val root: File = File(context.filesDir, "teams").apply { mkdirs() }

    /** All teams currently on disk. Cheap enough to call from a UI thread
     *  when a list screen refreshes. */
    fun list(): List<Team> = (root.listFiles() ?: emptyArray())
        .asSequence()
        .filter { it.isDirectory }
        .mapNotNull { load(it.name) }
        .sortedBy { it.roster.teamName.lowercase() }
        .toList()

    fun load(slug: String): Team? {
        val file = File(root, "$slug/roster.json")
        if (!file.exists()) return null
        return runCatching {
            val obj = JSONObject(file.readText())
            Team(
                slug = slug,
                roster = obj.toRoster(),
                stream = obj.toStreamConfig(),
                sourceUrl = obj.optString("_source_url", ""),
                importedAt = obj.optLong("_imported_at", 0L),
            )
        }.getOrNull()
    }

    /** Save (or overwrite) a team, keyed by [Team.slug]. */
    fun save(team: Team) {
        val dir = File(root, team.slug).apply { mkdirs() }
        val out = File(dir, "roster.json")
        val tmp = File(dir, "roster.json.tmp")
        // Include the VME-compatible fields at the top level, and prefix
        // the phone-only bookkeeping with an underscore so anyone
        // hand-reading the file sees the shape they expect from VME.
        val json = team.roster.toJson().apply {
            put("youtube", team.stream.toJson())
            if (team.sourceUrl.isNotEmpty()) put("_source_url", team.sourceUrl)
            if (team.importedAt > 0) put("_imported_at", team.importedAt)
        }
        tmp.writeText(json.toString(2))
        // renameTo is atomic on the same filesystem, which app files are.
        if (!tmp.renameTo(out)) {
            // Fallback: some emulator overlays fail silently on rename.
            out.writeText(tmp.readText())
            tmp.delete()
        }
    }

    fun delete(slug: String) {
        File(root, slug).deleteRecursively()
    }
}

// ---- JSON marshalling --------------------------------------------------
//
// The keys here match VME's RosterOut / PlayerOut exactly. If VME's
// schema gains a field this app needs, add it in both directions here
// and in Models.kt; if it gains one this app does not need, ignore it
// on read and do not write it back.

fun JSONObject.toRoster(): Roster {
    val playersJson = optJSONArray("players") ?: JSONArray()
    val players = (0 until playersJson.length()).map { i ->
        val p = playersJson.getJSONObject(i)
        Player(
            name = p.optString("name", ""),
            number = p.optInt("number", 0),
            shortName = p.optString("short_name").ifBlank { null },
            profilePicPath = p.optString("profile_pic_path").ifBlank { null },
            localPhotoPath = p.optString("_local_photo_path").ifBlank { null },
        )
    }
    return Roster(
        teamName = optString("team_name").ifBlank { "" },
        teamColor = optString("team_color").ifBlank { null },
        teamLogoPath = optString("team_logo_path").ifBlank { null },
        localLogoPath = optString("_local_logo_path").ifBlank { null },
        players = players,
    )
}

/**
 * Read the stream settings out of VME's `youtube` block.
 *
 * The keys are VME's, so a roster fetched straight from the server
 * arrives already configured -- the phone does not have to be told the
 * naming a team already uses for its uploads. `_announce_to` is the one
 * addition, underscored because VME has no notion of it and its
 * importer ignores unknown underscore-prefixed keys.
 */
fun JSONObject.toStreamConfig(): StreamConfig {
    val yt = optJSONObject("youtube") ?: JSONObject()
    return StreamConfig(
        titleTemplate = yt.optString("title_template")
            .ifBlank { StreamConfig.DEFAULT_TITLE },
        descriptionTemplate = yt.optString("description_template")
            .ifBlank { StreamConfig.DEFAULT_DESCRIPTION },
        playlistId = yt.optString("playlist_id"),
        announceTo = yt.optString("_announce_to"),
        emailSubjectTemplate = yt.optString("_email_subject")
            .ifBlank { StreamConfig.DEFAULT_EMAIL_SUBJECT },
        emailBodyTemplate = yt.optString("_email_body")
            .ifBlank { StreamConfig.DEFAULT_EMAIL_BODY },
    )
}

fun StreamConfig.toJson(): JSONObject = JSONObject()
    .put("title_template", titleTemplate)
    .put("description_template", descriptionTemplate)
    .put("playlist_id", playlistId)
    .put("_announce_to", announceTo)
    .put("_email_subject", emailSubjectTemplate)
    .put("_email_body", emailBodyTemplate)

fun Roster.toJson(): JSONObject {
    val obj = JSONObject()
    obj.put("team_name", teamName)
    if (teamColor != null) obj.put("team_color", teamColor)
    // Written back verbatim so a roster that round-trips through this
    // app does not lose the server's logo reference.
    if (teamLogoPath != null) obj.put("team_logo_path", teamLogoPath)
    // Phone-only (see Models.kt).
    if (localLogoPath != null) obj.put("_local_logo_path", localLogoPath)
    val arr = JSONArray()
    players.forEach { p ->
        val po = JSONObject()
            .put("name", p.name)
            .put("number", p.number)
        if (p.shortName != null) po.put("short_name", p.shortName)
        // Written back verbatim so a roster that round-trips through
        // this app does not lose the server's photo reference.
        if (p.profilePicPath != null) po.put("profile_pic_path", p.profilePicPath)
        // Phone-only, underscore-prefixed (see Models.kt).
        if (p.localPhotoPath != null) po.put("_local_photo_path", p.localPhotoPath)
        arr.put(po)
    }
    obj.put("players", arr)
    return obj
}

/** Fold a display name into a filesystem-safe slug that also serves as
 *  the URL identifier — same convention as the VME backend's team
 *  folders. Kept in Kotlin so this app can create a slug offline for
 *  an in-app team, without a round trip. */
fun slugify(displayName: String): String {
    val trimmed = displayName.trim()
    if (trimmed.isEmpty()) return "team"
    val sb = StringBuilder(trimmed.length)
    var lastWasSep = false
    for (ch in trimmed) {
        val ok = ch.isLetterOrDigit()
        if (ok) {
            sb.append(ch)
            lastWasSep = false
        } else if (!lastWasSep) {
            sb.append('_')
            lastWasSep = true
        }
    }
    return sb.toString().trim('_').ifEmpty { "team" }
}
