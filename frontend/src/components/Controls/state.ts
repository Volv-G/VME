import type { EventDto, GameStateDto, PlayerDto, RosterDto } from "../../types/api";

/** Empty/initial GameState — matches the backend default. */
export const EMPTY_STATE: GameStateDto = {
  home_score: 0,
  away_score: 0,
  home_sets: 0,
  away_sets: 0,
  serving_team: null,
  home_positions: { 1: null, 2: null, 3: null, 4: null, 5: null, 6: null },
  away_positions: { 1: null, 2: null, 3: null, 4: null, 5: null, 6: null },
  game_started: false,
  game_ended: false,
  in_cut_region: false,
  ball_served_since_last_score: false,
};

/**
 * Returns the GameState snapshot from the most recent event whose
 * global_frame is <= currentFrame. Backend sorts events by global_frame,
 * so we can scan from the end. Falls back to EMPTY_STATE when nothing
 * applies yet (playhead before first event, no events, etc.).
 */
export function stateAtPlayhead(events: EventDto[], currentFrame: number): GameStateDto {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.global_frame === null || e.state == null) continue;
    if (e.global_frame <= currentFrame) return e.state;
  }
  return EMPTY_STATE;
}

/** Find a roster entry by jersey number. */
export function findPlayer(
  roster: RosterDto,
  number: number | null | undefined
): PlayerDto | undefined {
  if (number == null) return undefined;
  return roster.players.find((p) => p.number === number);
}

/**
 * Jersey tag as shown on buttons and chips: `#7`.
 *
 * Liberos used to read `#7 (L)`, which cost four characters on every
 * card in the narrowest column of the editor and pushed names into an
 * ellipsis. The cards mark them with a corner badge instead (see
 * `.libero-badge`); `liberos` is still taken so call sites do not all
 * have to change when a caller wants it back.
 */
export function jerseyLabel(
  jersey: number,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _liberos: readonly number[] = []
): string {
  return `#${jersey}`;
}

/** Display name for a player at a position; "?" when empty. */
export function positionLabel(
  roster: RosterDto,
  jersey: number | null | undefined,
  liberos: readonly number[] = []
): string {
  if (jersey == null) return "?";
  const p = findPlayer(roster, jersey);
  if (!p) return jerseyLabel(jersey, liberos);
  return `${jerseyLabel(p.number, liberos)} ${p.short_name || p.name.split(" ")[0]}`;
}

// ---- libero rule --------------------------------------------------------
//
// A libero may only play the back row. When a side-out rotates one into
// P4, the player they came on for has to go back in. The backend stores
// `match.liberos` but its state machine never reads it, so the pairing
// is derived here, as a pure fold over the event list: it survives
// reload, undo and reorder for free because nothing is stored.

/** Front-row slots in rotation order: P4 is where a back-row player
 *  lands first after a side-out. */
const FRONT_ROW = [4, 3, 2];

/** Position (4/3/2) of the first libero found in the front row, or null. */
export function frontRowLibero(
  positions: GameStateDto["home_positions"],
  liberos: readonly number[]
): { position: number; jersey: number } | null {
  for (const position of FRONT_ROW) {
    const jersey = positions[position];
    if (jersey != null && liberos.includes(jersey)) return { position, jersey };
  }
  return null;
}

export interface LiberoPairs {
  /**
   * Who each libero came on for in THIS set. A libero coming on
   * remembers who they replaced; a libero going off, for anyone by any
   * route, forgets it. Cleared at a set end, because positions are.
   */
  current: Record<number, number>;
  /**
   * The same, but never cleared: who this libero last came on for at
   * any point in the match.
   *
   * It exists for the case `current` cannot answer. A line-up entered
   * at set start puts the libero into an EMPTY slot, so they displaced
   * nobody and there is no pairing to remember - and the first rotation
   * of every set would stop to ask a question the previous set already
   * answered. A libero covers the same player set after set, so the
   * last pairing is the right guess; it is only a guess, so the caller
   * uses it just when that player is off court, and Undo is one tap.
   */
  last: Record<number, number>;
}

/**
 * Libero pairings as of the last event at or before `uptoFrame`.
 *
 * Derived rather than stored, as a pure fold over the event list, so it
 * survives reload, undo and reorder for free.
 */
export function liberoPairs(
  events: EventDto[],
  liberos: readonly number[],
  uptoFrame: number
): LiberoPairs {
  const isLibero = (j: number | null): j is number =>
    j != null && liberos.includes(j);
  const current: Record<number, number> = {};
  const last: Record<number, number> = {};
  let prev: GameStateDto = EMPTY_STATE;
  for (const e of events) {
    if (e.global_frame === null || e.state == null) continue;
    if (e.global_frame > uptoFrame) break;
    if (e.type === "set_end") {
      for (const k of Object.keys(current)) delete current[Number(k)];
      // `team` is ABSENT on a home substitution, not "home": the backend
      // omits any field equal to its dataclass default, and every event
      // family defaults `team` to HOME. Comparing against "home"
      // therefore skipped every home sub, so no pairing was ever built
      // and the rule asked who comes back at every single rotation.
      // `eventSummary` mirrors the same default for the same reason.
    } else if (e.type === "substitution" && (e.payload.team ?? "home") === "home") {
      const position = Number(e.payload.position);
      const incoming = Number(e.payload.player_in_number);
      const outgoing = prev.home_positions[position] ?? null;
      if (isLibero(outgoing)) delete current[outgoing];
      if (isLibero(incoming)) {
        if (outgoing != null && !isLibero(outgoing)) {
          current[incoming] = outgoing;
          last[incoming] = outgoing;
        } else {
          delete current[incoming];
        }
      }
    }
    prev = e.state;
  }
  return { current, last };
}

/** Just the current-set pairings. */
export function liberoReplacements(
  events: EventDto[],
  liberos: readonly number[],
  uptoFrame: number
): Record<number, number> {
  return liberoPairs(events, liberos, uptoFrame).current;
}
