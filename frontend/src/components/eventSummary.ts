import type { EventDto, GameStateDto, PlayerDto, RosterDto } from "../types/api";
import { findPlayer } from "./Controls/state";

export interface SummaryContext {
  homeRoster: RosterDto;
  opponentRoster: RosterDto;
  homeName: string;
  opponentName: string;
}

/**
 * Human-readable description of a match event for the EventList.
 *
 * `prevState` is the GameState BEFORE this event was applied (i.e. the state
 * from the previous event, or EMPTY_STATE for the first one). It's needed for:
 *   - substitution: looking up who was at the position previously
 *   - ball_served: looking up who's at position 1 of the serving team
 *
 * `event.state` is the state AFTER this event (already on the DTO from the server).
 */
export function summarizeEvent(
  event: EventDto,
  prevState: GameStateDto,
  ctx: SummaryContext
): string {
  const p = event.payload;
  const teamLabel = (t: string | undefined) =>
    t === "home" ? ctx.homeName : t === "away" ? ctx.opponentName : "?";
  const rosterFor = (t: string | undefined) =>
    t === "home" ? ctx.homeRoster : ctx.opponentRoster;

  switch (event.type) {
    case "substitution": {
      const team = String(p.team || "home");
      const pos = Number(p.position ?? 0);
      const inNum = (p.player_in_number as number | null) ?? null;
      const positions = team === "home" ? prevState.home_positions : prevState.away_positions;
      const outNum = positions[pos] ?? null;
      const inLabel = playerLabel(rosterFor(team), inNum);
      const outLabel = playerLabel(rosterFor(team), outNum);
      const teamStr = ` (${teamLabel(team)})`;
      if (outNum == null) {
        return `Sub: ${inLabel} enters at P${pos}${teamStr}`;
      }
      return `Sub: ${inLabel} for ${outLabel} at P${pos}${teamStr}`;
    }

    case "score": {
      const team = String(p.team || "home");
      const after = event.state;
      const score = after ? ` (${after.home_score}-${after.away_score})` : "";
      return `${teamLabel(team)} +1${score}`;
    }

    case "first_serve": {
      const team = String(p.team || "home");
      return `1st Serve: ${teamLabel(team)}`;
    }

    case "ball_served": {
      const serving = prevState.serving_team ?? event.state?.serving_team ?? null;
      if (!serving) return "Ball served";
      const positions = serving === "home" ? prevState.home_positions : prevState.away_positions;
      const jersey = positions[1] ?? null;
      const who = playerLabel(serving === "home" ? ctx.homeRoster : ctx.opponentRoster, jersey);
      return `Ball served: ${teamLabel(serving)} ${who}`;
    }

    case "kill":
    case "ace":
    case "assist":
    case "block":
    case "dive":
    case "dig":
    case "highlight":
    case "focus_in": {
      const team = String(p.team || "home");
      const num = (p.player_number as number | null) ?? null;
      const verb = TYPE_VERB[event.type] ?? event.type;
      return `${verb}: ${playerLabel(rosterFor(team), num)} (${teamLabel(team)})`;
    }

    case "focus_out":
      return "Focus out";

    case "score_correction": {
      const fmt = (n: number) => (n >= 0 ? `+${n}` : `${n}`);
      const h = (p.home_delta as number) ?? 0;
      const a = (p.away_delta as number) ?? 0;
      const after = event.state;
      const score = after ? ` -> (${after.home_score}-${after.away_score})` : "";
      return `Score correction: H${fmt(h)}, A${fmt(a)}${score}`;
    }

    case "message":
      return `Message: "${String(p.text || "").slice(0, 60)}"`;

    case "cut_start":
      return "Cut start";
    case "cut_end": {
      const fade = (p.fade_frames as number) ?? 0;
      const shift = (p.frame_shift as number) ?? 0;
      return `Cut end (fade ${fade}, shift ${shift})`;
    }
    case "clip_transition": {
      const from = String(p.from_clip_id ?? "");
      const to = String(p.to_clip_id ?? "");
      return `Transition: ${from.slice(0, 6)} → ${to.slice(0, 6)}`;
    }
    case "set_end":
      return "End of set";
    case "game_start":
      return "Game start";
    case "game_end":
      return "Game end";
    case "replay":
      return "Replay";

    default:
      return event.type;
  }
}

const TYPE_VERB: Record<string, string> = {
  kill: "Kill",
  ace: "Ace",
  assist: "Assist",
  block: "Block",
  dive: "Dive",
  dig: "Dig",
  highlight: "Highlight",
  focus_in: "Focus on",
};

function playerLabel(roster: RosterDto, jersey: number | null | undefined): string {
  if (jersey == null) return "?";
  const p = findPlayer(roster, jersey);
  if (!p) return `#${jersey}`;
  const name = p.short_name || p.name;
  return `#${p.number} ${name}`;
}

/** Re-exported convenience: typed accessor for player metadata. */
export type { PlayerDto };
