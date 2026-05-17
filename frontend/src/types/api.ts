export interface PlayerDto {
  name: string;
  number: number;
  short_name?: string | null;
  profile_pic_path?: string | null;
}

export interface RosterDto {
  team_name?: string | null;
  team_color?: string | null;
  players: PlayerDto[];
}

export interface TeamSummary {
  name: string;
  has_roster: boolean;
  match_count: number;
}

export interface MatchSummary {
  team: string;
  name: string;
  opponent: string;
  date: string;
  clip_count: number;
  has_match_json: boolean;
  has_video: boolean;
}

export interface ClipDto {
  id: string;
  filename: string;
  frame_count: number;
  fps: number;
  width: number;
  height: number;
  /** Unix timestamp (UTC seconds) when the recording started. May be null
   *  for legacy clips that haven't been re-probed. */
  start_recording_time: number | null;
}

export interface GameStateDto {
  home_score: number;
  away_score: number;
  home_sets: number;
  away_sets: number;
  serving_team: "home" | "away" | null;
  /** Position number (1-6) -> player jersey number, or null when empty. */
  home_positions: Record<number, number | null>;
  away_positions: Record<number, number | null>;
  game_started: boolean;
  game_ended: boolean;
  in_cut_region: boolean;
  ball_served_since_last_score: boolean;
}

export interface EventDto {
  type: string;
  id: number;
  payload: Record<string, unknown>;
  global_frame: number | null;
  /** Server-computed game state snapshot AFTER this event. Null for ClipTransition. */
  state: GameStateDto | null;
}

export interface MatchDto {
  team: string;
  name: string;
  opponent: string;
  date: string;
  fps: number;
  clips: ClipDto[];
  events: EventDto[];
  home_roster: RosterDto;
  opponent_roster: RosterDto;
}

export interface RenderJobDto {
  id: string;
  team: string;
  match: string;
  label: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  percent: number;
  phase: string;
  message: string;
  output_filename: string | null;
  error: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  cancel_requested: boolean;
}

export interface AutoCutsResultDto {
  match: MatchDto;
  added: number;
  skipped_existing: number;
  skipped_missing_time: number;
  skipped_too_short: number;
  skipped_too_far: number;
}

export type Team = "home" | "away";

export const EVENT_TYPES = [
  "game_start",
  "game_end",
  "set_end",
  "first_serve",
  "ball_served",
  "score",
  "score_correction",
  "kill",
  "ace",
  "assist",
  "block",
  "dive",
  "dig",
  "highlight",
  "substitution",
  "replay",
  "cut_start",
  "cut_end",
  "clip_transition",
  "message",
  "focus_in",
  "focus_out",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];
