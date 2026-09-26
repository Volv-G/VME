package works.vme.streamer.logic

import works.vme.streamer.data.EventType
import works.vme.streamer.data.GameState
import works.vme.streamer.data.MatchEvent
import works.vme.streamer.data.Side

/**
 * State-machine port of `backend/app/domain/events/` and
 * `backend/app/domain/game_state.py`, scoped to the subset a courtside
 * operator can produce.
 *
 * Rules replicated (see backend `base.py::MatchEvent.apply`):
 *
 * - **Score / Kill / Ace** — the "scoring" events.
 *     - Rotate the winning team's positions (home only; opponent
 *       rotations aren't tracked, same as VME) if they were not
 *       already serving.
 *     - +1 to that team's score.
 *     - Serve transfers to them.
 *     - `ball_served_since_last_score` resets to false.
 *     - **Unless the ball was never served**, in which case the point
 *       is a correction: the score moves, the serve and the rotation
 *       do not. See [onScore].
 * - **ScoreCorrection** — apply `home_delta` / `away_delta` (never
 *   below zero). Does not touch serve or positions.
 * - **SetEnd** — whichever side leads takes the set; scores reset;
 *   positions cleared; serve cleared.
 * - **GameStart / GameEnd** — flip the flags.
 * - **FirstServe** — sets `serving_team`.
 * - **BallServed** — sets `ball_served_since_last_score = true`.
 * - **Replay** — resets the same flag (so a following Score is legal).
 * - **Substitution** — write jersey into the home lineup at the given
 *   position. Opponent subs are recorded but do not affect state.
 *   When the jersey going on or off is a libero, the
 *   `liberoReplacements` pairing is updated (see [onSubstitution]).
 *
 * Liberos are roster knowledge, not event knowledge, so [compute]
 * takes the set of libero jerseys alongside the event list. The
 * engine only *tracks* the pairing; sending the libero off when the
 * rotation carries them to the front row is the caller's job (see
 * [frontRowLibero]), because that swap is a new event the operator
 * may need to be asked about.
 * - Everything else (Highlight, Assist, Block, Dig, Dive, Focus*,
 *   Cut*, ClipTransition, Message) is state-neutral: it lives in the
 *   event log so a scoreboard-only operator can still surface it
 *   later, but it doesn't move the scoreboard.
 *
 * There is no validation here. Validation is a post-import concern
 * (`backend/app/domain` reports warnings/errors), and blocking a tap
 * at the gym for a suspected out-of-order serve would be worse than
 * letting the operator fix it later.
 */
object GameStateEngine {

    /** Fold all events into a running `GameState`. Cheap enough
     *  (integer arithmetic, <100 events per match) to recompute on
     *  every mutation. */
    fun compute(events: List<MatchEvent>, liberos: Set<Int> = emptySet()): GameState {
        // Does this match mark serves at all? A log with no Ball Served
        // in it cannot distinguish a rally from a correction, so it
        // keeps the old rule (every point moves the serve). Without
        // this, a match tagged before Ball Served was part of the
        // routine would replay with the serve frozen on whoever opened
        // the set.
        val tracksServes = events.any { it.type == EventType.BallServed }
        var s = GameState()
        for (e in events) s = apply(s, e, liberos, tracksServes)
        return s
    }

    /** Apply one event, returning the next state. Kept public so a UI
     *  can preview the effect of a proposed event before committing. */
    fun apply(
        state: GameState,
        e: MatchEvent,
        liberos: Set<Int> = emptySet(),
        tracksServes: Boolean = true,
    ): GameState = when (e.type) {
        EventType.GameStart -> state.copy(
            gameStarted = true, gameEnded = false, setScores = emptyList(),
        )
        EventType.GameEnd -> state.copy(gameEnded = true)
        EventType.SetEnd -> onSetEnd(state)
        EventType.FirstServe -> state.copy(servingTeam = teamOf(e))
        EventType.BallServed -> state.copy(ballServedSinceLastScore = true)
        EventType.Replay -> state.copy(ballServedSinceLastScore = false)
        EventType.Score -> onScore(state, teamOf(e) ?: return state, tracksServes)
        EventType.Kill -> onScore(state, teamOf(e) ?: return state, tracksServes)
        EventType.Ace -> onScore(state, teamOf(e) ?: return state, tracksServes)
        EventType.ScoreCorrection -> onCorrection(state, e)
        EventType.Substitution -> onSubstitution(state, e, liberos)
        else -> state   // stat-only or timeline-only events
    }

    /** Front-row slots in rotation order: P4 is where a back-row
     *  player lands first after a side-out. */
    private val FRONT_ROW = listOf(4, 3, 2)

    /**
     * The first libero found in the front row, as `position to
     * jersey`, or null when the lineup is legal. A libero may only
     * play the back row, so a hit here means the rotation just
     * carried one forward and they have to come off.
     */
    fun frontRowLibero(state: GameState, liberos: Set<Int>): Pair<Int, Int>? {
        for (pos in FRONT_ROW) {
            val j = state.homePositions[pos] ?: continue
            if (j in liberos) return pos to j
        }
        return null
    }

    // ---- individual transitions --------------------------------------

    private fun onScore(
        state: GameState,
        winner: Side,
        tracksServes: Boolean = true,
    ): GameState {
        // A point with no serve behind it is a correction, not a rally.
        //
        // Nobody can win a rally that was never started, so this is the
        // operator fixing the score: a point that went unrecorded, or a
        // tap on the wrong team a moment ago. The score moves and the
        // history strip keeps its length, but the serve stays where the
        // actual rallies left it and nobody rotates -- inventing a
        // side-out here would put the wrong player in the serving slot
        // for the rest of the set, which is much harder to notice than
        // a wrong score and much harder to unpick.
        if (tracksServes && !state.ballServedSinceLastScore) {
            val (ch, ca) = if (winner == Side.Home)
                (state.homeScore + 1) to state.awayScore
            else state.homeScore to (state.awayScore + 1)
            return state.copy(
                homeScore = ch,
                awayScore = ca,
                pointHistory = state.pointHistory + (winner == Side.Home),
            )
        }
        val positions =
            if (state.servingTeam != null && winner != state.servingTeam && winner == Side.Home)
                rotate(state.homePositions) else state.homePositions
        val (h, a) = if (winner == Side.Home)
            (state.homeScore + 1) to state.awayScore
        else state.homeScore to (state.awayScore + 1)
        return state.copy(
            homeScore = h,
            awayScore = a,
            servingTeam = winner,
            ballServedSinceLastScore = false,
            homePositions = positions,
            pointHistory = state.pointHistory + (winner == Side.Home),
        )
    }

    private fun onCorrection(state: GameState, e: MatchEvent): GameState {
        val hd = (e.payload["home_delta"] as? Number)?.toInt() ?: 0
        val ad = (e.payload["away_delta"] as? Number)?.toInt() ?: 0
        return state.copy(
            homeScore = maxOf(0, state.homeScore + hd),
            awayScore = maxOf(0, state.awayScore + ad),
        )
    }

    private fun onSetEnd(state: GameState): GameState {
        val (hSets, aSets) = when {
            state.homeScore > state.awayScore -> (state.homeSets + 1) to state.awaySets
            state.awayScore > state.homeScore -> state.homeSets to (state.awaySets + 1)
            else -> state.homeSets to state.awaySets
        }
        return state.copy(
            homeScore = 0,
            awayScore = 0,
            homeSets = hSets,
            awaySets = aSets,
            servingTeam = null,
            ballServedSinceLastScore = false,
            homePositions = (1..6).associateWith { null },
            liberoReplacements = emptyMap(),
            // `liberoLastPartners` is deliberately NOT cleared here:
            // it exists to outlive the set, so the next line-up entry
            // does not have to ask who the libero covers.
            pointHistory = emptyList(),
            // Keep the set's final score before wiping the running
            // one, so the end-of-game card can list every set.
            setScores = state.setScores + (state.homeScore to state.awayScore),
        )
    }

    private fun onSubstitution(state: GameState, e: MatchEvent, liberos: Set<Int>): GameState {
        if (teamOf(e) != Side.Home) return state
        val pos = (e.payload["position"] as? Number)?.toInt() ?: return state
        if (pos !in 1..6) return state
        val jersey = (e.payload["player_in_number"] as? Number)?.toInt() ?: return state
        val outgoing = state.homePositions[pos]
        val map = state.homePositions.toMutableMap()
        map[pos] = jersey

        // Libero bookkeeping. A libero coming on remembers who they
        // came on for, so the rotation rule can send that player
        // back without asking. A libero going off -- for anyone, by
        // any route -- forgets it. Placing a libero into an empty
        // slot (lineup entry at set start) records nothing, which is
        // what makes the caller fall back to a picker later.
        val pairs = state.liberoReplacements.toMutableMap()
        val lastPartners = state.liberoLastPartners.toMutableMap()
        if (outgoing != null && outgoing in liberos) pairs.remove(outgoing)
        if (jersey in liberos) {
            if (outgoing != null && outgoing !in liberos) {
                pairs[jersey] = outgoing
                lastPartners[jersey] = outgoing
            } else {
                pairs.remove(jersey)
            }
        }
        return state.copy(
            homePositions = map,
            liberoReplacements = pairs,
            liberoLastPartners = lastPartners,
        )
    }

    /** Clockwise rotation used after a side-out: 2->1, 3->2, ..., 1->6. */
    private fun rotate(positions: Map<Int, Int?>): Map<Int, Int?> {
        val next = HashMap<Int, Int?>(6)
        val pos1 = positions[1]
        for (i in 1..5) next[i] = positions[i + 1]
        next[6] = pos1
        return next
    }

    private fun teamOf(e: MatchEvent): Side? =
        Side.fromWire(e.payload["team"] as? String)
}
