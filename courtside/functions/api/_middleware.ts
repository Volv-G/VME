/**
 * Every /api request must come through Cloudflare Access.
 *
 * Access sits in front of the whole site and signs each request it lets
 * through (`Cf-Access-Jwt-Assertion`). Checking that signature here as
 * well means the data is never served on the strength of configuration
 * alone: if the Access application is removed, mistyped or not set up
 * yet, the API answers 401/503 rather than handing children's names and
 * stats to whoever asks.
 *
 * Signing in only proves an email. What that email may see is decided
 * per request from its memberships (server/access.ts).
 */
import { createRemoteJWKSet, jwtVerify } from "jose";
import type { Env } from "../../shared/env";
import { json } from "../../server/http";

const keySets = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

export const onRequest: PagesFunction<Env> = async (ctx) => {
  const { env, request } = ctx;
  // VME is a program, not a person: Access lets /api/publish/* through
  // (a Bypass application) and the handlers admit only VME's secret.
  const pathname = new URL(request.url).pathname;
  if (pathname === "/api/publish" || pathname.startsWith("/api/publish/")) {
    return ctx.next();
  }
  // Local `wrangler pages dev` only, set in .dev.vars (never deployed).
  if (env.DEV_NO_AUTH === "1") {
    // `X-Dev-Email` lets a local test act as several people.
    ctx.data.email = (request.headers.get("x-dev-email") || env.DEV_EMAIL || "dev@local").toLowerCase();
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
    const email = String(payload.email || "").toLowerCase();
    if (!email) return json({ error: "Your sign-in has no email address." }, 401);
    ctx.data.email = email;
  } catch {
    return json({ error: "Sign-in could not be verified." }, 401);
  }
  return ctx.next();
};
