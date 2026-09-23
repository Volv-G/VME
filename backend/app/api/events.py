"""Event CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.events.timeline import ClipTransitionEvent
from .helpers import (
    deserialize_event,
    event_field_names,
    load_match_or_404,
    save_match,
    serialize_match,
)
from .schemas import (
    EventCreateIn,
    EventUpdateIn,
    ImportEventsIn,
    ImportEventsOut,
    MatchOut,
    MoveEventIn,
    NudgeEventIn,
)

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
    # A caller that gives a moment rather than a frame gets it projected
    # onto the clips; one that gives both is taken at its word on the
    # frame, since that is what will be rendered.
    if body.at_ms is not None and body.clip_id is None:
        if not m.reproject_event(event, body.at_ms):
            raise HTTPException(
                400,
                "This match's clips have no recording times, so an event "
                "cannot be placed by wall clock. Give clip_id and "
                "local_frame instead.",
            )
    elif body.at_ms is not None:
        event.at_ms = body.at_ms
    else:
        event.at_ms = m.event_time_ms(event)
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
    if body.at_ms is not None:
        # Re-derive the frame position, unless the caller also pinned it.
        placed = m.project_ms(body.at_ms)
        if placed is not None and body.clip_id is None and body.local_frame is None:
            updates["clip_id"], updates["local_frame"] = placed
        updates["at_ms"] = body.at_ms
    elif body.clip_id is not None or body.local_frame is not None:
        # Frames moved, so the recorded moment is now a lie. Recompute it
        # from where the event has actually been put.
        if not isinstance(e, ClipTransitionEvent):
            clip_id = updates.get("clip_id", e.clip_id)
            local_frame = updates.get("local_frame", e.local_frame)
            start = m.clip_start_ms(clip_id)
            if start is not None:
                clip = m.get_clip(clip_id)
                fps = (clip.fps if clip and clip.fps else m.fps) or 30.0
                updates["at_ms"] = int(start + (local_frame / fps) * 1000.0)

    allowed = event_field_names(e)
    for k, v in body.payload.items():
        if k in allowed and k not in ("id", "state"):
            updates[k] = v

    m.update_event(event_id, updates)
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)


@router.post("/{event_id}/move", response_model=MatchOut)
def move_event(
    team: str,
    tournament: str,
    date: str,
    match: str,
    event_id: int,
    body: MoveEventIn,
) -> MatchOut:
    """Place one event directly after another - one frame later.

    A frame is the smallest gap that still sorts, so the moved event
    lands as close to its new neighbour as the timeline can express.
    That keeps a reorder a reorder: it changes which side of an event
    this one falls on without inventing a gap that says something about
    when it happened.

    `at_ms` is recomputed from the new frame rather than carried over.
    It is the event's anchor to the wall clock, and leaving it pointing
    at the old moment would let the next re-projection - a clip gaining
    recording times, say - undo the move.
    """
    m = load_match_or_404(team, tournament, date, match)
    e = m.get_event(event_id)
    if e is None:
        raise HTTPException(404, "Event not found")
    if isinstance(e, ClipTransitionEvent):
        raise HTTPException(
            400,
            "A clip transition sits at the boundary between two clips and "
            "moves only when the clips do.",
        )
    if body.after_event_id == event_id:
        raise HTTPException(400, "An event cannot be placed after itself.")

    if body.after_event_id is None:
        target = 0
    else:
        anchor = m.get_event(body.after_event_id)
        if anchor is None:
            raise HTTPException(404, "The event to place this after was not found")
        anchor_frame = m.to_global_frame(anchor.clip_id, anchor.local_frame)
        if anchor_frame is None:
            raise HTTPException(
                400,
                "The event to place this after is not on any clip yet, so "
                "there is no frame to go one past.",
            )
        target = anchor_frame + 1

    target = max(0, min(target, max(0, m.total_frames() - 1)))
    placed = m.from_global_frame(target)
    if placed is None:
        raise HTTPException(400, "Nowhere to move this event to.")
    e.clip_id, e.local_frame = placed
    e.at_ms = m.event_time_ms(e)

    # Same bookkeeping `update_event` does: order changes when an event
    # crosses another, and every state snapshot after it shifts.
    m.update_event(event_id, {})
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)


@router.post("/{event_id}/nudge", response_model=MatchOut)
def nudge_event(
    team: str,
    tournament: str,
    date: str,
    match: str,
    event_id: int,
    body: NudgeEventIn,
) -> MatchOut:
    """Shift one event forward or back along the timeline.

    Wall clock when the match has recording times, because that is the
    thing that is actually true: a second later is a second later
    whatever clip it lands in, including across a boundary.

    Frames when it does not. A legacy match has no anchor to the world,
    and refusing to nudge it would make the buttons dead on exactly the
    matches most likely to need hand-correcting, so it falls back to
    moving along the edit instead - the same distance, measured the only
    way that match can measure it.
    """
    m = load_match_or_404(team, tournament, date, match)
    e = m.get_event(event_id)
    if e is None:
        raise HTTPException(404, "Event not found")
    if isinstance(e, ClipTransitionEvent):
        raise HTTPException(
            400,
            "A clip transition sits at the boundary between two clips and "
            "moves only when the clips do.",
        )

    moved = False
    if getattr(e, "at_ms", None) is not None:
        moved = m.reproject_event(e, int(e.at_ms + round(body.seconds * 1000)))

    if not moved:
        current = m.to_global_frame(e.clip_id, e.local_frame)
        if current is None:
            raise HTTPException(400, "This event is not on any clip.")
        fps = m.fps or 30.0
        last = max(0, m.total_frames() - 1)
        target = max(0, min(int(round(current + body.seconds * fps)), last))
        placed = m.from_global_frame(target)
        if placed is None:
            raise HTTPException(400, "Nowhere to move this event to.")
        e.clip_id, e.local_frame = placed
        e.at_ms = m.event_time_ms(e)

    # Same bookkeeping `update_event` does: order can change when an
    # event crosses another, and every state snapshot after it shifts.
    m.update_event(event_id, {})
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)


# Event names older phone builds wrote that VME knows under another name.
# `timeout` was logged at a timeout's start only, and VME never had a
# type for it, so every one of them was skipped on import; read as the
# start it always was. Such a start has no end, so it pairs the way an
# unclosed cut does - with a set/game end, never across a serve.
LEGACY_PHONE_TYPES = {"timeout": "timeout_start"}


@router.post("/import", response_model=ImportEventsOut)
def import_events(
    team: str,
    tournament: str,
    date: str,
    match: str,
    body: ImportEventsIn,
) -> ImportEventsOut:
    """Take an event log captured elsewhere and place it on this match.

    The phone tags events against a wall clock and never sees the
    footage; the clips carry the recording times ffprobe read off them.
    Between the two, every event lands on the frame it actually
    happened on - which is the whole point of the exercise, and why
    this refuses rather than guesses when the clips have no times.

    Not idempotent by itself: calling it twice adds the events twice.
    `replace` is how a re-import is meant to be done, and it keeps clip
    transitions, which are structural rather than tagged.
    """
    m = load_match_or_404(team, tournament, date, match)
    # No clips yet, or none of them dated, is the normal case rather than
    # an error: the phone is finished the moment the match is, while the
    # card is still in the camera. Such events are stored with their
    # timestamp and no frame position, and `place_pending_events` picks
    # them up the first time the match is opened after the footage lands.
    can_place = bool(m.clips) and m.timeline_epoch_ms is not None

    if body.replace:
        m.events = [e for e in m.events if isinstance(e, ClipTransitionEvent)]

    imported = 0
    snapped = 0
    pending = 0
    skipped: list[str] = []

    for raw in body.events:
        type_name = LEGACY_PHONE_TYPES.get(raw.get("type"), raw.get("type"))
        at = raw.get("_at", raw.get("at_ms"))
        if not type_name:
            skipped.append("an entry with no type")
            continue
        if not at:
            skipped.append(f"{type_name}: no timestamp")
            continue
        at_ms = int(at)

        # The phone's own clip_id / local_frame are placeholders for
        # footage it never had; drop them and let the projection decide.
        fields = {
            k: v
            for k, v in raw.items()
            if k not in ("type", "id", "clip_id", "local_frame", "_at", "at_ms")
        }
        payload = fields.pop("payload", None)
        if isinstance(payload, dict):
            fields.update(payload)

        try:
            event = deserialize_event({"type": type_name, "payload": fields})
        except (ValueError, TypeError) as exc:
            skipped.append(f"{type_name}: {exc}")
            continue

        if can_place:
            if not m.covers_ms(at_ms):
                snapped += 1
            if not m.reproject_event(event, at_ms):
                skipped.append(f"{type_name}: could not be placed")
                continue
        else:
            # Parked: the time is what matters and we have it. The frame
            # position stays empty rather than being guessed at, so
            # nothing downstream mistakes it for a real placement.
            event.at_ms = at_ms
            event.clip_id = ""
            event.local_frame = 0
            pending += 1
        m.add_event(event)
        imported += 1

    save_match(team, tournament, date, match, m)
    return ImportEventsOut(
        imported=imported,
        snapped=snapped,
        pending=pending,
        skipped=skipped,
        match=serialize_match(team, tournament, date, match, m),
    )


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
