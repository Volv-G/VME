# Match Editor (VME)

Web-based volleyball match editor: upload source video, mark events on a
timeline, render a final video (full match, per-player highlight reels, or
focused-player cuts) with scoreboard + popup overlays, and optionally
upload the result to YouTube.

## Stack

- Backend: Python 3.11 + FastAPI + MoviePy + Pillow + ffmpeg
- Frontend: React + Vite + TypeScript
- Storage: filesystem (`<data-dir>/media/<Team>/<Tournament>/<Date>/<Match>/...`)
  with JSON sidecars
- Auth: HTTP Basic (single shared user)
- Production server: NSSM-wrapped Windows service, either behind IIS or
  binding 80/443 directly

## First-time setup

```powershell
# 1. Install Node.js (one-time)
.\scripts\install-node.ps1

# 2. Install backend deps (one-time). uv reads backend/pyproject.toml.
cd backend
uv sync
cd ..

# 3. Install frontend deps (one-time)
cd frontend
npm install
cd ..
```

External tools that must be on the system somewhere (not bundled):

- **ffmpeg + ffprobe** — used for probing uploads (fps, frame count,
  recording time) and for transcoding to the project fps when sources
  mix rates. The backend autodetects them in common install roots
  (`C:\Programs\ffmpeg-*`, `C:\Program Files\ffmpeg`, scoop, choco). If
  they live somewhere unusual, set `VME_FFMPEG` / `VME_FFPROBE` in
  `secrets.env`.
- **Optional: Google OAuth client** if you want to upload renders to
  YouTube. See [YouTube upload setup](#youtube-upload-setup) below.

## Development

```powershell
# Run backend (:8001) and frontend (:5173) together
.\scripts\dev.ps1
```

Open <http://localhost:5173>. Vite proxies `/api/*` to the backend.

The dev backend reads/writes the same data dir as the service if you set
`VME_DATA_DIR` (recommended) — otherwise it'll pick up project-root
defaults and you'll have two independent copies of state.

## Production deployment (Windows service)

Pick whichever mode fits your machine.

### Mode A: behind IIS as an Application at `/vme` (recommended)

IIS terminates TLS on :443 and reverse-proxies `https://<public-host>/vme/*`
to a Python service on `127.0.0.1:8000`. The app lives as an IIS
Application under `Default Web Site` (or whichever parent you specify),
so it coexists with anything else IIS is hosting.

```powershell
# 1. Build the frontend (Vite is configured with base=/vme/ for prod)
.\scripts\build.ps1

# 2. Set Basic Auth credentials
.\scripts\set-credentials.ps1

# 3. Configure IIS (installs URL Rewrite + ARR if needed, creates the
#    Application at /vme, binds your cert via SNI, disables ARR response
#    buffering for SSE, raises uploadReadAheadSize for large bodies).
#    Run as Administrator.
.\scripts\setup-iis.ps1 -PublicHost satis2.duckdns.org
#    Optional overrides:
#      -Thumbprint A1B2C3...   # explicit cert in LocalMachine\My
#      -ParentSite "MySite"    # default: "Default Web Site"
#      -UrlPrefix /matcheditor # default: /vme

# 4. Install the backend as a Windows service.
.\scripts\install-service.ps1
```

Open `https://satis2.duckdns.org/vme/`. The cert validates cleanly
because IIS terminates TLS using the cert in the Windows store.

If you change the URL prefix later you must rerun **both**
`setup-iis.ps1` (with the new `-UrlPrefix`) and `build.ps1` (after
editing `BASE_PATH` in `frontend/vite.config.ts`) — the prefix is baked
into the static asset URLs.

### Mode B: standalone (no IIS)

Hypercorn binds :80 (HTTP→HTTPS redirect) and :443 (TLS) directly.

```powershell
# 1. Build the frontend (edit BASE_PATH in vite.config.ts to "/" first)
.\scripts\build.ps1

# 2. Point the server at a TLS cert. Pick whichever matches what you have:
.\scripts\set-cert.ps1 -PublicHost satis2.duckdns.org -FromStore
#   or by thumbprint:
.\scripts\set-cert.ps1 -PublicHost satis2.duckdns.org -Thumbprint A1B2C3...
#   or from PEM files:
.\scripts\set-cert.ps1 -PublicHost satis2.duckdns.org `
    -CertFile C:\path\to\fullchain.pem -KeyFile C:\path\to\privkey.pem
#   or from a PFX bundle:
.\scripts\set-cert.ps1 -PublicHost satis2.duckdns.org -PfxFile C:\path\to\bundle.pfx
#   or self-signed (LAN only):
.\scripts\generate-cert.ps1

# 3. Credentials
.\scripts\set-credentials.ps1

# 4. Install service (opens firewall :80/:443, downloads NSSM)
.\scripts\install-service.ps1
```

### Common operations

- **Change the password** — re-run `set-credentials.ps1` (re-install the
  service afterwards, or set the new hash directly in `secrets.env`).
- **Restart the service** — `Restart-Service MatchEditor` from elevated
  PowerShell.
- **Remove the service** — `scripts/uninstall-service.ps1`. Mode A leaves
  the IIS site in place; remove that from IIS Manager separately if you
  no longer want it.
- **Tail logs** — `Get-Content <data-dir>\logs\service.log -Tail 50 -Wait`
  (path depends on `VME_DATA_DIR`; defaults to project-root).

### Data directory

By default the service writes media, logs, and the job queue to
subdirectories of the project root. To keep data outside the checkout
(strongly recommended in production), set `VME_DATA_DIR` in
`secrets.env`:

```
VME_DATA_DIR=C:\Users\vladi\VME
```

The service then uses:

```
C:\Users\vladi\VME\
  media\              # team/tournament/date/match tree
  logs\               # service.log + render logs
  jobs.json           # persistent render-queue state
  youtube_client_secret.json   # only if you set up YouTube upload
  youtube_token.json
```

After editing `secrets.env`, rerun `install-service.ps1` so the new
env var actually lands in the service process (NSSM only reads the
allow-listed env vars at install time).

The install script prints a `Data dir : ...` line near the top of its
output so misconfiguration is caught immediately rather than weeks later
when files don't show up where you expect.

### `secrets.env` reference

Gitignored, lives at project root. Managed mostly by helper scripts;
each key has a single purpose:

| Key | Purpose |
|---|---|
| `VME_USERNAME` / `VME_PASSWORD_HASH` | HTTP Basic Auth credentials. Set by `set-credentials.ps1`. |
| `VME_AUTH_REQUIRED` | `1` to enforce auth; unset/`0` only for trusted local dev. |
| `VME_DATA_DIR` | Root of media/logs/cred storage. Recommended. |
| `VME_MEDIA_ROOT`, `VME_LOG_ROOT`, `VME_CERT_DIR` | Per-subtree overrides (rarely needed; `VME_DATA_DIR` covers most cases). |
| `VME_BEHIND_PROXY` | `1` when behind IIS; set by `setup-iis.ps1`. |
| `VME_BACKEND_HOST`, `VME_BACKEND_PORT` | Where the proxy expects to find the backend (default `127.0.0.1:8000`). |
| `VME_URL_PREFIX` | URL path prefix when behind IIS (default `/vme`). |
| `VME_CERT_FILE`, `VME_KEY_FILE`, `VME_PUBLIC_HOST` | TLS settings for standalone mode. Set by `set-cert.ps1`. |
| `VME_HTTP_PORT`, `VME_HTTPS_PORT`, `VME_BIND_HOST` | Standalone bind overrides (default 80/443/all). |
| `VME_FFMPEG`, `VME_FFPROBE` | Explicit paths to the binaries (default: autodetect on PATH + common install roots). |

### IIS config

`iis/web.config` is deployed to `C:\ProgramData\VME\iis\web.config` by
`setup-iis.ps1`. Key knobs:

- `maxAllowedContentLength = 4 GiB - 1` so IIS request filtering doesn't
  reject large clip uploads.
- `text/event-stream` excluded from compression (otherwise SSE buffering
  breaks the render-progress stream).
- Server-side `uploadReadAheadSize = 32 MiB` (set in
  `applicationHost.config` by `setup-iis.ps1`, not in `web.config`,
  because that section is locked at the parent level).

Edits survive `setup-iis.ps1` reruns.

## YouTube upload setup

Optional. Enables a "Upload to YouTube" button on each completed full
render. Per-team config (privacy, default playlist, title/description
templates) lives in the team's `roster.json`.

```powershell
# 1. Create a Desktop OAuth client in Google Cloud Console:
#    - Enable YouTube Data API v3
#    - OAuth consent screen: External + Testing mode, add yourself as Test User
#    - Create credentials -> OAuth client ID -> Desktop app
#    - Download the JSON

# 2. Run the authorize script. Pass the downloaded JSON; the script
#    copies it into the data dir and runs the one-time browser auth.
.\scripts\yt-authorize.ps1 ~\Downloads\client_secret_*.json

# Verify
.\backend\.venv\Scripts\python.exe -c "from app.upload.youtube import get_status; print(get_status())"
```

After auth, set the per-team YouTube config in the Team dashboard's
"YouTube" card (privacy default, optional default playlist ID, title /
description templates with `{date}` `{opponent}` `{tournament_abbr}`
`{tournament_full}` `{match_index}` placeholders).

Quota: 10,000 units/day, 1,600 per upload → roughly 6 uploads/day on the
default quota.

## Render output naming

Each team can override the path templates used by the three render
flavors. Set them in the Team dashboard's "Render output naming" card
(defaults reproduce the legacy paths byte-for-byte). Templates resolve
at enqueue time, so changing them mid-run does NOT retroactively rewrite
queued jobs.

| Template | Default | Variables |
|---|---|---|
| Full render | `{label}_{timestamp}.mp4` | `{date}` `{opponent}` `{team}` `{tournament}` `{tournament_abbr}` `{tournament_full}` `{match_index}` `{label}` `{timestamp}` |
| Highlight | `highlights/{team}/{player}/{action}/{date}_vs_{opponent}_{match_timestamp}_{action}.mp4` | common + `{player}` `{player_number}` `{player_name}` `{action}` `{match_timestamp}` |
| Focused | `focused/{team}/{player}/{action}/{date}_vs_{opponent}_{start_timestamp}-{end_timestamp}.mp4` | common + `{player}` `{player_number}` `{player_name}` `{action}` `{start_timestamp}` `{end_timestamp}` |

Slashes in a template create subfolders. Variable values are sanitized
(safe characters only), template text is trusted (you wrote it). Path
traversal (`..`) is rejected.

For focused clips, `{action}` is the type_name of the first PlayerEvent
by the focused player inside the same rally (rally bounds = nearest
preceding BallServed to next BallServed / kill / ace). FocusIn events
with no matching PlayerEvent in the rally are flagged in red in the
event list and skipped at render time — they're treated as annotation
mistakes.

## Folder layout

```
<data-dir>/
  media/
    <Team>/                         # e.g. "NW_Jrs._15_Grey"
      roster.json                   # players, YouTube + naming config, team color
      <Tournament>/                 # e.g. "Spring_League_25"
        tournament.json             # optional: abbreviation + full_name overrides
        <YYYY-MM-DD>/               # date the matches were played
          <NN_Opponent>/            # e.g. "01_NPJ_Seattle_15_Premier"
            <whatever>.mp4          # one or more source clips
            match.json              # clips list + events + opponent meta
            <render outputs>.mp4    # resolved per the naming templates
            <render outputs>.mp4.youtube.json   # YouTube upload sidecars
  logs/
    service.log                     # NSSM-captured stdout + stderr
  jobs.json                         # render queue persistence
  youtube_client_secret.json        # OAuth Desktop client (you provide)
  youtube_token.json                # rotating access + refresh token
```

`match.json` is the only mutable per-match state. Reordering clips,
deleting a clip, and adding events all rewrite it. Source `.mp4` files
are never modified except during fps transcode (which replaces in place).

To pick up a new clip you've dropped into a match folder by hand, just
reload the page in the editor — every `GET /matches/{...}` re-scans the
folder server-side. (There is no separate "rescan" button or endpoint;
the scan is implicit.)

## Uploading clips

Three flavors, all available from the **Clips** dialog in the match
editor:

1. **Upload videos** — chunked / resumable upload over HTTPS. The file
   is sliced into 32 MiB chunks before sending, so individual requests
   stay well under the 2 GiB IIS/ARR single-request barrier. Each queue
   row has ✕ cancel and ↻ retry buttons.
2. **Import from path…** — type a server-side absolute path (file or
   folder) and the backend copies/moves the files into the match
   folder. Bypasses HTTP entirely; best option when the browser and
   server are on the same machine.
3. **Drop the file into the match folder via Explorer**, then reload
   the editor page. The `GET /matches/{...}` rescan picks it up.

Mixed-fps matches are automatically transcoded to the first clip's
playback rate so all the frame math stays consistent.

## Notes

- HTTP Basic Auth (single shared user) is the only auth layer. Protective
  enough for a small trusted group; not production-grade for hostile
  environments.
- Renders are CPU-bound and run one at a time through `jobs/dispatcher.py`.
  Progress streams to the UI via Server-Sent Events. The queue persists
  to `<data-dir>/jobs.json` so a service restart doesn't lose state.
- YouTube uploads are also enqueued through the same dispatcher
  (`kind="youtube_upload"`), so they don't compete with renders for CPU
  and can be paused / reordered / cancelled like any other job.
