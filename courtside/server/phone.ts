/**
 * The streaming phone's corner of the API, under /api/publish/phone/
 * (outside Cloudflare Access, like VME's publish routes) with its own
 * bearer token. VME makes the token, hands it to the phone and publishes
 * only its SHA-256 here (settings `phone_token_sha256`), so the site
 * never holds the token itself. It can read the schedule, leave changes
 * to it and say a stream is live - nothing else.
 *
 *   GET  /api/publish/phone/schedule   every team's upcoming matches, pending changes, live streams
 *   POST /api/publish/phone/schedule   {team, action, key?, tournament?, date?, time?, opponent?}
 *   POST /api/publish/phone/live       {team, id, match_key?, opponent, title, url, live, score?}
 *   POST /api/publish/phone/score      {team, id, match_key?, opponent, date, score}
 */
import type { Env } from "../shared/env";
import type { TeamDoc, TeamsDoc } from "../shared/docs";
import { getDoc } from "./content";
import { HttpError, json, readBody, sameSecret } from "./http";
import { liveNow, pendingSchedule, queueScheduleEdit, reportLive, reportScore, type LiveIn, type ScheduleEditIn, type ScoreIn } from "./schedule";

async function sha256Hex(s: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function checkPhoneToken(request: Request, env: Env): Promise<void> {
  const auth = request.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  const row = await env.DB.prepare("SELECT value FROM settings WHERE key = 'phone_token_sha256'").first<{ value: string }>();
  if (!token || !row || !sameSecret(await sha256Hex(token), row.value)) {
    throw new HttpError(403, "Not allowed.");
  }
}

async function teamSlugs(env: Env): Promise<string[]> {
  return ((await getDoc<TeamsDoc>(env, "teams"))?.teams ?? []).map((t) => t.slug);
}

export async function phoneRoute(path: string, request: Request, env: Env): Promise<Response> {
  await checkPhoneToken(request, env);
  const method = request.method;

  if (path === "publish/phone/schedule" && method === "GET") {
    const pending = await pendingSchedule(env);
    const teams = [];
    for (const slug of await teamSlugs(env)) {
      const doc = await getDoc<TeamDoc>(env, `team/${slug}`);
      if (!doc) continue;
      teams.push({
        team: slug,
        name: doc.name,
        tournaments: doc.tournament_names ?? doc.tournaments.map((t) => ({ slug: t.slug, name: t.name })),
        upcoming: doc.upcoming ?? [],
        pending: pending
          .filter((p) => p.team === slug)
          .map(({ id, action, key, tournament, date, time, opponent, submitted_at }) => ({
            id, action, key, tournament, date, time, opponent, submitted_at,
          })),
      });
    }
    return json({ teams, live: await liveNow(env) });
  }

  if (path === "publish/phone/schedule" && method === "POST") {
    const body = await readBody<ScheduleEditIn & { team?: string }>(request);
    const team = String(body.team || "");
    if (!(await teamSlugs(env)).includes(team)) throw new HttpError(400, "No such team on the site.");
    return json({ ok: true, id: await queueScheduleEdit(env, team, body, "phone") });
  }

  if (path === "publish/phone/live" && method === "POST") {
    const body = await readBody<LiveIn>(request);
    if (!(await teamSlugs(env)).includes(String(body.team || ""))) throw new HttpError(400, "No such team on the site.");
    await reportLive(env, body);
    // A bad score must not undo the live announcement just stored.
    if (body.score) await reportScore(env, body).catch(() => undefined);
    return json({ ok: true });
  }

  if (path === "publish/phone/score" && method === "POST") {
    const body = await readBody<ScoreIn>(request);
    if (!(await teamSlugs(env)).includes(String(body.team || ""))) throw new HttpError(400, "No such team on the site.");
    await reportScore(env, body);
    return json({ ok: true });
  }

  throw new HttpError(404, "Not found");
}
