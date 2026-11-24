export interface Env {
  DB: D1Database;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  /** Secret shared with VME's publisher (`wrangler pages secret put`). */
  PUBLISH_TOKEN?: string;
  /** "1" in .dev.vars for local development without Access. */
  DEV_NO_AUTH?: string;
}
