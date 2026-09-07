"""FastAPI application: API + (in production) static frontend."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import BasicAuthMiddleware, configure_from_env
from .config import DATA_DIR, LOG_ROOT, MEDIA_ROOT, STATIC_ROOT
from .api import (
    clips,
    events,
    logos,
    matches,
    media,
    renders,
    teams,
    tournaments,
)
from .jobs import persistence as jobs_persistence
from .jobs.dispatcher import DISPATCHER
from .upload.thumbnail_sync import WORKER as THUMBNAIL_SYNC

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_ROOT / "backend.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

app = FastAPI(title="VME - Match Editor", version="0.1.0")

# Note: middlewares run in reverse insertion order. CORS is added first so that
# it sits *inside* the auth middleware - meaning auth runs first on every
# request. CORS preflight (OPTIONS) is whitelisted in BasicAuthMiddleware.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # LAN tool; tighten if exposed externally
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_auth_user, _auth_hash, _ = configure_from_env()
app.add_middleware(
    BasicAuthMiddleware,
    username=_auth_user,
    password_hash=_auth_hash,
    public_paths={"/api/health"},
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "media_root": str(MEDIA_ROOT),
        "data_dir": str(DATA_DIR) if DATA_DIR else "",
    }


@app.on_event("startup")
def _startup_jobs() -> None:
    """Hydrate the render queue from disk and start the dispatcher thread.

    Order matters: load BEFORE installing the persistence hook so the
    initial load doesn't re-write the file with the same contents, and
    BEFORE starting the dispatcher so any pending jobs are visible to it.
    """
    jobs_persistence.load()
    jobs_persistence.install_hook()
    DISPATCHER.start()
    # Drains thumbnails YouTube refused (usually its per-channel rate
    # limit) so a batch of reels ends up fully branded without the user
    # re-pushing each one by hand.
    THUMBNAIL_SYNC.start()


@app.on_event("shutdown")
def _shutdown_jobs() -> None:
    """Stop the dispatcher cleanly so the daemon thread exits before the
    process does. Persistence is hooked into every state change so no
    explicit flush is needed here."""
    DISPATCHER.stop()
    THUMBNAIL_SYNC.stop()


api = FastAPI()
api.include_router(teams.router)
api.include_router(tournaments.router)
api.include_router(matches.list_router)
api.include_router(matches.router)
api.include_router(clips.router)
api.include_router(events.router)
api.include_router(media.router)
api.include_router(logos.router)
api.include_router(renders.router)
app.mount("/api", api)


# ---- Static frontend (only when built) -------------------------------------


_INDEX = STATIC_ROOT / "index.html"

if _INDEX.is_file():
    app.mount("/assets", StaticFiles(directory=STATIC_ROOT / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa_fallback(path: str):
        candidate = STATIC_ROOT / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_INDEX)
else:
    @app.get("/")
    def root() -> JSONResponse:
        return JSONResponse(
            {
                "message": (
                    "Match Editor API is running. Build the frontend (scripts/build.ps1) "
                    "or run scripts/dev.ps1 for development."
                ),
                "media_root": str(MEDIA_ROOT),
                "static_root": str(STATIC_ROOT),
            }
        )
