"""Publishing to the public VME site (see `app/publish/site.py`)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..publish import site

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
