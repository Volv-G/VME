package works.vme.streamer.data

import org.json.JSONArray
import org.json.JSONObject

/**
 * Match, event and game-state models.
 *
 * These deliberately mirror the VME backend's `MatchOut` / `EventOut` /
 * `GameStateOut` (see `backend/app/api/schemas.py`) so a match.json
 * written by this app can be dropped into the editor's file layout and
 * read as if the backend had produced it. The event `type` strings are
 * the exact values from `frontend/src/types/api.ts::EVENT_TYPES`;
 * changing them here breaks the import path silently, which is the one
 * failure mode this whole schema exists to prevent.
 *
 * There is no domain logic in this file. State transitions live in
 * `logic/GameStateEngine.kt` and mirror `backend/app/domain/events/`.
 */

/** All event types that this app can produce. Order matters only in
 *  that it drives the button grid layout in `MatchLiveActivity`. */
enum class EventType(val wire: String) {
    // Lifecycle / phase changes.
    GameStart("game_start"),
    GameEnd("game_end"),
    SetEnd("set_end"),
    /** A timeout, as the two moments that bound it: called, and play
     *  resumed. VME cuts the span between them, so both have to be in
     *  the log -- the card coming down is not enough on its own.
     *
     *  This replaces a single `timeout` logged at the start only,
     *  which VME never registered: every one of them was dropped as an
     *  unknown type on import. [fromWire] still reads that old name,
     *  as a start, so match files already on the phone keep loading. */
    TimeoutStart("timeout_start"),
    TimeoutEnd("timeout_end"),

    // Serve flow.
    FirstServe("first_serve"),
    BallServed("ball_served"),
    Replay("replay"),

    // Scoring.
    Score("score"),
    ScoreCorrection("score_correction"),

    // Player actions.
    Kill("kill"),
    Ace("ace"),
    Assist("assist"),
    Block("block"),
    Dive("dive"),
    Dig("dig"),
    Highlight("highlight"),

    // Roster.
    Substitution("substitution"),

    // Timeline markers (rarely used courtside but kept for schema parity).
    CutStart("cut_start"),
    CutEnd("cut_end"),
    ClipTransition("clip_transition"),

    // Focus spans + messages.
    FocusIn("focus_in"),
    FocusOut("focus_out"),
    Message("message");

    companion object {
        fun fromWire(s: String): EventType? =
            if (s == LEGACY_TIMEOUT) TimeoutStart
            else values().firstOrNull { it.wire == s }

        /** What a timeout start was called before it had an end. */
        private const val LEGACY_TIMEOUT = "timeout"
    }
}

/** "home" and "away" strings match the wire format everywhere in VME. */
enum class Side(val wire: String) {
    Home("home"), Away("away");

    fun other() = if (this == Home) Away else Home

    companion object {
        fun fromWire(s: String?): Side? = when (s) {
            "home" -> Home; "away" -> Away; else -> null
        }
    }
}

/**
 * One recorded event. `payload` carries the per-type fields VME expects
 * (e.g. `team` for a Score, `player_number` for a Kill, `home_delta` /
 * `away_delta` for a ScoreCorrection). Structured as a free-form map
 * because every subtype has a different shape and enumerating them as
 * sealed classes would be more code than the two producers (this file
 * and the UI) benefit from.
 *
 * `at` is a phone-side wall-clock (unix millis). VME uses `clip_id` +
 * `local_frame`, both of which are zero here: the phone is streaming,
 * not recording clips, and the importer resolves wall-clock -> frames
 * against `Clip.start_recording_time` on the receiving side.
 */
data class MatchEvent(
    val id: Long,
    val type: EventType,
    val payload: Map<String, Any?>,
    /** Unix millis when the tap was made. */
    val at: Long,
)

/**
 * Computed snapshot after applying an event. Enough to drive the
 * scoreboard overlay and to bring the operator up to speed after a
 * mid-match restart. Positions/serving are tracked for the *home*
 * team; opponent rotations are not — VME does not track them either,
 * because nobody tags substitutions on a team they are watching.
 */
data class GameState(
    val homeScore: Int = 0,
    val awayScore: Int = 0,
    val homeSets: Int = 0,
    val awaySets: Int = 0,
    val servingTeam: Side? = null,
    /** Position (1..6) -> jersey number, or null when empty. Home only. */
    val homePositions: Map<Int, Int?> = (1..6).associateWith { null },
    val gameStarted: Boolean = false,
    val gameEnded: Boolean = false,
    val ballServedSinceLastScore: Boolean = false,
    /** Per-point winner in the current set: `true` for home,
     *  `false` for away. Reset on set end / game start. Consumed by
     *  the scoreboard's point-history strip; matches VME's
     *  `state.point_history`. */
    val pointHistory: List<Boolean> = emptyList(),
    /** Final score of each completed set, in order, as
     *  `home to away`. Appended on every `set_end`; cleared on
     *  `game_start`.
     *
     *  Phone-only: VME derives set results by replaying the event
     *  log, so this is not written to `match.json`. It exists so the
     *  end-of-game card can show "25-21  23-25  25-19" without the
     *  overlay having to re-walk the events itself. */
    val setScores: List<Pair<Int, Int>> = emptyList(),
    /**
     * Libero jersey -> jersey of the player they came on for.
     *
     * A libero substitution is not a normal one: the libero comes
     * on for a specific back-row player and must go off for that
     * *same* player when the rotation carries them to the front.
     * Remembering the pairing is what lets the swap-back happen
     * without asking the operator, who is watching the court.
     *
     * Populated from substitution events, so it replays correctly
     * from the log and survives Undo. An entry is dropped when the
     * libero leaves the court.
     */
    val liberoReplacements: Map<Int, Int> = emptyMap(),
    /**
     * The same pairing, but never cleared: who each libero last came
     * on for at any point in the match.
     *
     * It answers the case [liberoReplacements] cannot. A line-up
     * entered at set start puts the libero into an EMPTY slot, so they
     * displaced nobody and there is nothing to remember - and the first
     * rotation of every set would stop to ask a question the previous
     * set already answered. A libero covers the same player set after
     * set, so the last pairing is the right guess. It is only a guess,
     * so the caller uses it just when that player is off court, and
     * Undo is one tap.
     */
    val liberoLastPartners: Map<Int, Int> = emptyMap(),
)

/**
 * A whole match record, kept in memory and written back to
 * `match.json` after each event.
 *
 * The container is VME-shaped on the wire (see `toJson` /
 * `matchFromJson`) but not everything on the backend's `MatchOut` is
 * kept here — clip lists, tournament info, reel padding and so on are
 * either recomputed by the importer or irrelevant to the phone.
 */
data class Match(
    val slug: String,
    val team: String,          // home team slug (folder name in VME)
    val tournament: String,    // usually "Streamed" for phone-created matches
    val date: String,          // yyyy-MM-dd
    val name: String,          // display leaf, e.g. "01_Bellevue"
    val matchIndex: Int?,      // 1-based; null for legacy/only-of-day
    val opponent: String,
    val fps: Int = 30,
    val homeRoster: Roster,
    val opponentRoster: Roster,
    val events: List<MatchEvent> = emptyList(),
    /**
     * Home jersey numbers designated libero for this match.
     *
     * A match property, not a roster one -- mirrors VME's
     * `Match.liberos` and is written as `liberos` so the importer
     * picks it up. Who wears the libero jersey changes between
     * tournaments, and the home roster in this record is only a
     * snapshot VME ignores on import (it loads the real one from the
     * team folder), so a per-player flag would have been lost.
     */
    val liberos: Set<Int> = emptySet(),
    /** YouTube watch URL, set when a broadcast has been created and bound. */
    val youtubeVideoUrl: String? = null,
    /**
     * The broadcast's own id, kept so a second Start can stream back
     * into the same video instead of creating a new one.
     *
     * Derivable from [youtubeVideoUrl] -- it is the `v=` parameter --
     * but stored rather than parsed: the URL is a display string that
     * has already changed shape once, and an id that silently stops
     * matching would resume the wrong video.
     */
    val youtubeBroadcastId: String? = null,
    /** Unix millis when the app pressed "start streaming". Used as the
     *  clock anchor for event timestamps on export. */
    val streamStartedAt: Long? = null,
)

/** Jersey label as it appears on buttons and in pickers for a *home*
 *  player: `#7`, or `#7 (L)` for one of this match's liberos. */
fun Match.jerseyLabel(number: Int): String =
    if (number in liberos) "#$number (L)" else "#$number"

// ---- JSON marshalling ---------------------------------------------------
//
// Keys match VME's schema exactly. Phone-only fields are underscored.

fun matchFromJson(slug: String, obj: JSONObject): Match {
    val eventsArr = obj.optJSONArray("events") ?: JSONArray()
    val events = (0 until eventsArr.length()).mapNotNull { i ->
        val e = eventsArr.getJSONObject(i)
        val type = EventType.fromWire(e.optString("type")) ?: return@mapNotNull null
        val payload = e.optJSONObject("payload")?.toMap() ?: emptyMap()
        MatchEvent(
            id = e.optLong("id"),
            type = type,
            payload = payload,
            // `_at` is our extension; older exports may not have it.
            at = e.optLong("_at", 0L),
        )
    }
    return Match(
        slug = slug,
        team = obj.optString("team"),
        tournament = obj.optString("tournament", "Streamed"),
        date = obj.optString("date"),
        name = obj.optString("name", slug),
        matchIndex = if (obj.has("match_index") && !obj.isNull("match_index"))
            obj.optInt("match_index") else null,
        opponent = obj.optString("opponent"),
        fps = obj.optInt("fps", 30),
        homeRoster = obj.optJSONObject("home_roster")?.toRoster()
            ?: Roster(teamName = obj.optString("team")),
        opponentRoster = obj.optJSONObject("opponent_roster")?.toRoster()
            ?: Roster(teamName = obj.optString("opponent")),
        events = events,
        liberos = obj.optJSONArray("liberos")?.let { a ->
            (0 until a.length()).map { a.getInt(it) }.toSet()
        } ?: emptySet(),
        youtubeVideoUrl = obj.optString("_youtube_video_url").ifBlank { null },
        youtubeBroadcastId = obj.optString("_youtube_broadcast_id").ifBlank { null },
        streamStartedAt = if (obj.has("_stream_started_at"))
            obj.optLong("_stream_started_at") else null,
    )
}

fun Match.toJson(): JSONObject {
    val obj = JSONObject()
    obj.put("team", team)
    obj.put("tournament", tournament)
    obj.put("date", date)
    obj.put("name", name)
    if (matchIndex != null) obj.put("match_index", matchIndex)
    obj.put("opponent", opponent)
    obj.put("fps", fps)
    obj.put("clips", JSONArray())  // always empty from the phone
    obj.put("home_roster", homeRoster.toJson())
    obj.put("opponent_roster", opponentRoster.toJson())
    val arr = JSONArray()
    events.forEach { e ->
        val o = JSONObject()
            .put("type", e.type.wire)
            .put("id", e.id)
            .put("clip_id", "")
            .put("local_frame", 0)
            .put("payload", JSONObject(e.payload.mapValues { it.value?.toJsonSafe() }))
            .put("_at", e.at)
        arr.put(o)
    }
    obj.put("events", arr)
    obj.put("liberos", JSONArray(liberos.sorted()))
    if (youtubeVideoUrl != null) obj.put("_youtube_video_url", youtubeVideoUrl)
    if (youtubeBroadcastId != null) obj.put("_youtube_broadcast_id", youtubeBroadcastId)
    if (streamStartedAt != null) obj.put("_stream_started_at", streamStartedAt)
    return obj
}

// JSONObject.toMap() is not on org.json's Android build; roll it.
private fun JSONObject.toMap(): Map<String, Any?> {
    val out = LinkedHashMap<String, Any?>(length())
    val it = keys()
    while (it.hasNext()) {
        val k = it.next()
        val v = get(k)
        out[k] = if (v === JSONObject.NULL) null else v
    }
    return out
}

private fun Any.toJsonSafe(): Any = when (this) {
    is Side -> wire
    is EventType -> wire
    else -> this
}
