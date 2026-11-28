/**
 * The documents VME publishes and the site reads, by key:
 *
 *   teams                                     TeamsDoc
 *   team/<team>                               TeamDoc
 *   match/<team>/<tournament>/<date>/<match>  MatchDoc
 *   private/<team>                            full names; only for the edit form (never via /api/doc)
 *   clubs                                     ClubsDoc (coaches and admins)
 *
 * Written by `backend/app/publish/` in VME; keep the two in step.
 * Image fields hold a key for `/api/image/<key>`.
 */

export interface TeamsDoc {
  teams: { slug: string; name: string; color: string; logo: string | null }[];
  published_at: number;
}

export interface StatsLine {
  number: number | null;
  name: string;
  events: Record<string, number>;
  serves: number;
  serves_won: number;
  plays: number;
}

export interface Stats {
  matches: number;
  columns: string[];
  players: StatsLine[];
  /** The whole team. Only coaches and the admin get it (and every
   *  player's line); a family gets their own player's lines and null. */
  total: StatsLine | null;
}

/** A video on YouTube (`youtube_id`) or, for reels VME uploads to
 *  OneDrive, an anonymous OneDrive link: `onedrive_embed` plays in a
 *  frame, `onedrive_url` opens OneDrive's own page. */
export interface Video {
  kind: "full" | "condensed" | "reel" | "season";
  /** The phone's live stream, standing in for the full video until VME has one. */
  stream?: boolean;
  youtube_id?: string;
  onedrive_embed?: string;
  onedrive_url?: string;
  /** A reel's web copy on the site itself, played in place: the key
   *  for /api/video/<key>. The OneDrive links stay for the original. */
  web?: string;
  /** A reel's thumbnail, same place: the key for /api/video/<key>. */
  web_poster?: string;
  /** Set for reels: whose plays they are. */
  player_number?: number | null;
  player_label?: string;
}

export interface MatchSummary {
  /** `<tournament>/<date>/<match>`, the tail of the match doc's key. */
  key: string;
  date: string;
  /** Order on the day; null for the day's only match. */
  index: number | null;
  opponent: string;
  opponent_logo: string | null;
  /** Finished sets, ours first: [[25, 19], [22, 25]]. */
  sets: [number, number][];
  home_sets: number;
  away_sets: number;
  /** "W" / "L" once the match is over, else null. */
  result: "W" | "L" | null;
  videos: Video[];
  /** Scheduled start, "HH:MM" local, when known. */
  time?: string | null;
  /** Has footage or events; false = only on the schedule so far. */
  played?: boolean;
  /** Tagged in VME: the score is VME's own. */
  scored?: boolean;
  /** Added by the site when read: the score is the phone's (VME hasn't
   *  tagged the match yet), and the current set's points while it's on. */
  score_source?: "phone";
  current?: [number, number] | null;
}

/** A match on the schedule that hasn't been played yet. */
export interface UpcomingMatch {
  key: string;
  tournament: string;
  tournament_name: string;
  date: string;
  time: string | null;
  index: number | null;
  opponent: string;
  opponent_logo?: string | null;
  opponent_color?: string | null;
}

/** The opponents' clubs and schools set up in VME, to pick from when
 *  adding a match: the name, colours (the first is the main one) and
 *  badge they give it. Key `clubs`; badges are `club/<id>` images. */
export interface ClubsDoc {
  clubs: { id: string; name: string; colors: string[]; logo: string | null }[];
  published_at: number;
}

/** A change to the schedule left here, waiting for VME to apply it. */
export interface ScheduleChange {
  id: number;
  action: "add" | "update" | "delete";
  key: string | null;
  tournament: string | null;
  date: string | null;
  time: string | null;
  opponent: string | null;
  club?: string | null;
  color?: string | null;
  submitted_by: string;
  submitted_at: number;
}

/** A stream the phone says is on now (server/schedule.ts). */
export interface LiveStream {
  team: string;
  id: string;
  match_key: string | null;
  opponent: string;
  title: string;
  url: string;
  started_at: number;
}

export interface TeamDoc {
  slug: string;
  name: string;
  color: string;
  logo: string | null;
  /** The crest made for the faint page background (no white disc behind it). */
  watermark?: string | null;
  players: { number: number; name: string; photo: string | null }[];
  stats: Stats;
  tournaments: { slug: string; name: string; matches: MatchSummary[] }[];
  /** Scheduled, not played yet, soonest first. */
  upcoming?: UpcomingMatch[];
  /** Every tournament folder, for picking one when adding a match. */
  tournament_names?: { slug: string; name: string }[];
  published_at: number;
  /** Added by the site when read, not published by VME: */
  live?: LiveStream[];
  /** Coaches and the admin: schedule changes VME hasn't applied yet. */
  pending_schedule?: ScheduleChange[];
  /** May this viewer change the schedule (and see every player's stats)? */
  can_manage?: boolean;
}

export interface MatchDoc extends MatchSummary {
  team: string;
  team_name: string;
  team_color?: string;
  team_logo?: string | null;
  team_watermark?: string | null;
  tournament: string;
  tournament_name: string;
  stats: Stats;
  published_at: number;
  /** Added by the site when read: this match streaming now. */
  live?: LiveStream | null;
}
