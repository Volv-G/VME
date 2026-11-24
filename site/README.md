# VME site

The page families use to watch matches and highlights and follow scores
and stats. Hosted on Cloudflare's free plan at https://courtside-vb.pages.dev:

- **Pages** serves the React app (`src/`) and the API (`functions/api/`,
  with its logic in `server/`).
- **D1** (`vme-site`) holds what VME publishes - documents
  (`shared/docs.ts`) and small images - plus who may see what. See
  `schema.sql`.
- **Access** puts sign-in in front of everything. The API verifies the
  Access token itself (`functions/api/_middleware.ts`), so if Access is
  missing or misconfigured it refuses to serve data rather than leaking it.

VME is the source of truth. It pushes a full copy with
`backend/app/publish/site.py` - from the team page's "Publish to site"
button, and by itself a minute after YouTube uploads finish - and once a
minute fetches join requests and player edits (`site_sync.py`). Its URL
and secret live in `<VME data dir>/site.json`, outside the repo. Nothing
on the internet calls into VME.

## Who sees what

Anyone can sign in; signing in proves an email and opens nothing. People
get in through the invite link (`/join/<code>`, shown on `/admin`): they
pick a team and claim a player (a parent, or the player) or the coach's
place, and wait for the admin to approve - on `/admin`, or from the 🔔 in
VME's header.

| | Team videos, stats | Reels | Edit a player |
|---|---|---|---|
| Admin (`ADMIN_EMAILS`) | every team | all | any |
| Coach | their team | all of the team's | - |
| Player / parent | their team | the claimed player's | the claimed player: name, photo |

Rules live in `server/access.ts`; documents are trimmed per person in
`server/content.ts`. Player edits wait in `player_edits` until VME
applies them to its roster and republishes.

## Deploying

```
cd site
npm install
npx wrangler login            # once
npm run db:schema             # after schema.sql changes (idempotent)
npm run deploy                # build + wrangler pages deploy
```

Set `CLOUDFLARE_ACCOUNT_ID` if wrangler asks which account to use.

## Configuration

- `wrangler.toml` `[vars]`: `ACCESS_TEAM_DOMAIN` (https://<team>.cloudflareaccess.com)
  and `ACCESS_AUD` (the Access application's audience tag). Until both are
  set, every API read answers 503.
- Secret `PUBLISH_TOKEN` (`wrangler pages secret put PUBLISH_TOKEN`): the
  same value as `token` in VME's `site.json`.
- Secret `ADMIN_EMAILS`: comma-separated emails with the admin role.
- Access: the site's application allows **Everyone** (any signed-in
  email) - the site decides the rest. A second, **Bypass** application
  covers `courtside-vb.pages.dev/api/publish` (and everything under it)
  so VME can publish and sync; those paths accept nothing but the token.

## Local development

```
npx wrangler d1 execute vme-site --local --file=schema.sql   # once
printf 'DEV_NO_AUTH=1\nPUBLISH_TOKEN=local\nADMIN_EMAILS=admin@test\n' > .dev.vars
npm run build && npx wrangler pages dev dist
```

`DEV_NO_AUTH` skips the Access check (you are `DEV_EMAIL`, or whoever an
`X-Dev-Email` header names); it only ever lives in `.dev.vars`, which is
not deployed or committed.
