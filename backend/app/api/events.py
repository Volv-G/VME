"""Event CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .helpers import (
    deserialize_event,
    event_field_names,
    load_match_or_404,
    save_match,
    serialize_match,
)
from .schemas import EventCreateIn, EventUpdateIn, MatchOut

router = APIRouter(
    prefix="/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/events",
    tags=["events"],
)


@router.post("", response_model=MatchOut)
def create_event(
    team: str,
    tournament: str,
    date: str,
    match: str,
    body: EventCreateIn,
) -> MatchOut:
    m = load_match_or_404(team, tournament, date, match)
    raw = {"type": body.type, **body.payload}
    if body.clip_id is not None:
        raw["clip_id"] = body.clip_id
    if body.local_frame is not None:
        raw["local_frame"] = body.local_frame
    try:
        event = deserialize_event({"type": body.type, "payload": raw})
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"Invalid event: {exc}") from exc
    m.add_event(event)
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)


@router.patch("/{event_id}", response_model=MatchOut)
def update_event(
    team: str,
    tournament: str,
    date: str,
    match: str,
    event_id: int,
    body: EventUpdateIn,
) -> MatchOut:
    m = load_match_or_404(team, tournament, date, match)
    e = m.get_event(event_id)
    if e is None:
        raise HTTPException(404, "Event not found")

    updates: dict = {}
    if body.clip_id is not None:
        updates["clip_id"] = body.clip_id
    if body.local_frame is not None:
        updates["local_frame"] = body.local_frame

    allowed = event_field_names(e)
    for k, v in body.payload.items():
        if k in allowed and k not in ("id", "state"):
            updates[k] = v

    m.update_event(event_id, updates)
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)


@router.delete("/{event_id}", response_model=MatchOut)
def delete_event(
    team: str, tournament: str, date: str, match: str, event_id: int
) -> MatchOut:
    m = load_match_or_404(team, tournament, date, match)
    if m.get_event(event_id) is None:
        raise HTTPException(404, "Event not found")
    m.delete_event(event_id)
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)
