/** Reading what VME published, trimmed to what the viewer may see. */
import type { Env } from "../shared/env";
import type { MatchDoc, TeamDoc, TeamsDoc, Video } from "../shared/docs";
import type { Membership, MembershipOut } from "../shared/api";
import { canSeeTeam, reelFilter, type Viewer } from "./access";
import { HttpError } from "./http";

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
  const keep = keepVideo(reelFilter(v, team));
  if (kind === "team") {
    const doc = await getDoc<TeamDoc>(env, key);
    if (!doc) throw new HttpError(404, "Not found");
    return {
      ...doc,
      season_reels: doc.season_reels.filter(keep),
      tournaments: doc.tournaments.map((t) => ({
        ...t,
        matches: t.matches.map((m) => ({ ...m, videos: m.videos.filter(keep) })),
      })),
    } satisfies TeamDoc;
  }
  const doc = await getDoc<MatchDoc>(env, key);
  if (!doc) throw new HttpError(404, "Not found");
  return { ...doc, videos: doc.videos.filter(keep) } satisfies MatchDoc;
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
