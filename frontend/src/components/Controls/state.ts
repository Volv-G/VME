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

/** Display name for a player at a position; "?" when empty. */
export function positionLabel(roster: RosterDto, jersey: number | null | undefined): string {
  if (jersey == null) return "?";
  const p = findPlayer(roster, jersey);
  if (!p) return `#${jersey}`;
  return `#${p.number} ${p.short_name || p.name.split(" ")[0]}`;
}
