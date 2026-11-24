/**
 * Every /api request must come through Cloudflare Access.
 *
 * Access sits in front of the whole site and signs each request it lets
 * through (`Cf-Access-Jwt-Assertion`). Checking that signature here as
 * well means the data is never served on the strength of configuration
 * alone: if the Access application is removed, mistyped or not set up
 * yet, the API answers 401/503 rather than handing children's names and
 * stats to whoever asks.
 */
import { createRemoteJWKSet, jwtVerify } from "jose";
import type { Env } from "../../shared/env";

const keySets = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

export const onRequest: PagesFunction<Env> = async (ctx) => {
  const { env, request } = ctx;
  // VME's publisher is a program, not a person: Access lets this one
  // path through (a Bypass application), and the handler admits only the
  // shared secret. Nothing is READ here, so a bypass exposes no data.
  if (request.method === "POST" && new URL(request.url).pathname === "/api/publish") {
    return ctx.next();
  }
  // Local `wrangler pages dev` only, set in .dev.vars (never deployed).
  if (env.DEV_NO_AUTH === "1") {
    ctx.data.identity = "dev";
    return ctx.next();
  }
  const domain = (env.ACCESS_TEAM_DOMAIN || "").replace(/\/+$/, "");
  if (!domain || !env.ACCESS_AUD) {
    return json({ error: "Sign-in is not configured yet." }, 503);
  }
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return json({ error: "Not signed in." }, 401);

  let keys = keySets.get(domain);
  if (!keys) {
    keys = createRemoteJWKSet(new URL(`${domain}/cdn-cgi/access/certs`));
    keySets.set(domain, keys);
  }
  try {
    const { payload } = await jwtVerify(token, keys, {
      issuer: domain,
      audience: env.ACCESS_AUD,
    });
    // A person has an email; VME's publisher signs in with a service
    // token, which carries its name instead.
    ctx.data.identity = String(payload.email || payload.common_name || "");
  } catch {
    return json({ error: "Sign-in could not be verified." }, 401);
  }
  return ctx.next();
};
