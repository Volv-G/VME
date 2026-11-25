/**
 * The documents VME publishes and the site reads, by key:
 *
 *   teams                                     TeamsDoc
 *   team/<team>                               TeamDoc
 *   match/<team>/<tournament>/<date>/<match>  MatchDoc
 *   private/<team>                            full names; only for the edit form (never via /api/doc)
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
  total: StatsLine;
}

/** A video on YouTube (`youtube_id`) or, for reels VME uploads to
 *  OneDrive, an anonymous OneDrive link: `onedrive_embed` plays in a
 *  frame, `onedrive_url` opens OneDrive's own page. */
export interface Video {
  kind: "full" | "condensed" | "reel" | "season";
  youtube_id?: string;
  onedrive_embed?: string;
  onedrive_url?: string;
  /** A reel's web copy on the site itself, played in place: the key
   *  for /api/video/<key>. The OneDrive links stay for the original. */
  web?: string;
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
}

export interface TeamDoc {
  slug: string;
  name: string;
  color: string;
  logo: string | null;
  players: { number: number; name: string; photo: string | null }[];
  stats: Stats;
  tournaments: { slug: string; name: string; matches: MatchSummary[] }[];
  published_at: number;
}

export interface MatchDoc extends MatchSummary {
  team: string;
  team_name: string;
  team_color?: string;
  team_logo?: string | null;
  tournament: string;
  tournament_name: string;
  stats: Stats;
  published_at: number;
}
