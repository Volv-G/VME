"""Scan the media library and reconcile with `match.json` sidecars."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import json

from ..config import MEDIA_ROOT, VIDEO_EXTENSIONS
from ..domain.match import Clip, Match, new_clip_id
from ..domain.roster import Roster
from ..domain.tournament import Tournament
from . import paths
from .probe import probe

logger = logging.getLogger(__name__)


@dataclass
class TeamSummary:
    name: str
    has_roster: bool
    tournament_count: int


@dataclass
class TournamentSummary:
    team: str
    name: str
    match_count: int


@dataclass
class FullRenderInfo:
    """One full-match render visible at the team-dashboard level.

    "Full" here means "a render at the top level of a match's `renders/`
    directory" - we deliberately skip the nested `highlights/` and
    `focused/` subtrees because those are batch outputs and the team
    page would drown in them. Preview renders also live at the top
    level, so we filter those out by filename prefix - preview filenames
    start with `preview_` (see render_job.py's label_segment).
    """

    team: str
    tournament: str
    date: str
    match: str
    # Leaf filename inside the match's renders/ dir.
    filename: str
    size_bytes: int
    created_at: float
    opponent: str = ""
    match_index: int | None = None
    # Populated from the `.youtube.json` sidecar if a successful upload
    # has been recorded for this file. None when no upload exists.
    youtube_video_id: str | None = None
    youtube_uploaded_at: float | None = None


@dataclass
class MatchSummary:
    team: str
    tournament: str
    # Date folder under the tournament (also the canonical date string for
    # display). The match identity is (team, tournament, date, name).
    date: str
    # Match folder name (URL identifier). Conventionally `<NN>_<opponent>`.
    name: str
    # 1-based match order on that date, parsed from the folder name prefix.
    # Null for legacy folders without a numeric prefix.
    match_index: int | None
    opponent: str
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
        tournament_count = sum(1 for c in d.iterdir() if c.is_dir())
        teams.append(
            TeamSummary(
                name=d.name,
                has_roster=roster_path.is_file(),
                tournament_count=tournament_count,
            )
        )
    return teams


def list_tournaments(team: str) -> list[TournamentSummary]:
    """List tournament folders under a team."""
    team_path = paths.team_dir(team)
    if not team_path.exists():
        return []
    out: list[TournamentSummary] = []
    for d in sorted(team_path.iterdir()):
        if not d.is_dir():
            continue
        # Count matches across all date subfolders.
        match_count = 0
        for date_d in d.iterdir():
            if date_d.is_dir():
                match_count += sum(1 for c in date_d.iterdir() if c.is_dir())
        out.append(TournamentSummary(team=team, name=d.name, match_count=match_count))
    return out


def list_matches(team: str, tournament: str) -> list[MatchSummary]:
    """List matches under a tournament, flattened across date subfolders.

    Sorted by (date, match_index, name) so the UI gets a chronological
    listing (within each date, by play order) for free.
    """
    t_path = paths.tournament_dir(team, tournament)
    if not t_path.exists():
        return []
    summaries: list[MatchSummary] = []
    for date_d in sorted(t_path.iterdir()):
        if not date_d.is_dir():
            continue
        # Sort by parsed match index (legacy unnumbered folders go last).
        children = [c for c in date_d.iterdir() if c.is_dir()]
        children.sort(
            key=lambda c: (
                (paths.parse_match_folder(c.name)[0] is None),
                paths.parse_match_folder(c.name)[0] or 0,
                c.name,
            )
        )
        for d in children:
            videos = [
                v for v in d.iterdir()
                if v.is_file() and v.suffix.lower() in VIDEO_EXTENSIONS
            ]
            match_json = d / "match.json"
            idx, opponent_part = paths.parse_match_folder(d.name)
            opponent = opponent_part  # default: from folder name
            if match_json.exists():
                try:
                    m = Match.load(match_json)
                    opponent = m.opponent or opponent
                except Exception as exc:
                    logger.warning("failed to load %s: %s", match_json, exc)
            summaries.append(
                MatchSummary(
                    team=team,
                    tournament=tournament,
                    date=date_d.name,
                    name=d.name,
                    match_index=idx,
                    opponent=opponent,
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


def load_tournament_info(team: str, tournament: str) -> Tournament:
    """Read the tournament.json sidecar (or return defaults if absent)."""
    p = paths.tournament_json_path(team, tournament)
    if not p.exists():
        return Tournament()
    return Tournament.load(p)


def save_tournament_info(
    team: str, tournament: str, info: Tournament
) -> None:
    p = paths.tournament_json_path(team, tournament)
    p.parent.mkdir(parents=True, exist_ok=True)
    info.save(p)


def load_youtube_sidecar(render_path: Path) -> dict | None:
    """Read a render's YouTube-upload sidecar (or None if absent / unreadable).

    Sidecar shape: `{ "video_id": str, "uploaded_at": float,
    "title": str, "privacy_status": str, "playlist_id": str | None }`.
    Anything else in the file is ignored on read; we just round-trip the
    keys we care about.
    """
    p = paths.youtube_sidecar_path(render_path)
    if not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, ValueError):
        return None


def save_youtube_sidecar(render_path: Path, info: dict) -> None:
    """Write the YouTube-upload sidecar next to a render file.

    Caller is responsible for the dict shape; we just JSON-dump it.
    """
    p = paths.youtube_sidecar_path(render_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)


def _is_full_render(render_filename: str) -> bool:
    """True iff `render_filename` represents a full-match render.

    Filtering rules (kept here so list_team_full_renders and the upload
    endpoint agree on what's eligible):
      - Must be a top-level .mp4 inside the renders/ dir (no slashes:
        anything in `highlights/` or `focused/` is excluded).
      - Preview outputs are excluded by their `preview_` filename prefix
        (see render_job.py: previews use label='preview' which becomes
        the segment).
    """
    if "/" in render_filename or "\\" in render_filename:
        return False
    if not render_filename.lower().endswith(".mp4"):
        return False
    leaf = render_filename.lower()
    if leaf.startswith("preview_") or leaf == "preview.mp4":
        return False
    return True


def list_team_full_renders(team: str) -> list[FullRenderInfo]:
    """Enumerate all full-match renders under a team, across tournaments.

    Walks `<team>/<tournament>/<date>/<match>/renders/` for each match
    folder and picks up top-level mp4s only. Each entry includes the
    YouTube upload state if a `.youtube.json` sidecar is present.

    Sorted newest-first by file mtime so the team dashboard shows
    recent renders at the top without further client-side sorting.
    """
    team_path = paths.team_dir(team)
    if not team_path.exists():
        return []
    out: list[FullRenderInfo] = []
    for tournament_d in team_path.iterdir():
        if not tournament_d.is_dir():
            continue
        for date_d in tournament_d.iterdir():
            if not date_d.is_dir():
                continue
            for match_d in date_d.iterdir():
                if not match_d.is_dir():
                    continue
                renders = match_d / "renders"
                if not renders.is_dir():
                    continue
                idx, opponent_part = paths.parse_match_folder(match_d.name)
                # Prefer the match.json's opponent if available - it's the
                # display string the user typed, vs. the folder-slug fallback.
                opponent = opponent_part
                match_json = match_d / "match.json"
                if match_json.exists():
                    try:
                        opponent = (
                            Match.load(match_json).opponent or opponent
                        )
                    except Exception:
                        pass
                for p in renders.iterdir():
                    if not p.is_file():
                        continue
                    if not _is_full_render(p.name):
                        continue
                    try:
                        stat = p.stat()
                    except OSError:
                        continue
                    sidecar = load_youtube_sidecar(p)
                    out.append(
                        FullRenderInfo(
                            team=team,
                            tournament=tournament_d.name,
                            date=date_d.name,
                            match=match_d.name,
                            filename=p.name,
                            size_bytes=stat.st_size,
                            created_at=stat.st_mtime,
                            opponent=opponent,
                            match_index=idx,
                            youtube_video_id=(
                                sidecar.get("video_id") if sidecar else None
                            ),
                            youtube_uploaded_at=(
                                sidecar.get("uploaded_at") if sidecar else None
                            ),
                        )
                    )
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


def load_or_create_match(
    team: str, tournament: str, date: str, match: str
) -> Match:
    """Load `match.json` if present; otherwise create an empty Match.

    Always reconciles the clip list with files actually present on disk.
    """
    json_path = paths.match_json_path(team, tournament, date, match)
    if json_path.exists():
        m = Match.load(json_path)
    else:
        m = Match(opponent="", date="", fps=30.0)
    # Authoritative date / opponent come from the folder hierarchy. Keep the
    # JSON in sync so existing code reading `m.opponent` / `m.date` (e.g.
    # the renderer, summaries) doesn't have to know about path layout.
    if m.date != date:
        m.date = date
    if not m.opponent:
        m.opponent = match
    reconcile_clips(team, tournament, date, match, m)
    return m


def reconcile_clips(
    team: str, tournament: str, date: str, match: str, m: Match
) -> bool:
    """Add any video files in the match folder that aren't yet in `m.clips`.

    Returns True if any change was made (caller should save).
    """
    folder = paths.match_dir(team, tournament, date, match)
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

    if backfill_recording_times(team, tournament, date, match, m):
        changed = True

    return changed


def backfill_recording_times(
    team: str, tournament: str, date: str, match: str, m: Match
) -> bool:
    """Fill in `start_recording_time` for any clips that don't have one yet."""
    if not m.clips or all(c.start_recording_time is not None for c in m.clips):
        return False
    folder = paths.match_dir(team, tournament, date, match)
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
    """Make `m.fps` match the first clip's playback rate."""
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


def create_tournament(team: str, tournament: str) -> Path:
    p = paths.tournament_dir(team, tournament)
    p.mkdir(parents=True, exist_ok=True)
    return p


def delete_tournament(team: str, tournament: str) -> None:
    folder = paths.tournament_dir(team, tournament)
    if not folder.exists():
        return
    import shutil

    shutil.rmtree(folder)


class MatchIndexConflict(ValueError):
    """Raised when the requested match index is already used on that date."""


def create_match(
    team: str,
    tournament: str,
    date: str,
    opponent: str,
    match_index: int | None = None,
) -> tuple[str, Match]:
    """Create a new match folder under `<Tournament>/<Date>/<NN>_<Opponent>/`.

    `match_index` is the 1-based match order on that date. When omitted, the
    server picks `max(existing) + 1`. When supplied, it must not collide with
    an existing match (raises `MatchIndexConflict`).

    Returns `(match_name, Match)` where `match_name` is the on-disk folder
    leaf (e.g. `"03_North_Vipers"`).
    """
    parent = paths.date_dir(team, tournament, date)
    parent.mkdir(parents=True, exist_ok=True)

    used_indices = {
        i
        for i in (
            paths.parse_match_folder(c.name)[0]
            for c in parent.iterdir()
            if c.is_dir()
        )
        if i is not None
    }

    if match_index is None:
        idx = (max(used_indices) + 1) if used_indices else 1
    else:
        if match_index < 1:
            raise ValueError("match_index must be >= 1")
        if match_index in used_indices:
            raise MatchIndexConflict(
                f"Match #{match_index} already exists on {date}"
            )
        idx = match_index

    folder_name = paths.format_match_folder(idx, opponent or "match")
    # Defensive: if a name collision still exists (e.g. mixed legacy folders
    # use the same NN_opponent slug), bump until we find a free slot. Only
    # reachable when match_index is None.
    while (parent / folder_name).exists():
        if match_index is not None:
            raise MatchIndexConflict(
                f"Folder {folder_name!r} already exists on {date}"
            )
        idx += 1
        folder_name = paths.format_match_folder(idx, opponent or "match")

    folder = parent / folder_name
    folder.mkdir(parents=True, exist_ok=False)

    m = Match(opponent=opponent or folder_name, date=date, fps=30.0)
    m.save(folder / "match.json")
    return folder_name, m


def delete_match(team: str, tournament: str, date: str, match: str) -> None:
    folder = paths.match_dir(team, tournament, date, match)
    if not folder.exists():
        return
    import shutil

    shutil.rmtree(folder)
