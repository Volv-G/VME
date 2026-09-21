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

/** Jersey tag as shown on buttons and chips: `#7`, or `#7 (L)` for a libero. */
export function jerseyLabel(jersey: number, liberos: readonly number[]): string {
  return liberos.includes(jersey) ? `#${jersey} (L)` : `#${jersey}`;
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

/**
 * Libero jersey -> jersey of the player they came on for, as of the
 * last event at or before `uptoFrame`.
 *
 * A libero coming on remembers who they replaced; a libero going off,
 * for anyone by any route, forgets it. Placing a libero into an empty
 * slot (lineup entry at set start) records nothing - which is what
 * makes the caller fall back to the picker. Cleared on set end,
 * because positions are.
 */
export function liberoReplacements(
  events: EventDto[],
  liberos: readonly number[],
  uptoFrame: number
): Record<number, number> {
  const isLibero = (j: number | null): j is number =>
    j != null && liberos.includes(j);
  const pairs: Record<number, number> = {};
  let prev: GameStateDto = EMPTY_STATE;
  for (const e of events) {
    if (e.global_frame === null || e.state == null) continue;
    if (e.global_frame > uptoFrame) break;
    if (e.type === "set_end") {
      for (const k of Object.keys(pairs)) delete pairs[Number(k)];
    } else if (e.type === "substitution" && e.payload.team === "home") {
      const position = Number(e.payload.position);
      const incoming = Number(e.payload.player_in_number);
      const outgoing = prev.home_positions[position] ?? null;
      if (isLibero(outgoing)) delete pairs[outgoing];
      if (isLibero(incoming)) {
        if (outgoing != null && !isLibero(outgoing)) pairs[incoming] = outgoing;
        else delete pairs[incoming];
      }
    }
    prev = e.state;
  }
  return pairs;
}
