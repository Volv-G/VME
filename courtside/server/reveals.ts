/**
 * No-spoiler reveals, per person: which matches' scores (and which
 * seasons' stats) someone chose to see, and which reels they have
 * watched ("seen:<team>/<match key>/<number>") and the videos they have
 * come across ("known:...", to flag one added later), so their phone and laptop agree.
 * The ids are made by the app (src/Spoilers.tsx); the site only stores them.
 */
import type { Env } from "../shared/env";
import type { Viewer } from "./access";
import { HttpError } from "./http";

const ID = /^(match|season|seen|known):[^\x00-\x1f]{1,300}$/;
const MAX_PER_CALL = 500;

export async function listReveals(env: Env, v: Viewer): Promise<string[]> {
  if (!v.email) return [];
  const rows = await env.DB.prepare("SELECT id FROM reveals WHERE email = ?").bind(v.email).all<{ id: string }>();
  return rows.results.map((r) => r.id);
}

export async function addReveals(env: Env, v: Viewer, ids: unknown): Promise<void> {
  if (!v.email) throw new HttpError(403, "Not signed in.");
  if (!Array.isArray(ids) || ids.length > MAX_PER_CALL || !ids.every((id) => typeof id === "string" && ID.test(id))) {
    throw new HttpError(400, "Bad reveal ids.");
  }
  if (!ids.length) return;
  const now = Date.now();
  const insert = env.DB.prepare("INSERT OR IGNORE INTO reveals (email, id, revealed_at) VALUES (?, ?, ?)");
  await env.DB.batch(ids.map((id) => insert.bind(v.email, id, now)));
}
