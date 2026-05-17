# Match Editor (VME)

Web-based volleyball match editor: upload source video, mark events on a timeline, render a final video with scoreboard + popup overlays.

## Stack

- Backend: Python 3.11 + FastAPI + MoviePy + Pillow
- Frontend: React + Vite + TypeScript
- Storage: filesystem (`media/<Team>/<Match>/...`) with `match.json` sidecars
- Production server: Hypercorn binding both port 80 (redirect) and 443 (HTTPS)

## First-time setup

```powershell
# 1. Install Node.js (one-time)
.\scripts\install-node.ps1

# 2. Install backend deps (one-time)
cd backend
uv sync
cd ..

# 3. Install frontend deps (one-time)
cd frontend
npm install
cd ..
```

## Development

```powershell
# Run backend (:8000) and frontend (:5173) together
.\scripts\dev.ps1
```

Open http://localhost:5173. Vite proxies `/api/*` to the backend.

## Production deployment (Windows service)

There are two supported deployment modes - pick whichever fits your machine.

### Mode A: behind IIS as an Application at /vme (recommended)

IIS terminates TLS on :443 and reverse-proxies `https://<public-host>/vme/*` to a
Python service on `127.0.0.1:8000`. The app lives as an IIS Application under
`Default Web Site` (or whichever parent site you specify), so it coexists with
anything else IIS is hosting.

```powershell
# 1. Build the frontend (Vite is configured with base=/vme/ for prod builds)
.\scripts\build.ps1

# 2. Set Basic Auth credentials
.\scripts\set-credentials.ps1

# 3. Configure IIS (installs URL Rewrite + ARR if needed, creates the
#    Application at /vme, binds your cert via SNI, disables ARR response
#    buffering for SSE). Run as Administrator.
.\scripts\setup-iis.ps1 -PublicHost satis2.duckdns.org
#    Optional overrides:
#      -Thumbprint A1B2C3...   # explicit cert in LocalMachine\My
#      -ParentSite "MySite"    # default: "Default Web Site"
#      -UrlPrefix /matcheditor # default: /vme

# 4. Install the backend as a Windows service. setup-iis.ps1 has written
#    VME_BEHIND_PROXY=1 / VME_URL_PREFIX=/vme to secrets.env, so this
#    installs uvicorn on 127.0.0.1:8000 with --root-path /vme and no
#    firewall / cert work.
.\scripts\install-service.ps1
```

Open `https://satis2.duckdns.org/vme/` — the cert validates cleanly because IIS terminates TLS using the cert in the Windows store.

If you change the URL prefix later, you must rerun **both** `setup-iis.ps1` (with the new `-UrlPrefix`) and `build.ps1` (after editing `BASE_PATH` in `frontend/vite.config.ts`), since the prefix is baked into the static asset URLs.

### Mode B: standalone (no IIS)

Hypercorn binds :80 (HTTP→HTTPS redirect) and :443 (TLS) directly. Use this if
IIS is not installed, or you've taken IIS off ports 80/443.

```powershell
# 1. Build the frontend (also edit BASE_PATH in vite.config.ts to "/" for root-mounted standalone)
.\scripts\build.ps1

# 2. Point the server at a TLS cert. Pick whichever matches what you have:
#    a) Cert is in Windows cert store (LocalMachine\My) - exports to PEM:
.\scripts\set-cert.ps1 -PublicHost satis2.duckdns.org -FromStore
#       ...or by explicit thumbprint:
.\scripts\set-cert.ps1 -PublicHost satis2.duckdns.org -Thumbprint A1B2C3...
#    b) PEM files on disk:
.\scripts\set-cert.ps1 -PublicHost satis2.duckdns.org `
    -CertFile C:\path\to\fullchain.pem -KeyFile C:\path\to\privkey.pem
#    c) PFX bundle (e.g. from win-acme):
.\scripts\set-cert.ps1 -PublicHost satis2.duckdns.org -PfxFile C:\path\to\bundle.pfx
#    d) Self-signed (LAN only, browser warns on first visit):
.\scripts\generate-cert.ps1

# 3. Set Basic Auth credentials
.\scripts\set-credentials.ps1

# 4. Install service (opens firewall :80/:443, downloads NSSM, etc.)
.\scripts\install-service.ps1
```

You only need `generate-cert.ps1` if you have *no* cert at all. If you already have one in the Windows store, use `-FromStore`/`-Thumbprint` and skip the generation step.

### Common operations

- Change the password: re-run `scripts/set-credentials.ps1` (service restarts automatically).
- Remove the service: `scripts/uninstall-service.ps1`. Mode A leaves the IIS site in place; remove that from IIS Manager if you no longer want it.

### Configuration files

- `secrets.env` (gitignored, project root): credentials, cert paths, public hostname,
  `VME_BEHIND_PROXY` flag. Managed by the helper scripts; don't edit by hand unless you know what you're doing.
- `iis/web.config`: URL Rewrite rules + content-length + SSE-friendly compression
  rules used by Mode A. Edits survive `setup-iis.ps1` reruns.

### Going public over the internet (later)

Whichever mode you're in:

1. Forward TCP **80** and **443** on your router to this machine.
2. Make sure your domain points at your public IP. DuckDNS handles dynamic IPs.
3. Mode A: nothing else to do - IIS already serves on those ports.
   Mode B: nothing else to do - the service already binds :80/:443.
4. Basic Auth is enforced regardless of mode, so anyone hitting the URL must sign in.

## Folder layout

```
media/
  <Team>/                  # one folder per team (Team A is permanent)
    roster.json
    <Match>/
      source.mp4           # one or more video files (any name)
      match.json           # events + opponent info (auto-saved)
      renders/             # rendered output files
        preview_*.mp4
```

You can drop new video files into a match folder manually; click **Rescan folder** in the editor to pick them up.

## Notes

- HTTP Basic Auth (single shared user) is the only auth layer. It's protective enough for a small trusted group; do not treat it as production-grade for hostile environments.
- Renders are CPU-bound and run one at a time (FastAPI background thread). Progress streams to the UI via Server-Sent Events.
- The render pipeline is ported from the original PySide desktop app (`C:\temp\Match Editor`); the event system has been redesigned around typed effect properties (timeline / overlay / score / roster).
