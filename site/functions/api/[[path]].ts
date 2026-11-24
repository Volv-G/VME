/**
 * The site's API. Reached only after `_middleware.ts` has verified the
 * caller's sign-in.
 *
 *   GET  /api/me              who is signed in
 *   GET  /api/doc/<key>       a published document (see schema.sql)
 *   GET  /api/image/<key>     a published photo or logo
 *   POST /api/publish         VME replacing what is published (bearer token)
 */
import type { Env } from "../../shared/env";

interface PublishBody {
  /** Keys whose prefix is listed here are deleted before the docs below
   *  are written, in the same transaction - so a match removed in VME
   *  disappears from the site instead of lingering. */
  replace_prefixes?: string[];
  docs?: { key: string; json: unknown }[];
  images?: { key: string; mime: string; base64: string }[];
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

function fromBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Constant-time comparison, so the token can't be guessed byte by byte. */
function sameSecret(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function publish(request: Request, env: Env): Promise<Response> {
  const auth = request.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!env.PUBLISH_TOKEN || !token || !sameSecret(token, env.PUBLISH_TOKEN)) {
    return json({ error: "Not allowed to publish." }, 403);
  }
  const body = (await request.json()) as PublishBody;
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

export const onRequest: PagesFunction<Env> = async (ctx) => {
  const { request, env } = ctx;
  const url = new URL(request.url);
  const path = decodeURIComponent(url.pathname.replace(/^\/api\/?/, ""));

  if (path === "publish" && request.method === "POST") return publish(request, env);
  if (request.method !== "GET") return json({ error: "Method not allowed" }, 405);

  if (path === "me") return json({ identity: ctx.data.identity ?? "" });

  if (path.startsWith("doc/")) {
    const row = await env.DB.prepare("SELECT json FROM docs WHERE key = ?")
      .bind(path.slice(4))
      .first<{ json: string }>();
    if (!row) return json({ error: "Not found" }, 404);
    return new Response(row.json, {
      headers: { "content-type": "application/json", "cache-control": "private, no-cache" },
    });
  }

  if (path.startsWith("image/")) {
    const row = await env.DB.prepare("SELECT mime, data FROM images WHERE key = ?")
      .bind(path.slice(6))
      .first<{ mime: string; data: number[] | ArrayBuffer }>();
    if (!row) return new Response("Not found", { status: 404 });
    // D1 hands a BLOB back as an array of byte values, not as bytes.
    return new Response(new Uint8Array(row.data as ArrayLike<number>), {
      headers: { "content-type": row.mime, "cache-control": "private, max-age=3600" },
    });
  }

  return json({ error: "Not found" }, 404);
};
