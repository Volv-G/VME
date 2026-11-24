"""Publishing to the public VME site, and its join requests
(see `app/publish/site.py` and `app/publish/site_sync.py`)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..publish import site
from ..publish.site_sync import WORKER

router = APIRouter(prefix="/site", tags=["site"])


@router.get("/status")
def site_status() -> dict:
    cfg = site.load_config()
    return {"configured": cfg is not None, "url": cfg["url"] if cfg else None}


@router.post("/publish")
def site_publish() -> dict:
    try:
        return site.publish()
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/requests")
def site_requests() -> dict:
    """People waiting for approval, as of the last sync (once a minute)."""
    return {"requests": WORKER.requests, "last_sync": WORKER.last_sync, "error": WORKER.error}


class Decision(BaseModel):
    action: Literal["approve", "decline"]


@router.post("/requests/{member_id}")
def site_decide(member_id: int, body: Decision) -> dict:
    try:
        requests = WORKER.sync(decide=[{"id": member_id, "action": body.action}])
    except Exception as exc:
        raise HTTPException(502, f"Couldn't reach the site: {exc}") from exc
    return {"requests": requests, "last_sync": WORKER.last_sync, "error": None}
