import type { EventDto } from "../types/api";

/**
 * A libero leaving and coming back on are one swap, at one moment.
 *
 * When the rotation carries the libero to the front row they must come
 * off, and the player they replaced returns: `Libero → #12`. In the
 * same stoppage the libero goes back on for whoever has just rotated to
 * the back row: `#7 → Libero`. Two events, one dead ball.
 *
 * The phone records the first automatically the instant the point is
 * logged, and the operator taps the second a few seconds later - so the
 * pair arrives at VME several seconds apart, and the overlay shows two
 * substitutions at two different moments of a match where nothing
 * happened in between. The fix is to put the second where the first is.
 *
 * Only ever within one gap between rallies: a serve means play resumed,
 * and a libero going off in one rally has nothing to do with one coming
 * on in the next.
 */

/**
 * Below this the pair is already effectively simultaneous, and a badge
 * on every libero swap in the match would be noise. Half a second is
 * thirty frames at 60fps - visible in a render, which is the point.
 */
export const LIBERO_SWAP_WARN_SECONDS = 0.5;

/**
 * Play resuming, or the set's structure changing. Either way a libero
 * exit before one of these does not pair with an entry after it.
 *
 * Scoring events are deliberately NOT here: a point is what *opens* the
 * gap, and the automatic libero substitution is logged immediately
 * after it. Timeouts are not here either - the teams are standing
 * around, which is exactly when substitutions happen.
 */
const RALLY_BOUNDARY = new Set([
  "ball_served",
  "first_serve",
  "replay",
  "set_end",
  "game_start",
  "game_end",
]);

export interface LiberoSwap {
  /** The `#7 → Libero` event, which is the one out of place. */
  id: number;
  /** The `Libero → #12` event it belongs with. */
  anchorId: number;
  /** How far behind the anchor it sits, in seconds. */
  seconds: number;
  /** The libero's jersey, for the explanation. */
  jersey: number;
}

/** Jersey leaving the court in a substitution, from the state before it. */
function outgoing(ev: EventDto, before: EventDto | undefined): number | null {
  const pos = Number(ev.payload.position ?? 0);
  if (!pos) return null;
  const team = String(ev.payload.team || "home");
  const state = before?.state;
  if (!state) return null;
  const positions = team === "away" ? state.away_positions : state.home_positions;
  const at = positions?.[pos];
  return typeof at === "number" ? at : null;
}

/**
 * Libero entries that are sitting later than the exit they belong with,
 * keyed by event id.
 *
 * `liberos` is the match's libero jerseys; without any there is nothing
 * to pair and the map comes back empty.
 */
export function analyzeLiberoSwaps(
  events: EventDto[],
  liberos: readonly number[],
  fps: number
): Map<number, LiberoSwap> {
  const out = new Map<number, LiberoSwap>();
  if (!liberos.length || fps <= 0) return out;
  const isLibero = (n: number | null) => n != null && liberos.includes(n);

  // The most recent exit for each libero, within the current gap. Keyed
  // by jersey so a team with two liberos cannot cross them over.
  let pendingExit = new Map<number, EventDto>();

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (RALLY_BOUNDARY.has(ev.type)) {
      pendingExit = new Map();
      continue;
    }
    if (ev.type !== "substitution") continue;

    const went = outgoing(ev, events[i - 1]);
    const came = Number(ev.payload.player_in_number ?? 0) || null;

    if (isLibero(went)) {
      pendingExit.set(went as number, ev);
      continue;
    }
    if (!isLibero(came)) continue;

    const exit = pendingExit.get(came as number);
    if (!exit) continue;
    // Paired: this entry belongs with that exit. Consumed either way,
    // so a second entry for the same libero in one gap - which would be
    // a mis-tag - does not chain off the same exit.
    pendingExit.delete(came as number);
    if (ev.global_frame == null || exit.global_frame == null) continue;
    const seconds = (ev.global_frame - exit.global_frame) / fps;
    if (seconds < LIBERO_SWAP_WARN_SECONDS) continue;
    out.set(ev.id, {
      id: ev.id,
      anchorId: exit.id,
      seconds,
      jersey: came as number,
    });
  }
  return out;
}
