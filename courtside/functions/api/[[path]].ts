/**
 * The site's API. People reach it only through `_middleware.ts`, which
 * has verified their sign-in and put their email in `ctx.data.email`;
 * what each route then shows depends on who that is (server/access.ts).
 *
 *   GET  /api/me                        who you are, your memberships
 *   GET  /api/doc/<key>                 a published document, trimmed for you
 *   GET  /api/image/<key>               a photo or logo
 *   GET  /api/join/<code>               the invite's team and players
 *   POST /api/join/<code>               ask to be that team's player or coach
 *   GET  /api/invite/<team>             the team's invite link (its members and the admin)
 *   GET  /api/video/<key>               a reel's web copy or thumbnail (Range supported)
 *   GET  /api/reveals                   scores/stats you chose to see (no spoilers)
 *   POST /api/reveals                   {ids} more of them
 *   GET  /api/player/<team>/<n>         a player's details, for editing
 *   POST /api/player/<team>/<n>         change a player's name or photo
 *   GET  /api/admin                     requests, members, each team's invite link (admin)
 *   POST /api/admin/invite              a team's new invite link; the old one stops working
 *   POST /api/admin/members/<id>        approve / decline / remove
 *
 * And for VME, with its bearer token instead of a sign-in:
 *
 *   POST /api/publish                   replace what is published
 *   POST /api/publish/sync              decisions and acks in; requests and edits out
 *   GET  /api/publish/videos?prefix=    the reel copies stored (server/videos.ts)
 *   PUT  /api/publish/video/<key>       store a reel copy or thumbnail (body: the MP4 / JPEG)
 *   DELETE /api/publish/video/<key>     drop a reel copy
 */
import type { Env } from "../../shared/env";
import type { Decision, Me } from "../../shared/api";
import { canSeeTeam, loadViewer, type Viewer } from "../../server/access";
import { canSeeImage, Names, readDoc } from "../../server/content";
import { blobBytes, fromBase64, HttpError, json, readBody, sameSecret } from "../../server/http";
import { adminState, decide, inviteCode, joinTeam, pendingRequests, requestJoin, type JoinRequest } from "../../server/members";
import { ackEdits, editPlayer, pendingEdits, playerInfo, type PlayerEdit } from "../../server/players";
import { deleteVideo, listVideos, putVideo, serveVideo } from "../../server/videos";
import { addReveals, listReveals } from "../../server/reveals";

interface PublishBody {
  /** Keys whose prefix is listed here are deleted before the docs below
   *  are written, in the same transaction - so a match removed in VME
   *  disappears from the site instead of lingering. */
  replace_prefixes?: string[];
  docs?: { key: string; json: unknown }[];
  images?: { key: string; mime: string; base64: string }[];
}

interface SyncBody {
  /** Player edits VME has applied to its roster. */
  ack?: number[];
  /** Requests decided in VME. */
  decide?: { id: number; action: Decision }[];
}

function checkToken(request: Request, env: Env): void {
  const auth = request.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!env.PUBLISH_TOKEN || !sameSecret(token, env.PUBLISH_TOKEN)) {
    throw new HttpError(403, "Not allowed to publish.");
  }
}

async function publish(request: Request, env: Env): Promise<Response> {
  const body = await readBody<PublishBody>(request);
  const now = Date.now();
  const stmts: D1PreparedStatement[] = [];
  for (const prefix of body.replace_prefixes ?? []) {
    stmts.push(env.DB.prepare("DELETE FROM docs WHERE substr(key, 1, ?) = ?").bind(prefix.length, prefix));
    stmts.push(env.DB.prepare("DELETE FROM images WHERE substr(key, 1, ?) = ?").bind(prefix.length, prefix));
  }
  for (const d of body.docs ?? []) {
    stmts.push(
      env.DB.prepare("INSERT OR REPLACE INTO docs (key, json, updated_at) VALUES (?, ?, ?)")
        .bind(d.key, JSON.stringify(d.json), now)
    );
  }
  for (const img of body.images ?? []) {
    stmts.push(
      env.DB.prepare("INSERT OR REPLACE INTO images (key, mime, data, updated_at) VALUES (?, ?, ?, ?)")
        .bind(img.key, img.mime, fromBase64(img.base64), now)
    );
  }
  if (stmts.length) await env.DB.batch(stmts);
  return json({ ok: true, docs: body.docs?.length ?? 0, images: body.images?.length ?? 0 });
}

async function sync(request: Request, env: Env): Promise<Response> {
  const body = await readBody<SyncBody>(request);
  await ackEdits(env, body.ack ?? []);
  for (const d of body.decide ?? []) await decide(env, Number(d.id), d.action, "VME");
  return json({ requests: await pendingRequests(env), edits: await pendingEdits(env) });
}

function requireAdmin(v: Viewer): void {
  if (!v.admin) throw new HttpError(403, "Admins only.");
}

async function route(ctx: EventContext<Env, string, Record<string, unknown>>): Promise<Response> {
  const { request, env } = ctx;
  const url = new URL(request.url);
  const path = decodeURIComponent(url.pathname.replace(/^\/api\/?/, ""));
  const method = request.method;

  if (path === "publish" || path.startsWith("publish/")) {
    checkToken(request, env);
    if (path === "publish" && method === "POST") return publish(request, env);
    if (path === "publish/sync" && method === "POST") return sync(request, env);
    if (path === "publish/videos" && method === "GET") return listVideos(env, url.searchParams.get("prefix") || "reels/");
    if (path.startsWith("publish/video/")) {
      const key = path.slice("publish/video/".length);
      if (method === "PUT") return putVideo(env, key, request);
      if (method === "DELETE") return deleteVideo(env, key);
    }
    throw new HttpError(404, "Not found");
  }

  const v = await loadViewer(env, String(ctx.data.email || ""));
  const parts = path.split("/");

  if (path === "me" && method === "GET") {
    const pending = v.admin
      ? ((await env.DB.prepare("SELECT count(*) AS n FROM members WHERE status = 'pending'").first<{ n: number }>())?.n ?? 0)
      : 0;
    const me: Me = {
      email: v.email,
      admin: v.admin,
      memberships: await new Names(env).describeAll(v.memberships),
      pending,
    };
    return json(me);
  }

  if (parts[0] === "doc" && method === "GET") {
    return new Response(JSON.stringify(await readDoc(env, v, parts.slice(1).join("/"))), {
      headers: { "content-type": "application/json", "cache-control": "private, no-cache" },
    });
  }

  if (parts[0] === "image" && method === "GET") {
    const key = parts.slice(1).join("/");
    if (!canSeeImage(v, key)) throw new HttpError(403, "Not allowed.");
    const row = await env.DB.prepare("SELECT mime, data FROM images WHERE key = ?")
      .bind(key)
      .first<{ mime: string; data: unknown }>();
    if (!row) return new Response("Not found", { status: 404 });
    return new Response(blobBytes(row.data), {
      headers: { "content-type": row.mime, "cache-control": "private, max-age=3600" },
    });
  }

  if (parts[0] === "video" && (method === "GET" || method === "HEAD")) {
    return serveVideo(env, v, parts.slice(1).join("/"), request);
  }

  if (path === "reveals") {
    if (method === "GET") return json({ ids: await listReveals(env, v) });
    if (method === "POST") {
      await addReveals(env, v, (await readBody<{ ids: unknown }>(request)).ids);
      return json({ ok: true });
    }
  }

  if (parts[0] === "invite" && parts.length === 2 && method === "GET") {
    // A team's own people can pass its invite link on; it still takes
    // the admin's approval to get in.
    if (!canSeeTeam(v, parts[1])) throw new HttpError(403, "You don't have access to this team.");
    if (!(await new Names(env).team(parts[1]))) throw new HttpError(404, "No such team.");
    return json({ code: await inviteCode(env, parts[1]) });
  }

  if (parts[0] === "join" && parts.length === 2) {
    if (method === "GET") return json(await joinTeam(env, parts[1]));
    if (method === "POST") return json(await requestJoin(env, v, parts[1], await readBody<JoinRequest>(request)));
  }

  if (parts[0] === "player" && parts.length === 3) {
    const number = Number(parts[2]);
    if (!Number.isInteger(number)) throw new HttpError(404, "Not found");
    if (method === "GET") return json(await playerInfo(env, v, parts[1], number));
    if (method === "POST") {
      await editPlayer(env, v, parts[1], number, await readBody<PlayerEdit>(request));
      return json({ ok: true });
    }
  }

  if (parts[0] === "admin") {
    requireAdmin(v);
    if (path === "admin" && method === "GET") return json(await adminState(env));
    if (path === "admin/invite" && method === "POST") {
      const { team } = await readBody<{ team: string }>(request);
      if (!(await new Names(env).team(String(team || "")))) throw new HttpError(400, "No such team.");
      return json({ code: await inviteCode(env, team, true) });
    }
    if (parts[1] === "members" && parts.length === 3 && method === "POST") {
      const { action } = await readBody<{ action: Decision }>(request);
      await decide(env, Number(parts[2]), action, v.email);
      return json({ ok: true });
    }
  }

  throw new HttpError(404, "Not found");
}

export const onRequest: PagesFunction<Env> = async (ctx) => {
  try {
    return await route(ctx);
  } catch (e) {
    if (e instanceof HttpError) return json({ error: e.message }, e.status);
    console.error(e);
    return json({ error: "Something went wrong on the server." }, 500);
  }
};
