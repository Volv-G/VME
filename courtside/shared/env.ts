export interface Env {
  DB: D1Database;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  /** Secret shared with VME's publisher (`wrangler pages secret put`). */
  PUBLISH_TOKEN?: string;
  /** Secret: comma-separated emails that may do everything. */
  ADMIN_EMAILS?: string;
  /** "1" in .dev.vars for local development without Access. */
  DEV_NO_AUTH?: string;
  /** Who you are under DEV_NO_AUTH (default dev@local). */
  DEV_EMAIL?: string;
}
