package works.vme.streamer.data

/**
 * On-phone data shapes. Deliberately a strict subset of the VME
 * backend's schema (see `backend/app/api/schemas.py` and
 * `frontend/src/types/api.ts`) so a match record produced here can be
 * imported by the existing editor without translation.
 *
 * Two rules keep this file honest:
 *
 * 1.  Names are the ones VME uses. `players`, not `roster_players`;
 *     `opponent`, not `opponent_name`; `home_score`, not `homeScore`.
 *     When these drift the importer has to guess, and the whole point
 *     of a shared schema is that it does not have to.
 *
 * 2.  Fields VME does not need at capture time are not modelled at all.
 *     Naming templates, YouTube config, media-server paths — those live
 *     in the editor and this app has no reason to store, edit or ship
 *     them. Anything imported and not used is dropped on write, not
 *     preserved.
 *
 * All JSON is hand-rolled through `org.json` (see `Storage.kt` and
 * `VmeClient.kt`) rather than any reflection-based library, because the
 * shapes are small and adding kotlinx-serialization or Moshi buys
 * nothing here except an extra dependency and a plugin.
 */

/** A single player on a roster. */
data class Player(
    val name: String,
    val number: Int,
    /** Optional short/nickname, useful in overlays where a full name
     *  will not fit. Null when the roster did not supply one. */
    val shortName: String? = null,
    /** VME's `profile_pic_path`: the server-side path to this player's
     *  photo. Preserved verbatim on round-trip so the editor keeps
     *  working, but **not** what the phone loads from -- the phone
     *  reads [localPhotoPath]. Null when the player has no photo. */
    val profilePicPath: String? = null,
    /** Absolute path to this player's photo cached in app storage by
     *  `PhotoCache`, or null when we have not fetched one.
     *
     *  Phone-only, hence the `_` prefix when serialised -- VME's
     *  importer ignores unknown underscore-prefixed keys, and a path
     *  under `/data/data/works.vme.streamer/` is meaningless on the
     *  server anyway. */
    val localPhotoPath: String? = null,
)

/** A roster, either the local team's or an opponent's. */
data class Roster(
    /** Human-readable team name. Distinct from the storage key: a team
     *  named "Vipers 14U" is stored under the slug "Vipers_14U", but
     *  the overlay uses this string. */
    val teamName: String,
    /** Team accent colour as a `#rrggbb` hex string. `null` = fall back
     *  to the overlay default. */
    val teamColor: String? = null,
    /** VME's `team_logo_path`: the logo filename, relative to the
     *  roster's own directory on the server. Round-tripped verbatim
     *  so a roster that passes through this app keeps the editor's
     *  reference; **not** what the phone draws from -- that is
     *  [localLogoPath]. Null when the team has no logo. */
    val teamLogoPath: String? = null,
    /** Absolute path to the logo cached in app storage, or null when
     *  we have not got one.
     *
     *  Phone-only, hence the `_` prefix when serialised: a path under
     *  `/data/data/works.vme.streamer/` means nothing on the server,
     *  and VME's importer ignores unknown underscore-prefixed keys.
     *
     *  For the home team this is filled at roster import, from VME.
     *  For the opponent it is whatever the operator picked out of the
     *  phone's gallery -- there is no opponent roster on the server to
     *  import one from, and the badge is usually on a draw sheet or a
     *  club website minutes before the first whistle. */
    val localLogoPath: String? = null,
    val players: List<Player> = emptyList(),
)

/**
 * How this team's broadcasts are named, filed and announced.
 *
 * Mirrors VME's per-team `youtube` block in `roster.json` -- same
 * template placeholders, same playlist -- so a stream and the upload of
 * the same match end up named the same way. Seeded from VME at roster
 * import and editable on the phone afterwards, because the name is
 * wanted at the gym and the server is not always reachable from one.
 *
 * `announceTo` has no VME counterpart: it is the phone that knows a
 * broadcast has started, and it is the only moment the announcement is
 * worth sending.
 */
data class StreamConfig(
    /** Title template. Placeholders: `{team}`, `{opponent}`, `{date}`,
     *  `{tournament}`. Blank falls back to [DEFAULT_TITLE]. */
    val titleTemplate: String = DEFAULT_TITLE,
    /** Description template, same placeholders. */
    val descriptionTemplate: String = DEFAULT_DESCRIPTION,
    /** YouTube playlist to add each broadcast to. Blank = none. */
    val playlistId: String = "",
    /**
     * Who to tell when a broadcast starts.
     *
     * One address per line. A Google Group is the right thing to put
     * here: membership is then managed in Google rather than on a phone
     * in a kit bag, and joining or leaving does not need an app update.
     */
    val announceTo: String = "",
    /** Subject line of that email. Also takes `{title}` and `{url}`. */
    val emailSubjectTemplate: String = DEFAULT_EMAIL_SUBJECT,
    /** Body of that email. Same placeholders as the subject. */
    val emailBodyTemplate: String = DEFAULT_EMAIL_BODY,
) {
    /**
     * Fill the placeholders. Unknown ones are left alone rather than
     * erased -- a typo should look like a typo, not vanish.
     */
    fun render(template: String, vars: Map<String, String>): String =
        vars.entries.fold(template) { acc, (k, v) -> acc.replace("{$k}", v) }

    companion object Vars {
        const val DEFAULT_TITLE = "{team} vs {opponent} - {date}"
        const val DEFAULT_DESCRIPTION =
            "{tournament}. {team} vs {opponent}, {date}.\n\nStreamed from VME."

        /**
         * The announcement says who, what and where without being
         * opened: most of these land on a phone lock screen, where
         * the subject is often the whole message anyone reads.
         *
         * The body repeats the fixture rather than leaning on
         * `{title}`, because a team that rewrites its title template
         * to something terse should not silently lose the detail
         * here as well.
         */
        const val DEFAULT_EMAIL_SUBJECT = "LIVE: {team} vs {opponent} - {tournament}"
        const val DEFAULT_EMAIL_BODY =
            "{team} vs {opponent} is streaming live now.\n\n" +
            "Tournament: {tournament}\n" +
            "Date: {date}\n\n" +
            "Watch: {url}\n"

        /**
         * Placeholder values for one fixture.
         *
         * `title` and `url` are blank for the stream title and
         * description, which are rendered *before* the broadcast
         * exists -- there is no watch URL yet, and the title is the
         * thing being produced. They carry values only for the
         * announcement email, which is sent afterwards.
         */
        fun vars(
            team: String,
            opponent: String,
            date: String,
            tournament: String,
            title: String = "",
            url: String = "",
        ): Map<String, String> = mapOf(
            "team" to team,
            "opponent" to opponent,
            "date" to date,
            "tournament" to tournament,
            "title" to title,
            "url" to url,
        )
    }

    /** Addresses, one per line or comma-separated, blanks dropped. */
    val recipients: List<String>
        get() = announceTo.split('\n', ',')
            .map { it.trim() }
            .filter { it.isNotBlank() }


}

/** A locally-stored team. The `slug` is the folder/URL identifier
 *  (matches VME's team folder name), the `roster` is the imported data
 *  as of the last import. */
data class Team(
    val slug: String,
    val roster: Roster,
    /** Naming, playlist and announcement settings for this team's
     *  broadcasts. Defaults are usable without ever being edited. */
    val stream: StreamConfig = StreamConfig(),
    /** Where this roster was imported from, for a follow-up refresh.
     *  Empty string when the team was made in-app rather than imported. */
    val sourceUrl: String = "",
    /** Unix seconds; 0 = never. */
    val importedAt: Long = 0L,
)
