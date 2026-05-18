/** Focus-event analysis: flag FocusIn events that have no matching
 *  PlayerEvent in their rally.
 *
 *  The render-side rule (see `backend/app/render/batch.py`): a focused
 *  clip's action label comes from the first PlayerEvent by the same
 *  player in the same rally. When no such event exists, the backend
 *  skips that clip entirely - and the frontend flags the FocusIn row
 *  in red so the user can either tag the action or delete the focus.
 *
 *  Rally bounds match the backend heuristic exactly:
 *    - rally start = nearest preceding `ball_served`
 *    - rally end   = next `kill` / `ace` (rally-ending score) OR next
 *                    `ball_served` (next rally's serve)
 *
 *  This module is intentionally a pure function over `EventDto[]` so
 *  it can be reused by Timeline + EventList without sharing state.
 */

import type { EventDto } from "../types/api";

/** Event types that count as a "player event" for focus matching.
 *  Mirrors the backend `PlayerEvent` class hierarchy (see
 *  `app/domain/events/player.py`). Kept here as a literal set rather
 *  than imported because the EVENT_TYPES const in `types/api.ts` mixes
 *  player and non-player types and doesn't expose the subset cleanly.
 */
const PLAYER_EVENT_TYPES = new Set([
  "kill",
  "ace",
  "assist",
  "block",
  "dive",
  "dig",
  "highlight",
]);

const RALLY_END_SCORING_TYPES = new Set(["kill", "ace"]);

export interface FocusAnalysis {
  /** FocusIn event ids whose surrounding rally has no PlayerEvent
   *  for the same (team, player_number). */
  orphanIds: Set<number>;
}

export function analyzeFocus(events: EventDto[]): FocusAnalysis {
  // Sort by global frame once; rally lookups walk this sorted list.
  // Events without a resolvable global frame are skipped - they can't
  // be placed on the timeline at all, so they can't have a rally.
  const sorted = [...events]
    .filter((e) => e.global_frame !== null && e.global_frame !== undefined)
    .sort((a, b) => (a.global_frame as number) - (b.global_frame as number));

  const orphans = new Set<number>();

  for (const focusIn of sorted) {
    if (focusIn.type !== "focus_in") continue;
    const team = focusIn.payload?.["team"] as string | undefined;
    const playerNumber = focusIn.payload?.["player_number"] as
      | number
      | undefined;
    // No player or team metadata = nothing to validate against. Don't
    // flag - it would be a different kind of problem (malformed event)
    // and is rare enough not to worth blocking renders over.
    if (team == null || playerNumber == null) continue;

    const focusFrame = focusIn.global_frame as number;
    // Resolve rally bounds. `rallyStart` keeps updating while we see
    // earlier ball_served events; once we pass `focusFrame` we look
    // for the rally end. The two loops merge naturally since `sorted`
    // is already in frame order.
    let rallyStart = -Infinity;
    let rallyEnd = Infinity;
    for (const cand of sorted) {
      const g = cand.global_frame as number;
      if (g <= focusFrame) {
        if (cand.type === "ball_served") rallyStart = g;
        continue;
      }
      // g > focusFrame
      if (RALLY_END_SCORING_TYPES.has(cand.type)) {
        rallyEnd = g;
        break;
      }
      if (cand.type === "ball_served") {
        rallyEnd = g;
        break;
      }
    }

    // Scan for a matching PlayerEvent inside [rallyStart, rallyEnd].
    let found = false;
    for (const cand of sorted) {
      const g = cand.global_frame as number;
      if (g < rallyStart) continue;
      if (g > rallyEnd) break;
      if (!PLAYER_EVENT_TYPES.has(cand.type)) continue;
      const candTeam = cand.payload?.["team"];
      const candNumber = cand.payload?.["player_number"];
      if (candTeam === team && candNumber === playerNumber) {
        found = true;
        break;
      }
    }
    if (!found) orphans.add(focusIn.id);
  }

  return { orphanIds: orphans };
}
