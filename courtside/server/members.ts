/** Invite links, requests to join, and the admin's decisions. */
import type { Env } from "../shared/env";
import type { TeamDoc, TeamsDoc } from "../shared/docs";
import type { AdminState, Decision, Invite, JoinTeam, Membership, Role } from "../shared/api";
import { addedAdmins, adminEmails, type Viewer } from "./access";
import { getDoc, Names } from "./content";
import { HttpError, sameSecret } from "./http";
import { queueNotice } from "./notices";

const CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";

function newCode(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, (b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
}

// One invite link per team, kept in `settings` as `invite:<team>`.
const inviteKey = (team: string) => `invite:${team}`;

export async function inviteCode(env: Env, team: string, renew = false): Promise<string> {
  if (!renew) {
    const row = await env.DB.prepare("SELECT value FROM settings WHERE key = ?")
      .bind(inviteKey(team))
      .first<{ value: string }>();
    if (row) return row.value;
  }
  const code = newCode();
  await env.DB.prepare("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)").bind(inviteKey(team), code).run();
  return code;
}

/** The team an invite code opens, or 404. */
async function teamForCode(env: Env, code: string): Promise<TeamDoc> {
  const { results } = await env.DB.prepare("SELECT key, value FROM settings WHERE key LIKE 'invite:%'")
    .all<{ key: string; value: string }>();
  // Compare against every code, not stopping at a match, so timing
  // doesn't hint at how close a guess came.
  let team: string | null = null;
  for (const r of results) if (sameSecret(code, r.value)) team = r.key.slice("invite:".length);
  const doc = team ? await getDoc<TeamDoc>(env, `team/${team}`) : null;
  if (!doc) throw new HttpError(404, "This invite link doesn't work any more. Ask your team for a new one.");
  return doc;
}

/** The invite's team with its players, for choosing whom to claim. */
export async function joinTeam(env: Env, code: string): Promise<JoinTeam> {
  const t = await teamForCode(env, code);
  return {
    slug: t.slug,
    name: t.name,
    color: t.color,
    logo: t.logo,
    players: t.players.map((p) => ({ number: p.number, name: p.name })),
  };
}

export interface JoinRequest {
  role: Role;
  number?: number | null;
  note?: string;
}

export async function requestJoin(env: Env, v: Viewer, code: string, req: JoinRequest): Promise<Membership> {
  const team = await teamForCode(env, code);
  if (req.role !== "player" && req.role !== "coach") throw new HttpError(400, "Choose a player or coach.");
  const number = req.role === "player" ? Number(req.number) : null;
  if (number !== null && !team.players.some((p) => p.number === number)) {
    throw new HttpError(400, "No such player on this team.");
  }
  const note = String(req.note || "").trim().slice(0, 200);
  const now = Date.now();
  const existing = await env.DB.prepare(
    "SELECT * FROM members WHERE email = ? AND team = ? AND role = ? AND number IS ?"
  )
    .bind(v.email, team.slug, req.role, number)
    .first<Membership>();
  if (existing) {
    if (existing.status === "declined") {
      // Asking again after a "no" is allowed; it goes back in the queue.
      await env.DB.prepare(
        "UPDATE members SET status = 'pending', note = ?, requested_at = ?, decided_at = NULL, decided_by = NULL WHERE id = ?"
      )
        .bind(note, now, existing.id)
        .run();
      await queueNotice(env, "request", existing.id);
      return { ...existing, status: "pending", note, requested_at: now, decided_at: null, decided_by: null };
    }
    return existing;
  }
  const row = await env.DB.prepare(
    "INSERT INTO members (email, team, role, number, note, status, requested_at) VALUES (?, ?, ?, ?, ?, 'pending', ?) RETURNING *"
  )
    .bind(v.email, team.slug, req.role, number, note, now)
    .first<Membership>();
  await queueNotice(env, "request", row!.id);
  return row!;
}

export async function decide(env: Env, id: number, action: Decision, by: string): Promise<void> {
  if (action === "remove") {
    await env.DB.prepare("DELETE FROM members WHERE id = ?").bind(id).run();
    return;
  }
  if (action !== "approve" && action !== "decline") throw new HttpError(400, "Unknown decision.");
  const before = await env.DB.prepare("SELECT status FROM members WHERE id = ?").bind(id).first<{ status: string }>();
  if (!before) throw new HttpError(404, "No such request.");
  const status = action === "approve" ? "approved" : "declined";
  await env.DB.prepare("UPDATE members SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?")
    .bind(status, Date.now(), by, id)
    .run();
  // Tell them they're in - once, not again for an approval repeated.
  if (status === "approved" && before.status !== "approved") await queueNotice(env, "approved", id);
}

export async function pendingRequests(env: Env, names = new Names(env)) {
  const { results } = await env.DB.prepare("SELECT * FROM members WHERE status = 'pending' ORDER BY requested_at")
    .all<Membership>();
  return names.describeAll(results);
}

/** Make someone an admin, or not. Only people already approved on some
 *  team can be made one (a typo can't hand out the site); the owners in
 *  ADMIN_EMAILS stay admins whatever is done here. */
export async function setAdmin(env: Env, email: unknown, admin: boolean, by: string): Promise<void> {
  const e = String(email || "").trim().toLowerCase();
  if (!e) throw new HttpError(400, "Whose?");
  if (adminEmails(env).includes(e)) throw new HttpError(400, "That's an owner (ADMIN_EMAILS); change it in Cloudflare.");
  if (!admin) {
    await env.DB.prepare("DELETE FROM admins WHERE email = ?").bind(e).run();
    return;
  }
  const member = await env.DB.prepare("SELECT 1 AS x FROM members WHERE email = ? AND status = 'approved'").bind(e).first();
  if (!member) throw new HttpError(400, "Only someone already approved on a team can be made an admin.");
  await env.DB.prepare("INSERT OR IGNORE INTO admins (email, added_at, added_by) VALUES (?, ?, ?)").bind(e, Date.now(), by).run();
}

export async function adminState(env: Env): Promise<AdminState> {
  const names = new Names(env);
  const approved = await env.DB.prepare("SELECT * FROM members WHERE status = 'approved' ORDER BY team, role, number, email")
    .all<Membership>();
  const declined = await env.DB.prepare(
    "SELECT * FROM members WHERE status = 'declined' ORDER BY decided_at DESC LIMIT 50"
  ).all<Membership>();
  const teams = (await getDoc<TeamsDoc>(env, "teams"))?.teams ?? [];
  const invites: Invite[] = [];
  for (const t of teams) invites.push({ team: t.slug, team_name: t.name, code: await inviteCode(env, t.slug) });
  return {
    owners: adminEmails(env),
    admins: await addedAdmins(env),
    invites,
    requests: await pendingRequests(env, names),
    members: await names.describeAll(approved.results),
    declined: await names.describeAll(declined.results),
  };
}
