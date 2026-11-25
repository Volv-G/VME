/**
 * Web copies of the reels, kept in R2 (binding VIDEOS) so the site can
 * play them in place. VME makes a small H.264 copy of each reel it put on
 * OneDrive and uploads it here; the key says whose reel it is,
 *
 *   reels/<team>/<player number>/<content hash>.mp4
 *
 * so a viewer's right to it is checked with the same rule as the reel's
 * entry in the team doc (server/access.ts reelFilter).
 */
import type { Env } from "../shared/env";
import { canSeeTeam, reelFilter, type Viewer } from "./access";
import { HttpError, json } from "./http";

const KEY = /^reels\/([A-Za-z0-9._-]+)\/(\d{1,3})\/[a-f0-9]{8,64}\.mp4$/;
/** A copy is ~15-25 MB; the request body limit on the free plan is 100 MB. */
const MAX_BYTES = 95 * 1024 * 1024;

function parseKey(key: string): { team: string; number: number } {
  const m = KEY.exec(key);
  if (!m) throw new HttpError(404, "Not found");
  return { team: m[1], number: Number(m[2]) };
}

function bucket(env: Env): R2Bucket {
  if (!env.VIDEOS) throw new HttpError(503, "Video storage is not set up.");
  return env.VIDEOS;
}

/** GET / HEAD a copy for a viewer, honouring Range so players can seek. */
export async function serveVideo(env: Env, v: Viewer, key: string, request: Request): Promise<Response> {
  const { team, number } = parseKey(key);
  if (!canSeeTeam(v, team) || !reelFilter(v, team)(number)) throw new HttpError(403, "Not allowed.");
  const videos = bucket(env);

  const head = await videos.head(key);
  if (!head) return new Response("Not found", { status: 404 });
  const size = head.size;
  const headers = new Headers({
    "content-type": "video/mp4",
    "accept-ranges": "bytes",
    // Private: the copy is only for this viewer's browser, never a shared cache.
    "cache-control": "private, max-age=86400",
    etag: head.httpEtag,
  });
  if (request.method === "HEAD") {
    headers.set("content-length", String(size));
    return new Response(null, { headers });
  }

  const range = parseRange(request.headers.get("range"), size);
  if (range === "invalid") {
    headers.set("content-range", `bytes */${size}`);
    return new Response(null, { status: 416, headers });
  }
  if (!range) {
    const obj = await videos.get(key);
    if (!obj) return new Response("Not found", { status: 404 });
    headers.set("content-length", String(size));
    return new Response(obj.body, { headers });
  }
  const obj = await videos.get(key, { range: { offset: range.start, length: range.end - range.start + 1 } });
  if (!obj) return new Response("Not found", { status: 404 });
  headers.set("content-length", String(range.end - range.start + 1));
  headers.set("content-range", `bytes ${range.start}-${range.end}/${size}`);
  return new Response(obj.body, { status: 206, headers });
}

/** A single `bytes=` range, clamped to the file; null for none. */
function parseRange(header: string | null, size: number): { start: number; end: number } | null | "invalid" {
  if (!header) return null;
  const m = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!m || (m[1] === "" && m[2] === "")) return null;
  let start: number;
  let end: number;
  if (m[1] === "") {
    // Suffix: the last N bytes.
    const n = Number(m[2]);
    start = Math.max(0, size - n);
    end = size - 1;
  } else {
    start = Number(m[1]);
    end = m[2] === "" ? size - 1 : Math.min(Number(m[2]), size - 1);
  }
  if (start >= size || start > end) return "invalid";
  return { start, end };
}

/** VME: store one copy (the request body is the MP4 itself). */
export async function putVideo(env: Env, key: string, request: Request): Promise<Response> {
  parseKey(key);
  const length = Number(request.headers.get("content-length") || 0);
  if (!length) throw new HttpError(411, "Send the file with its Content-Length.");
  if (length > MAX_BYTES) throw new HttpError(413, "That copy is too big.");
  if (!request.body) throw new HttpError(400, "No file.");
  await bucket(env).put(key, request.body, { httpMetadata: { contentType: "video/mp4" } });
  return json({ ok: true, key, size: length });
}

export async function deleteVideo(env: Env, key: string): Promise<Response> {
  parseKey(key);
  await bucket(env).delete(key);
  return json({ ok: true });
}

/** VME: what's stored under a prefix, so it uploads only what's missing. */
export async function listVideos(env: Env, prefix: string): Promise<Response> {
  if (!prefix.startsWith("reels/")) throw new HttpError(400, "Prefix must start with reels/.");
  const videos = bucket(env);
  const out: { key: string; size: number }[] = [];
  let cursor: string | undefined;
  do {
    const page = await videos.list({ prefix, cursor, limit: 1000 });
    for (const o of page.objects) out.push({ key: o.key, size: o.size });
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return json({ videos: out, total_bytes: out.reduce((s, o) => s + o.size, 0) });
}
