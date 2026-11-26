/** Reading what VME published, trimmed to what the viewer may see. */
import type { Env } from "../shared/env";
import type { MatchDoc, MatchSummary, Stats, TeamDoc, TeamsDoc, Video } from "../shared/docs";
import type { Membership, MembershipOut } from "../shared/api";
import { canManageTeam, canSeeTeam, reelFilter, type Viewer } from "./access";
import { HttpError } from "./http";
import { liveNow, pendingSchedule, phoneScores, streamLinks, type PhoneScore } from "./schedule";

export async function getDoc<T>(env: Env, key: string): Promise<T | null> {
  const row = await env.DB.prepare("SELECT json FROM docs WHERE key = ?").bind(key).first<{ json: string }>();
  return row ? (JSON.parse(row.json) as T) : null;
}

/** Full names, published for the edit form only (never by /api/doc). */
export interface PrivateDoc {
  players: Record<string, { name: string; short_name: string }>;
}

function keepVideo(allow: (n: number | null | undefined) => boolean) {
  return (v: Video) => (v.kind === "reel" || v.kind === "season" ? allow(v.player_number) : true);
}

/** Stats as this viewer may see them: everything for coaches and the
 *  admin; for a family, their own player's line and no team totals -
 *  the same rule as the reels. */
function trimStats(stats: Stats, all: boolean, allow: (n: number | null | undefined) => boolean): Stats {
  if (all) return stats;
  return { ...stats, players: stats.players.filter((l) => allow(l.number)), total: null };
}

/** A published document as this viewer may see it. */
export async function readDoc(env: Env, v: Viewer, key: string): Promise<unknown> {
  if (key === "teams") {
    const doc = await getDoc<TeamsDoc>(env, "teams");
    if (!doc) throw new HttpError(404, "Nothing has been published yet.");
    return { ...doc, teams: doc.teams.filter((t) => canSeeTeam(v, t.slug)) };
  }
  const [kind, team] = key.split("/");
  if ((kind !== "team" && kind !== "match") || !team) throw new HttpError(404, "Not found");
  if (!canSeeTeam(v, team)) throw new HttpError(403, "You don't have access to this team.");
  const allow = reelFilter(v, team);
  const keep = keepVideo(allow);
  const all = canManageTeam(v, team);
  if (kind === "team") {
    const stored = await getDoc<TeamDoc & { season_reels?: unknown }>(env, key);
    if (!stored) throw new HttpError(404, "Not found");
    // Season reels are off the site for now; an older VME still sends
    // them, unfiltered by player, so they are dropped here.
    const { season_reels: _dropped, ...doc } = stored;
    const merged = mergePhone(doc, await phoneScores(env, team), await streamLinks(env, team));
    return {
      ...doc,
      upcoming: merged.upcoming,
      stats: trimStats(doc.stats, all, allow),
      tournaments: merged.tournaments.map((t) => ({
        ...t,
        matches: t.matches.map((m) => ({ ...m, videos: m.videos.filter(keep) })),
      })),
      live: await liveNow(env, team),
      pending_schedule: all ? await pendingSchedule(env, team) : [],
      can_manage: all,
    } satisfies TeamDoc;
  }
  const doc = await getDoc<MatchDoc>(env, key);
  if (!doc) throw new HttpError(404, "Not found");
  const matchKey = key.split("/").slice(2).join("/");
  const phone = withPhone(doc, (await phoneScores(env, team)).get(matchKey), (await streamLinks(env, team)).get(matchKey));
  return {
    ...phone,
    stats: trimStats(doc.stats, all, allow),
    videos: phone.videos.filter(keep),
    live: (await liveNow(env, team)).find((l) => l.match_key === matchKey) ?? null,
  } satisfies MatchDoc;
}

/** Team logos are public to anyone signed in (the join page shows them);
 *  everything else about a team only to its members. */
export function canSeeImage(v: Viewer, key: string): boolean {
  const [kind, team, what] = key.split("/");
  if (kind !== "img" || !team) return false;
  return what === "logo" || canSeeTeam(v, team);
}

/** Team and player names for showing memberships to people. */
export class Names {
  private teams = new Map<string, TeamDoc | null>();
  constructor(private env: Env) {}

  async team(slug: string): Promise<TeamDoc | null> {
    if (!this.teams.has(slug)) this.teams.set(slug, await getDoc<TeamDoc>(this.env, `team/${slug}`));
    return this.teams.get(slug) ?? null;
  }

  async describe(m: Membership): Promise<MembershipOut> {
    const t = await this.team(m.team);
    const p = m.number != null ? t?.players.find((x) => x.number === m.number) : undefined;
    return {
      ...m,
      team_name: t?.name ?? m.team.replace(/_/g, " "),
      player_name: m.role === "coach" ? "" : `#${m.number}${p ? ` ${p.name}` : ""}`,
    };
  }

  async describeAll(rows: Membership[]): Promise<MembershipOut[]> {
    const out: MembershipOut[] = [];
    for (const m of rows) out.push(await this.describe(m));
    return out;
  }
}

function youtubeId(url: string): string | null {
  const m = /(?:[?&]v=|youtu\.be\/|\/live\/|\/embed\/)([A-Za-z0-9_-]{6,20})/.exec(url);
  return m ? m[1] : null;
}

/**
 * A match with what the phone knows filled in: its score while VME hasn't
 * tagged the match (VME's own score wins from then on), and its stream as
 * the full video until VME has one of its own.
 */
function withPhone<T extends MatchSummary>(m: T, score?: PhoneScore, stream?: string): T {
  let out = m;
  if (!m.scored && score) {
    out = {
      ...out,
      sets: score.sets,
      home_sets: score.home_sets,
      away_sets: score.away_sets,
      result: score.ended && score.home_sets !== score.away_sets ? (score.home_sets > score.away_sets ? "W" : "L") : null,
      score_source: "phone",
      current: score.ended ? null : [score.home, score.away],
      played: true,
    };
  }
  const id = stream ? youtubeId(stream) : null;
  if (id && !m.videos.some((v) => v.kind === "full")) {
    out = { ...out, videos: [{ kind: "full", youtube_id: id, stream: true }, ...out.videos] };
  }
  return out;
}

/** The team's matches with the phone's scores and streams merged in. A
 *  scheduled match the phone has started scoring moves from `upcoming`
 *  to the played list. */
function mergePhone(doc: TeamDoc, scores: Map<string, PhoneScore>, streams: Map<string, string>): Pick<TeamDoc, "tournaments" | "upcoming"> {
  const tournaments = doc.tournaments.map((t) => ({
    ...t,
    matches: t.matches.map((m) => withPhone(m, scores.get(m.key), streams.get(m.key))),
  }));
  const upcoming = [];
  for (const u of doc.upcoming ?? []) {
    const score = scores.get(u.key);
    if (!score) {
      upcoming.push(u);
      continue;
    }
    const summary: MatchSummary = {
      key: u.key, date: u.date, index: u.index, opponent: u.opponent, opponent_logo: null,
      sets: [], home_sets: 0, away_sets: 0, result: null, videos: [], time: u.time, played: false, scored: false,
    };
    let group = tournaments.find((t) => t.slug === u.tournament);
    if (!group) tournaments.push((group = { slug: u.tournament, name: u.tournament_name, matches: [] }));
    group.matches.push(withPhone(summary, score, streams.get(u.key)));
    group.matches.sort((a, b) => a.date.localeCompare(b.date) || (a.index ?? 0) - (b.index ?? 0));
  }
  // Latest tournament first, as VME orders them.
  const last = (t: { matches: MatchSummary[] }) => t.matches.reduce((d, m) => (m.date > d ? m.date : d), "");
  tournaments.sort((a, b) => last(b).localeCompare(last(a)));
  return { tournaments, upcoming };
}
