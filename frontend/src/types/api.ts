export interface PlayerDto {
  name: string;
  number: number;
  short_name?: string | null;
  profile_pic_path?: string | null;
}

export interface YouTubeConfigDto {
  privacy_status: "private" | "unlisted" | "public";
  playlist_id?: string | null;
  title_template?: string | null;
  description_template?: string | null;
}

/** Team-level render-output naming templates. Slashes in a template
 *  create subfolders inside the match's renders/ dir. All fields
 *  optional - empty means "use server default". */
export interface NamingConfigDto {
  /** Filename for full / preview renders. Default:
   *  `{label}_{timestamp}.mp4` */
  full_render_template?: string | null;
  /** Path (relative to renders/) for one per-event highlight clip.
   *  Default: `highlights/{team}/{player}/{action}/
   *           {date}_vs_{opponent}_{match_timestamp}_{action}.mp4` */
  highlight_template?: string | null;
  /** Path (relative to renders/) for one FocusIn/Out clip. Default:
   *  `focused/{team}/{player}/
   *   {date}_vs_{opponent}_{start_timestamp}-{end_timestamp}.mp4` */
  focused_template?: string | null;
}

export interface RosterDto {
  team_name?: string | null;
  team_color?: string | null;
  /** Team-level YouTube upload defaults. Optional on input - the server
   *  preserves any previously-saved settings when omitted. */
  youtube?: YouTubeConfigDto | null;
  /** Team-level render-output naming templates. Optional - omitted
   *  block leaves previously-saved values intact. */
  naming?: NamingConfigDto | null;
  players: PlayerDto[];
}

export interface TournamentInfoDto {
  abbreviation?: string | null;
  full_name?: string | null;
}

export interface TeamSummary {
  name: string;
  has_roster: boolean;
  tournament_count: number;
}

export interface TournamentSummary {
  team: string;
  name: string;
  match_count: number;
}

export interface MatchSummary {
  team: string;
  tournament: string;
  /** Date folder under the tournament. Also the canonical date string. */
  date: string;
  /** Match folder leaf (URL identifier), e.g. "03_North_Vipers". */
  name: string;
  /** 1-based match order on this date. Null for legacy unnumbered folders. */
  match_index: number | null;
  opponent: string;
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
  tournament: string;
  date: string;
  /** Match folder leaf (URL identifier). */
  name: string;
  /** 1-based match order on this date. */
  match_index: number | null;
  opponent: string;
  fps: number;
  clips: ClipDto[];
  events: EventDto[];
  home_roster: RosterDto;
  opponent_roster: RosterDto;
}

export interface RenderJobDto {
  id: string;
  team: string;
  tournament: string;
  date: string;
  match: string;
  label: string;
  /** What sort of render this job represents.
   *  - "full"               whole match, one output
   *  - "preview"            window around `playhead_frame`, one output
   *  - "highlights"         one mp4 per highlight (rally bounds)
   *  - "focused_highlights" one mp4 per FocusIn/FocusOut span */
  kind:
    | "full"
    | "preview"
    | "highlights"
    | "focused_highlights"
    | "youtube_upload";
  /** Render parameters captured at enqueue time. */
  playhead_frame: number | null;
  seconds_around: number | null;
  /** Display helpers - filled by the backend so the queue UI doesn't need
   *  to refetch each match.json. */
  opponent: string;
  match_index: number | null;
  /** Immediate jobs (previews) bypass the queue and run right away. */
  immediate: boolean;
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

/** Aggregate state of the global render queue. */
export interface QueueStateDto {
  /** True when the dispatcher is processing pending jobs. */
  active: boolean;
  pending: number;
  running: number;
  total: number;
}

export interface RenderFileDto {
  filename: string;
  size_bytes: number;
  /** Unix seconds; file mtime, used as creation proxy. */
  created_at: number;
}

/** A full-match render listed on the team dashboard, with optional
 *  YouTube upload state read from the per-render sidecar. */
export interface FullRenderDto {
  team: string;
  tournament: string;
  date: string;
  match: string;
  filename: string;
  size_bytes: number;
  created_at: number;
  opponent: string;
  match_index: number | null;
  youtube_video_id?: string | null;
  youtube_uploaded_at?: number | null;
  /** privacyStatus YouTube actually applied (read back from the upload
   *  response). When this differs from `youtube_requested_privacy_status`
   *  the upload was silently downgraded - most often because the OAuth
   *  client is in Google Cloud Console's "Testing" publishing state. */
  youtube_privacy_status?: string | null;
  youtube_requested_privacy_status?: string | null;
}

/** Whether the backend can upload to YouTube right now.
 *  `configured=false` => UI disables the upload button and shows
 *  `reason` as a tooltip. */
export interface YouTubeStatusDto {
  configured: boolean;
  reason: string;
  has_client_secret: boolean;
  has_token: boolean;
  library_installed: boolean;
}

export interface AutoCutsResultDto {
  match: MatchDto;
  added: number;
  skipped_existing: number;
  skipped_missing_time: number;
  skipped_too_short: number;
  added_set_ends: number;
  /** GameStart / GameEnd lifecycle events inserted by the run (0 or 1 each). */
  added_game_start: number;
  added_game_end: number;
  skipped_too_close: number;
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
