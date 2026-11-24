/** The invite link, requests to join, and the admin's decisions. */
import type { Env } from "../shared/env";
import type { TeamsDoc } from "../shared/docs";
import type { AdminState, Decision, JoinTeam, Membership, Role } from "../shared/api";
import type { Viewer } from "./access";
import { getDoc, Names } from "./content";
import { HttpError, sameSecret } from "./http";

const CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";

function newCode(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, (b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
}

export async function inviteCode(env: Env, renew = false): Promise<string> {
  if (!renew) {
    const row = await env.DB.prepare("SELECT value FROM settings WHERE key = 'invite_code'").first<{ value: string }>();
    if (row) return row.value;
  }
  const code = newCode();
  await env.DB.prepare("INSERT OR REPLACE INTO settings (key, value) VALUES ('invite_code', ?)").bind(code).run();
  return code;
}

async function checkCode(env: Env, code: string): Promise<void> {
  const row = await env.DB.prepare("SELECT value FROM settings WHERE key = 'invite_code'").first<{ value: string }>();
  if (!row || !sameSecret(code, row.value)) {
    throw new HttpError(404, "This invite link doesn't work any more. Ask for a new one.");
  }
}

/** Every team with its players, for choosing whom to claim. */
export async function joinTeams(env: Env, code: string): Promise<JoinTeam[]> {
  await checkCode(env, code);
  const teams = await getDoc<TeamsDoc>(env, "teams");
  const names = new Names(env);
  const out: JoinTeam[] = [];
  for (const t of teams?.teams ?? []) {
    const doc = await names.team(t.slug);
    out.push({ ...t, players: (doc?.players ?? []).map((p) => ({ number: p.number, name: p.name })) });
  }
  return out;
}

export interface JoinRequest {
  team: string;
  role: Role;
  number?: number | null;
  note?: string;
}

export async function requestJoin(env: Env, v: Viewer, code: string, req: JoinRequest): Promise<Membership> {
  await checkCode(env, code);
  const team = await new Names(env).team(String(req.team || ""));
  if (!team) throw new HttpError(400, "No such team.");
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
      return { ...existing, status: "pending", note, requested_at: now, decided_at: null, decided_by: null };
    }
    return existing;
  }
  const row = await env.DB.prepare(
    "INSERT INTO members (email, team, role, number, note, status, requested_at) VALUES (?, ?, ?, ?, ?, 'pending', ?) RETURNING *"
  )
    .bind(v.email, team.slug, req.role, number, note, now)
    .first<Membership>();
  return row!;
}

export async function decide(env: Env, id: number, action: Decision, by: string): Promise<void> {
  if (action === "remove") {
    await env.DB.prepare("DELETE FROM members WHERE id = ?").bind(id).run();
    return;
  }
  if (action !== "approve" && action !== "decline") throw new HttpError(400, "Unknown decision.");
  const r = await env.DB.prepare("UPDATE members SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?")
    .bind(action === "approve" ? "approved" : "declined", Date.now(), by, id)
    .run();
  if (!r.meta.changes) throw new HttpError(404, "No such request.");
}

export async function pendingRequests(env: Env, names = new Names(env)) {
  const { results } = await env.DB.prepare("SELECT * FROM members WHERE status = 'pending' ORDER BY requested_at")
    .all<Membership>();
  return names.describeAll(results);
}

export async function adminState(env: Env): Promise<AdminState> {
  const names = new Names(env);
  const approved = await env.DB.prepare("SELECT * FROM members WHERE status = 'approved' ORDER BY team, role, number, email")
    .all<Membership>();
  const declined = await env.DB.prepare(
    "SELECT * FROM members WHERE status = 'declined' ORDER BY decided_at DESC LIMIT 50"
  ).all<Membership>();
  return {
    invite_code: await inviteCode(env),
    requests: await pendingRequests(env, names),
    members: await names.describeAll(approved.results),
    declined: await names.describeAll(declined.results),
  };
}
