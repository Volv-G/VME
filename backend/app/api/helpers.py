"""Shared helpers used by API routes."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from ..domain.events import event_from_dict, event_to_dict
from ..domain.events.base import MatchEvent
from ..domain.events.timeline import ClipTransitionEvent
from ..domain.game_state import GameState
from ..domain.match import Match
from ..library import paths, scanner
from .schemas import (
    ClipOut,
    EventOut,
    GameStateOut,
    MatchOut,
    PlayerOut,
    RosterOut,
)


def _serialize_state(state: GameState | None) -> GameStateOut | None:
    if state is None:
        return None
    return GameStateOut(
        home_score=state.home_score,
        away_score=state.away_score,
        home_sets=state.home_sets,
        away_sets=state.away_sets,
        # Team is a str-Enum; .value yields "home" / "away".
        serving_team=state.serving_team.value if state.serving_team is not None else None,
        home_positions=dict(state.home_positions),
        away_positions=dict(state.away_positions),
        game_started=state.game_started,
        game_ended=state.game_ended,
        in_cut_region=state.in_cut_region,
        ball_served_since_last_score=state.ball_served_since_last_score,
    )


def _serialize_roster(r) -> RosterOut:
    return RosterOut(
        team_name=r.team_name,
        team_color=r.team_color,
        players=[PlayerOut(**p.to_dict()) for p in r.players],
    )


def serialize_event(match: Match, event: MatchEvent) -> EventOut:
    raw = event_to_dict(event)
    payload = {k: v for k, v in raw.items() if k not in ("type", "id")}
    if isinstance(event, ClipTransitionEvent):
        global_frame = None
        state = None  # transitions don't carry meaningful state
    else:
        global_frame = match.to_global_frame(event.clip_id, event.local_frame)
        state = _serialize_state(event.state)
    return EventOut(
        type=raw["type"],
        id=raw["id"],
        payload=payload,
        global_frame=global_frame,
        state=state,
    )


def serialize_match(team: str, match_name: str, m: Match) -> MatchOut:
    return MatchOut(
        team=team,
        name=match_name,
        opponent=m.opponent,
        date=m.date,
        fps=m.fps,
        clips=[
            ClipOut(
                id=c.id,
                filename=c.filename,
                frame_count=c.frame_count,
                fps=c.fps,
                width=c.width,
                height=c.height,
                start_recording_time=c.start_recording_time,
            )
            for c in m.clips
        ],
        events=[serialize_event(m, e) for e in m.events],
        home_roster=_serialize_roster(scanner.load_team_roster(team)),
        opponent_roster=_serialize_roster(m.opponent_roster),
    )


def deserialize_event(payload: dict[str, Any]) -> MatchEvent:
    """Build a MatchEvent from `{type, id?, payload: {...}}`."""
    data = {"type": payload["type"]}
    if "id" in payload:
        data["id"] = payload["id"]
    data.update(payload.get("payload", {}))
    return event_from_dict(data)


def event_field_names(event: MatchEvent) -> set[str]:
    if not is_dataclass(event):
        return set()
    return {f.name for f in fields(event) if f.name != "state"}


def save_match(team: str, match_name: str, m: Match) -> None:
    m.save(paths.match_json_path(team, match_name))


def load_match_or_404(team: str, match_name: str) -> Match:
    folder = paths.match_dir(team, match_name)
    if not folder.exists():
        raise FileNotFoundError(f"Match not found: {team}/{match_name}")
    return scanner.load_or_create_match(team, match_name)
