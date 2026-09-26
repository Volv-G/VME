import type { EventDto, GameStateDto } from "../types/api";

/**
 * An ace is won by the player who served it, so the ace and the serve
 * must name the same person.
 *
 * The phone credits the server automatically - position 1 of the
 * serving team - but only when it knows the rotation. Tag an ace by
 * hand, or import a log whose line-up was entered late, and the credit
 * can land on somebody who was not even serving. Nothing downstream
 * notices: the point is counted, the reel is cut, and a player ends the
 * season with aces they never hit.
 *
 * Reported as an error rather than a warning. A serve gap is a judgement
 * call; this is two records of one moment contradicting each other.
 */

export interface AceMismatch {
  /** Who the ace is credited to. */
  credited: number;
  /** Who was at position 1 of the serving team when it was served. */
  server: number;
}

/**
 * Aces credited to somebody other than the server, keyed by event id.
 *
 * Deliberately silent where the rotation is not known well enough to
 * disagree: before a first serve, with an empty line-up, or for an
 * opponent ace (VME tracks our rotation, not theirs). A check that
 * fires on missing data would flag most of a hand-built match.
 */
export function analyzeAceServers(events: EventDto[]): Map<number, AceMismatch> {
  const out = new Map<number, AceMismatch>();
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (ev.type !== "ace") continue;

    const credited = Number(ev.payload.player_number ?? 0) || null;
    if (credited == null) continue;

    // The state BEFORE the ace: afterwards the point has been scored and
    // the rotation may already have moved on.
    const before: GameStateDto | null =
      i === 0 ? null : (events[i - 1].state ?? null);
    const serving = before?.serving_team ?? null;
    if (!before || !serving) continue;

    // An ace is scored by the serving side. A payload that says
    // otherwise is the same contradiction seen from the other end, and
    // the server named below is the right one either way.
    const positions =
      serving === "home" ? before.home_positions : before.away_positions;
    const server = positions?.[1] ?? null;
    if (server == null) continue;

    if (server !== credited) out.set(ev.id, { credited, server });
  }
  return out;
}
