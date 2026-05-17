"""Scan the media library and reconcile with `match.json` sidecars."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import MEDIA_ROOT, VIDEO_EXTENSIONS
from ..domain.match import Clip, Match, new_clip_id
from ..domain.roster import Roster
from . import paths
from .probe import probe

logger = logging.getLogger(__name__)


@dataclass
class TeamSummary:
    name: str
    has_roster: bool
    match_count: int


@dataclass
class MatchSummary:
    team: str
    name: str
    opponent: str
    date: str
    clip_count: int
    has_match_json: bool
    has_video: bool


def list_teams() -> list[TeamSummary]:
    """List all team folders under `MEDIA_ROOT`."""
    if not MEDIA_ROOT.exists():
        return []
    teams: list[TeamSummary] = []
    for d in sorted(MEDIA_ROOT.iterdir()):
        if not d.is_dir():
            continue
        roster_path = d / "roster.json"
        match_count = sum(1 for c in d.iterdir() if c.is_dir())
        teams.append(
            TeamSummary(
                name=d.name,
                has_roster=roster_path.is_file(),
                match_count=match_count,
            )
        )
    return teams


def list_matches(team: str) -> list[MatchSummary]:
    """List match folders for a team."""
    team_path = paths.team_dir(team)
    if not team_path.exists():
        return []
    summaries: list[MatchSummary] = []
    for d in sorted(team_path.iterdir()):
        if not d.is_dir():
            continue
        videos = [v for v in d.iterdir() if v.is_file() and v.suffix.lower() in VIDEO_EXTENSIONS]
        match_json = d / "match.json"
        opponent = ""
        date = ""
        if match_json.exists():
            try:
                m = Match.load(match_json)
                opponent = m.opponent
                date = m.date
            except Exception as exc:
                logger.warning("failed to load %s: %s", match_json, exc)
        summaries.append(
            MatchSummary(
                team=team,
                name=d.name,
                opponent=opponent,
                date=date,
                clip_count=len(videos),
                has_match_json=match_json.exists(),
                has_video=bool(videos),
            )
        )
    return summaries


def load_team_roster(team: str) -> Roster:
    p = paths.team_roster_path(team)
    if not p.exists():
        return Roster()
    return Roster.load(p)


def save_team_roster(team: str, roster: Roster) -> None:
    p = paths.team_roster_path(team)
    p.parent.mkdir(parents=True, exist_ok=True)
    roster.save(p)


def load_or_create_match(team: str, match: str) -> Match:
    """Load `match.json` if present; otherwise create an empty Match.

    Always reconciles the clip list with files actually present on disk.
    """
    json_path = paths.match_json_path(team, match)
    if json_path.exists():
        m = Match.load(json_path)
    else:
        m = Match(opponent="", date="", fps=30.0)
    reconcile_clips(team, match, m)
    return m


def reconcile_clips(team: str, match: str, m: Match) -> bool:
    """Add any video files in the match folder that aren't yet in `m.clips`.

    Returns True if any change was made (caller should save).
    """
    folder = paths.match_dir(team, match)
    if not folder.exists():
        return False

    known = {c.filename for c in m.clips}
    changed = False
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if path.name in known:
            continue
        info = probe(path)
        clip = Clip(
            id=new_clip_id(),
            filename=path.name,
            frame_count=info.frame_count if info else 0,
            fps=info.fps if info else 30.0,
            width=info.width if info else 0,
            height=info.height if info else 0,
            start_recording_time=info.start_recording_time if info else None,
        )
        m.add_clip(clip)
        changed = True

    if sync_match_fps(m):
        changed = True

    if backfill_recording_times(team, match, m):
        changed = True

    return changed


def backfill_recording_times(team: str, match: str, m: Match) -> bool:
    """Fill in `start_recording_time` for any clips that don't have one yet.

    This handles legacy `match.json` files created before recording-time
    metadata existed. Probing is slow, so we only do it for clips that
    actually need backfilling.

    Returns True if any clip was updated.
    """
    if not m.clips or all(c.start_recording_time is not None for c in m.clips):
        return False
    folder = paths.match_dir(team, match)
    if not folder.exists():
        return False
    changed = False
    for clip in m.clips:
        if clip.start_recording_time is not None:
            continue
        clip_path = folder / clip.filename
        if not clip_path.is_file():
            continue
        info = probe(clip_path)
        if info is not None and info.start_recording_time is not None:
            clip.start_recording_time = info.start_recording_time
            changed = True
    return changed


def sync_match_fps(m: Match) -> bool:
    """Make `m.fps` match the first clip's playback rate.

    The FrameMap has one entry per source frame, so the output runs at the
    source clip's fps. If `m.fps` drifts away from that (e.g. it was left at
    the default 30 while clips are 60 fps), rendered videos play back at the
    wrong speed.

    Returns True if `m.fps` was changed.
    """
    if not m.clips:
        return False
    first_fps = m.clips[0].fps
    if first_fps <= 0:
        return False
    if abs(m.fps - first_fps) < 1e-3:
        return False
    logger.info("syncing match.fps %.3f -> %.3f (from first clip)", m.fps, first_fps)
    m.fps = first_fps
    return True


def create_team(team: str) -> Path:
    p = paths.team_dir(team)
    p.mkdir(parents=True, exist_ok=True)
    return p


def create_match(team: str, match: str, opponent: str = "", date: str = "") -> Match:
    folder = paths.match_dir(team, match)
    folder.mkdir(parents=True, exist_ok=True)
    json_path = paths.match_json_path(team, match)
    if json_path.exists():
        return load_or_create_match(team, match)
    m = Match(opponent=opponent, date=date, fps=30.0)
    m.save(json_path)
    return m


def delete_match(team: str, match: str) -> None:
    folder = paths.match_dir(team, match)
    if not folder.exists():
        return
    import shutil

    shutil.rmtree(folder)
