/**
 * Who may see and change what.
 *
 *   admin   (ADMIN_EMAILS)  everything, every team; approves requests
 *   coach   (of a team)     the team's videos, all stats, every reel, the schedule
 *   player  (of a team)     the team's videos, the stats and reels of the
 *                           claimed player only, and editing that player
 *
 * Signing in proves an email and nothing more: anyone can sign in, and
 * without an approved membership they see no team at all.
 */
import type { Env } from "../shared/env";
import type { Membership } from "../shared/api";

export interface Viewer {
  email: string;
  admin: boolean;
  memberships: Membership[];
}

export function adminEmails(env: Env): string[] {
  return (env.ADMIN_EMAILS || "")
    .split(/[,;\s]+/)
    // Forgiving about how the secret was typed: quotes, <angle brackets>.
    .map((s) => s.replace(/^["'<]+|["'>]+$/g, "").trim().toLowerCase())
    .filter(Boolean);
}

export async function loadViewer(env: Env, email: string): Promise<Viewer> {
  const { results } = await env.DB.prepare("SELECT * FROM members WHERE email = ? ORDER BY id")
    .bind(email)
    .all<Membership>();
  return { email, admin: adminEmails(env).includes(email), memberships: results };
}

function approved(v: Viewer, team: string): Membership[] {
  return v.memberships.filter((m) => m.status === "approved" && m.team === team);
}

export function canSeeTeam(v: Viewer, team: string): boolean {
  return v.admin || approved(v, team).length > 0;
}

/** Whose reels the viewer may watch on this team. */
export function reelFilter(v: Viewer, team: string): (n: number | null | undefined) => boolean {
  const mine = approved(v, team);
  if (v.admin || mine.some((m) => m.role === "coach")) return () => true;
  const numbers = new Set(mine.map((m) => m.number));
  return (n) => n != null && numbers.has(n);
}

/** Coaches and the admin: every player's stats, the team's totals, and
 *  changing the schedule. */
export function canManageTeam(v: Viewer, team: string): boolean {
  return v.admin || approved(v, team).some((m) => m.role === "coach");
}

export function canEditPlayer(v: Viewer, team: string, number: number): boolean {
  return v.admin || approved(v, team).some((m) => m.role === "player" && m.number === number);
}
