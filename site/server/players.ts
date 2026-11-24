/** Players edited on the site, and handing those edits to VME. */
import type { Env } from "../shared/env";
import type { PlayerInfo } from "../shared/api";
import { canEditPlayer, type Viewer } from "./access";
import { getDoc, Names, type PrivateDoc } from "./content";
import { blobBytes, fromBase64, HttpError, toBase64 } from "./http";

const MAX_PHOTO_BYTES = 2 * 1024 * 1024;

interface EditRow {
  id: number;
  team: string;
  number: number;
  name: string | null;
  short_name: string | null;
  photo: unknown;
  submitted_by: string;
  submitted_at: number;
}

function mayEdit(v: Viewer, team: string, number: number) {
  if (!canEditPlayer(v, team, number)) throw new HttpError(403, "You can't edit this player.");
}

export async function playerInfo(env: Env, v: Viewer, team: string, number: number): Promise<PlayerInfo> {
  mayEdit(v, team, number);
  const doc = await new Names(env).team(team);
  const p = doc?.players.find((x) => x.number === number);
  if (!doc || !p) throw new HttpError(404, "No such player.");
  const names = (await getDoc<PrivateDoc>(env, `private/${team}`))?.players[String(number)];
  const last = await env.DB.prepare(
    "SELECT name, short_name, photo IS NOT NULL AS photo, submitted_at FROM player_edits WHERE team = ? AND number = ? ORDER BY id DESC LIMIT 1"
  )
    .bind(team, number)
    .first<{ name: string | null; short_name: string | null; photo: number; submitted_at: number }>();
  return {
    team,
    number,
    name: names?.name ?? "",
    short_name: names?.short_name ?? "",
    photo: p.photo,
    pending: last ? { ...last, photo: !!last.photo } : null,
  };
}

export interface PlayerEdit {
  name?: string | null;
  short_name?: string | null;
  /** JPEG, base64, already shrunk by the browser. */
  photo_base64?: string | null;
}

export async function editPlayer(env: Env, v: Viewer, team: string, number: number, e: PlayerEdit): Promise<void> {
  mayEdit(v, team, number);
  const name = e.name == null ? null : String(e.name).trim();
  const shortName = e.short_name == null ? null : String(e.short_name).trim();
  if (name !== null && (name.length < 1 || name.length > 80)) throw new HttpError(400, "The name must be 1-80 characters.");
  if (shortName !== null && shortName.length > 40) throw new HttpError(400, "The short name must be at most 40 characters.");
  let photo: Uint8Array | null = null;
  if (e.photo_base64) {
    try {
      photo = fromBase64(e.photo_base64);
    } catch {
      throw new HttpError(400, "The photo could not be read.");
    }
    if (photo.length > MAX_PHOTO_BYTES) throw new HttpError(413, "The photo is too large.");
    if (photo[0] !== 0xff || photo[1] !== 0xd8) throw new HttpError(400, "The photo must be a JPEG.");
  }
  if (name === null && shortName === null && photo === null) throw new HttpError(400, "Nothing to change.");
  await env.DB.prepare(
    "INSERT INTO player_edits (team, number, name, short_name, photo, submitted_by, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
  )
    .bind(team, number, name, shortName, photo, v.email, Date.now())
    .run();
}

/** Edits waiting for VME, oldest first, photos as base64. */
export async function pendingEdits(env: Env) {
  const { results } = await env.DB.prepare("SELECT * FROM player_edits ORDER BY id").all<EditRow>();
  return results.map((r) => ({
    id: r.id,
    team: r.team,
    number: r.number,
    name: r.name,
    short_name: r.short_name,
    photo_base64: r.photo == null ? null : toBase64(blobBytes(r.photo)),
    submitted_by: r.submitted_by,
    submitted_at: r.submitted_at,
  }));
}

export async function ackEdits(env: Env, ids: number[]): Promise<void> {
  const clean = ids.map(Number).filter(Number.isInteger);
  if (!clean.length) return;
  await env.DB.batch(clean.map((id) => env.DB.prepare("DELETE FROM player_edits WHERE id = ?").bind(id)));
}
