# VME site

The page families use to watch matches and highlights and follow scores
and stats. Hosted on Cloudflare's free plan at https://vme-site.pages.dev:

- **Pages** serves the React app (`src/`) and the API (`functions/api/`).
- **D1** (`vme-site`) holds what VME publishes: documents (`shared/docs.ts`)
  and small images. See `schema.sql`.
- **Access** puts sign-in in front of everything. The API verifies the
  Access token itself (`functions/api/_middleware.ts`), so if Access is
  missing or misconfigured it refuses to serve data rather than leaking it.

VME is the source of truth. It pushes a full copy with
`backend/app/publish/site.py` - from the team page's "Publish to site"
button, and by itself a minute after YouTube uploads finish. Its URL and
secret live in `<VME data dir>/site.json`, outside the repo. Nothing on
the internet calls into VME.

## Deploying

```
cd site
npm install
npx wrangler login            # once
npm run deploy                # build + wrangler pages deploy
```

Set `CLOUDFLARE_ACCOUNT_ID` if wrangler asks which account to use.

## Configuration

- `wrangler.toml` `[vars]`: `ACCESS_TEAM_DOMAIN` (https://<team>.cloudflareaccess.com)
  and `ACCESS_AUD` (the Access application's audience tag). Until both are
  set, every API read answers 503.
- Secret `PUBLISH_TOKEN` (`wrangler pages secret put PUBLISH_TOKEN`): the
  same value as `token` in VME's `site.json`.
- Access needs a **Bypass** application for `vme-site.pages.dev/api/publish`
  so VME can publish; that path accepts nothing but the token and serves no data.

## Local development

```
npm run db:schema -- --local   # once (drop --remote from the script for local)
printf 'DEV_NO_AUTH=1\nPUBLISH_TOKEN=local\n' > .dev.vars
npm run build && npx wrangler pages dev dist
```

`DEV_NO_AUTH` skips the Access check; it only ever lives in `.dev.vars`,
which is not deployed or committed.
