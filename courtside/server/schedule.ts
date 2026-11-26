/**
 * The schedule and live streams.
 *
 * Upcoming matches are VME's (the team doc's `upcoming`). Changes made
 * here - by a coach or the admin on the site, or from the streaming
 * phone (server/phone.ts) - wait in `schedule_edits` until VME fetches
 * them on its next sync, applies them and republishes; until then they
 * show as pending.
 *
 * A stream is live while the phone keeps saying so: it reports when it
 * starts, every few minutes while it goes on, and when it stops. One that
 * falls silent (phone died, signal gone) stops counting after STALE_MS.
 *
 * The phone also sends its score as it goes (phone_scores). It stands in
 * for a match VME hasn't tagged yet - scores, never stats - and VME's own
 * score replaces it once VME has the full video tagged (`scored` on the match).
 */
import type { Env } from "../shared/env";
import type { LiveStream, ScheduleChange } from "../shared/docs";
import { HttpError } from "./http";

const STALE_MS = 20 * 60 * 1000;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^([01]?\d|2[0-3]):[0-5]\d$/;
const KEY = /^[^/\x00-\x1f]{1,120}\/\d{4}-\d{2}-\d{2}\/[^/\x00-\x1f]{1,120}$/;

export interface ScheduleEditIn {
  action?: string;
  key?: string | null;
  tournament?: string | null;
  date?: string | null;
  time?: string | null;
  opponent?: string | null;
}

function text(v: unknown, max: number): string | null {
  if (v == null) return null;
  const s = String(v).trim();
  if (s.length > max) throw new HttpError(400, "Too long.");
  return s || null;
}

/** Leave a change for VME. `by` is an email, or "phone". */
export async function queueScheduleEdit(env: Env, team: string, e: ScheduleEditIn, by: string): Promise<number> {
  const action = e.action;
  if (action !== "add" && action !== "update" && action !== "delete") throw new HttpError(400, "Unknown action.");
  const key = text(e.key, 300);
  const tournament = text(e.tournament, 120);
  const date = text(e.date, 10);
  const time = text(e.time, 5);
  const opponent = text(e.opponent, 120);
  if (action !== "add" && (!key || !KEY.test(key))) throw new HttpError(400, "Which match?");
  if (action === "add" && (!tournament || !opponent || !date)) {
    throw new HttpError(400, "A new match needs a tournament, a date and an opponent.");
  }
  if (date && !DATE.test(date)) throw new HttpError(400, "Date must be YYYY-MM-DD.");
  if (time && !TIME.test(time)) throw new HttpError(400, "Time must be HH:MM.");
  const row = await env.DB.prepare(
    `INSERT INTO schedule_edits (team, action, key, tournament, date, time, opponent, submitted_by, submitted_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id`
  )
    .bind(team, action, key, tournament, date, time, opponent, by, Date.now())
    .first<{ id: number }>();
  return row!.id;
}

/** Changes waiting for VME: one team's, or (for VME and the phone) all. */
export async function pendingSchedule(env: Env, team?: string): Promise<(ScheduleChange & { team: string })[]> {
  const q = team
    ? env.DB.prepare("SELECT * FROM schedule_edits WHERE team = ? ORDER BY id").bind(team)
    : env.DB.prepare("SELECT * FROM schedule_edits ORDER BY id");
  return (await q.all<ScheduleChange & { team: string }>()).results;
}

export async function ackSchedule(env: Env, ids: unknown[]): Promise<void> {
  const clean = ids.map(Number).filter(Number.isInteger);
  if (!clean.length) return;
  await env.DB.batch(clean.map((id) => env.DB.prepare("DELETE FROM schedule_edits WHERE id = ?").bind(id)));
}

export interface LiveIn {
  team?: string;
  /** The phone may send its score with a heartbeat too (see ScoreIn). */
  score?: unknown;
  date?: string | null;
  id?: string;
  match_key?: string | null;
  opponent?: string;
  title?: string;
  url?: string;
  live?: boolean;
}

/** The phone saying a stream started, is still going, or stopped. */
export async function reportLive(env: Env, b: LiveIn): Promise<void> {
  const team = text(b.team, 120);
  const id = text(b.id, 200);
  if (!team || !id) throw new HttpError(400, "Which team and match?");
  const now = Date.now();
  if (!b.live) {
    await env.DB.prepare("UPDATE live SET ended_at = ?, updated_at = ? WHERE team = ? AND id = ?")
      .bind(now, now, team, id)
      .run();
    return;
  }
  const url = text(b.url, 500);
  // Only a YouTube watch link: it is put on a page for families to tap.
  if (!url || !/^https:\/\/(www\.)?(youtube\.com|youtu\.be)\//.test(url)) throw new HttpError(400, "A YouTube link, please.");
  const key = text(b.match_key, 300);
  await env.DB.prepare(
    `INSERT INTO live (team, id, match_key, opponent, title, url, started_at, updated_at, ended_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
     ON CONFLICT (team, id) DO UPDATE SET
       match_key = excluded.match_key, opponent = excluded.opponent, title = excluded.title,
       url = excluded.url, updated_at = excluded.updated_at, ended_at = NULL,
       -- A stream that had stopped and starts again is a new start.
       started_at = CASE WHEN live.ended_at IS NULL AND live.updated_at > ? THEN live.started_at ELSE excluded.started_at END`
  )
    .bind(team, id, key && KEY.test(key) ? key : null, text(b.opponent, 120) ?? "", text(b.title, 200) ?? "", url, now, now, now - STALE_MS)
    .run();
}

/** Streams on now: one team's, or every team's. */
export async function liveNow(env: Env, team?: string): Promise<LiveStream[]> {
  const since = Date.now() - STALE_MS;
  const sql = `SELECT team, id, match_key, opponent, title, url, started_at FROM live
               WHERE ended_at IS NULL AND updated_at > ?${team ? " AND team = ?" : ""} ORDER BY started_at`;
  const q = team ? env.DB.prepare(sql).bind(since, team) : env.DB.prepare(sql).bind(since);
  return (await q.all<LiveStream>()).results;
}

export interface PhoneScore {
  home_sets: number;
  away_sets: number;
  sets: [number, number][];
  home: number;
  away: number;
  ended: boolean;
}

export interface ScoreIn {
  team?: string;
  id?: string;
  match_key?: string | null;
  opponent?: string;
  date?: string | null;
  score?: unknown;
}

const n = (v: unknown) => (Number.isInteger(v) && (v as number) >= 0 && (v as number) < 100 ? (v as number) : null);

function cleanScore(s: unknown): PhoneScore | null {
  if (!s || typeof s !== "object") return null;
  const o = s as Record<string, unknown>;
  const sets = Array.isArray(o.sets) ? o.sets : [];
  if (sets.length > 7) return null;
  const pairs: [number, number][] = [];
  for (const p of sets) {
    if (!Array.isArray(p) || p.length !== 2 || n(p[0]) === null || n(p[1]) === null) return null;
    pairs.push([p[0], p[1]]);
  }
  const hs = n(o.home_sets), as = n(o.away_sets), h = n(o.home ?? 0), a = n(o.away ?? 0);
  if (hs === null || as === null || h === null || a === null) return null;
  return { home_sets: hs, away_sets: as, sets: pairs, home: h, away: a, ended: o.ended === true };
}

/** The phone's latest score for one of its matches. */
export async function reportScore(env: Env, b: ScoreIn): Promise<void> {
  const team = text(b.team, 120);
  const id = text(b.id, 200);
  if (!team || !id) throw new HttpError(400, "Which team and match?");
  const score = cleanScore(b.score);
  if (!score) throw new HttpError(400, "Bad score.");
  const key = text(b.match_key, 300);
  const date = text(b.date, 10);
  await env.DB.prepare(
    `INSERT OR REPLACE INTO phone_scores (team, id, match_key, opponent, date, score, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)`
  )
    .bind(team, id, key && KEY.test(key) ? key : null, text(b.opponent, 120) ?? "", date && DATE.test(date) ? date : null, JSON.stringify(score), Date.now())
    .run();
}

/** Match key -> the phone's latest score for it (matches the phone knows VME's key for). */
export async function phoneScores(env: Env, team: string): Promise<Map<string, PhoneScore>> {
  const rows = await env.DB.prepare(
    "SELECT match_key, score FROM phone_scores WHERE team = ? AND match_key IS NOT NULL ORDER BY updated_at"
  )
    .bind(team)
    .all<{ match_key: string; score: string }>();
  return new Map(rows.results.map((r) => [r.match_key, JSON.parse(r.score) as PhoneScore]));
}

/** Match key -> the YouTube link of its latest stream, live or over. */
export async function streamLinks(env: Env, team: string): Promise<Map<string, string>> {
  const rows = await env.DB.prepare(
    "SELECT match_key, url FROM live WHERE team = ? AND match_key IS NOT NULL ORDER BY started_at"
  )
    .bind(team)
    .all<{ match_key: string; url: string }>();
  return new Map(rows.results.map((r) => [r.match_key, r.url]));
}
