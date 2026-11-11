"""Season reel jobs: render one player's season, and upload it to OneDrive.

A season reel is the player's reel of every match the team has, in
order, each match introduced by a title card - the match thumbnail's
design with the player in the home slot - that fades in and out. See
`render/season.py` for how the pieces are joined.

Both are team-level jobs: `tournament`, `date` and `match` are empty
and the player is `payload["jersey"]`. Output goes to
`<team>/season_reels/<NN>_<player>/`.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..domain.match import Match
from ..domain.roster import Roster
from ..library import paths, scanner
from ..render import season
from ..render.reels import ReelSpec, reels_from_match
from ..render.renderer import MatchRenderer, RenderCancelled, RenderProgress
from ..render.settings import load_settings
from ..render.thumbnail import ThumbnailSpec, build_thumbnail, thumbnail_path, write_thumbnail
from .manager import JOBS, RenderJob

logger = logging.getLogger(__name__)

# The parts are encoded twice - once by the match renderer, once by the
# join - so the first pass runs at a rate that leaves nothing for the
# second to lose.
_PART_BITRATE = "40000k"


@dataclass
class SeasonMatch:
    """One match of the season with this player's plays in it."""

    tournament: str
    date: str
    match: str
    m: Match
    reel: ReelSpec


def player_label(roster: Roster, jersey: int) -> tuple[str, str]:
    """("#8 Kate G", "Kate G") for a jersey, from the roster."""
    p = roster.find_by_number(jersey)
    name = (p.short_name or p.name) if p is not None else ""
    return (f"#{jersey} {name}".strip(), name)


def output_folder(team: str, roster: Roster, jersey: int) -> Path:
    p = roster.find_by_number(jersey)
    return paths.season_reels_dir(team) / paths.player_folder_name(
        jersey, p.name if p is not None else None
    )


def _has_footage(team: str, tournament: str, date: str, match: str, m: Match) -> bool:
    folder = paths.match_dir(team, tournament, date, match)
    return bool(m.clips) and all((folder / c.filename).is_file() for c in m.clips)


def season_matches(
    team: str, roster: Roster, jersey: Optional[int] = None
) -> tuple[list[SeasonMatch], list[str]]:
    """Every match with plays by `jersey` (or by anyone, when None), in
    playing order, and the matches left out because their footage is
    gone - those cannot be rendered, and the caller says so."""
    found: list[SeasonMatch] = []
    no_footage: list[str] = []
    for d in paths.iter_tournament_dirs(team):
        for s in scanner.list_matches(team, d.name):
            if not s.has_match_json:
                continue
            try:
                m = scanner.load_or_create_match(team, s.tournament, s.date, s.name)
                reels = reels_from_match(m, roster, roster.team_name or team)
            except Exception as exc:  # one broken match must not sink the season
                logger.warning("season reel: skipping %s/%s: %s", s.date, s.name, exc)
                continue
            reels = [r for r in reels if jersey is None or r.jersey == jersey]
            if not reels:
                continue
            if not _has_footage(team, s.tournament, s.date, s.name, m):
                no_footage.append(f"{s.date} {s.opponent}")
                continue
            for reel in reels:
                found.append(SeasonMatch(s.tournament, s.date, s.name, m, reel))
    found.sort(
        key=lambda x: (x.date, paths.parse_match_folder(x.match)[0] or 0, x.tournament)
    )
    return found, no_footage


def _part_settings(settings: dict) -> dict:
    """The team's settings, at a bitrate high enough to survive the join."""
    out = dict(settings)
    params = list(settings.get("ffmpeg_params") or [])
    if "-b:v" in params:
        i = params.index("-b:v")
        if i + 1 < len(params):
            params[i + 1] = _PART_BITRATE
    out["ffmpeg_params"] = params
    return out


def _card(team: str, sm: SeasonMatch, roster: Roster, label: str, part: Path, jersey: int):
    """The title card for one match: its thumbnail, with the player in the
    home slot and the number of their plays in the banner."""
    from .render_job import _branding, _grab_backdrop, _resolve_player_photo, _thumbnail_caption

    home, away = _branding(team, sm.tournament, sm.date, sm.match, sm.m, roster)
    photo = _resolve_player_photo(team, roster, jersey)
    plays = len(sm.reel.segments)
    spec = ThumbnailSpec(
        home_name=home.name,
        away_name=away.name,
        home_color=home.color,
        away_color=away.color,
        home_logo=photo,
        away_logo=away.logo_path,
        home_badge="" if photo else f"#{jersey}",
        home_is_photo=photo is not None,
        caption=_thumbnail_caption(team, sm.tournament, sm.date, sm.match, sm.m, roster),
        subject=f"{label}  ·  {plays} play{'' if plays == 1 else 's'}",
        backdrop=_grab_backdrop(part),
    )
    return build_thumbnail(spec)


def _season_thumbnail(
    team: str, roster: Roster, jersey: int, label: str, matches: int, year: str, output: Path
) -> None:
    """The reel's own thumbnail: the player beside the team crest, the
    season's year between them where a match has its "VS"."""
    from .render_job import _resolve_logo, _resolve_player_photo

    photo = _resolve_player_photo(team, roster, jersey)
    crest = _resolve_logo(paths.team_dir(team), roster.team_logo_path, paths.TEAM_LOGO_STEM)
    color = roster.team_color or "#2d8a4e"
    spec = ThumbnailSpec(
        home_name=label,
        away_name=roster.team_name or team,
        home_color=color,
        away_color=color,
        home_logo=photo,
        away_logo=crest,
        home_badge="" if photo else f"#{jersey}",
        home_is_photo=photo is not None,
        center_text=year,
        caption=f"Season  ·  {matches} match{'' if matches == 1 else 'es'}",
        subject=f"{label}  ·  Season highlights",
    )
    write_thumbnail(output, spec)


def _timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def run_season_reel(job: RenderJob, cancel_check) -> None:
    jersey = int((job.payload or {}).get("jersey"))
    team = job.team
    roster = scanner.load_team_roster(team)
    label, _ = player_label(roster, jersey)

    JOBS.update(job.id, phase="scanning", message="Finding the plays in every match")
    matches, no_footage = season_matches(team, roster, jersey)
    if not matches:
        why = f" ({len(no_footage)} with plays have no footage)" if no_footage else ""
        JOBS.update(job.id, message=f"No plays by {label} in any match{why}")
        JOBS.mark_done(job.id, "")
        return

    settings = load_settings()
    part_settings = _part_settings(settings)
    folder = output_folder(team, roster, jersey)
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"season_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{settings.get('container') or 'mp4'}"
    output = folder / filename
    rel = output.relative_to(paths.season_reels_dir(team)).as_posix()
    JOBS.update(job.id, output_filename=rel)

    work = folder / f".work_{job.id}"
    work.mkdir(parents=True, exist_ok=True)
    total_frames = sum(sm.reel.total_frames for sm in matches) or 1
    done = [0]
    last_emit = [0.0]

    def say(pct: float, msg: str, phase: str = "video") -> None:
        now = time.time()
        if now - last_emit[0] > 0.4:
            JOBS.update(job.id, percent=pct, phase=phase, message=msg)
            last_emit[0] = now

    try:
        from .render_job import _branding, _reel_captions

        pieces: list[season.Piece] = []
        lines: list[str] = []
        fps = 0.0
        for i, sm in enumerate(matches, start=1):
            if cancel_check():
                raise RenderCancelled("Render cancelled by user")
            where = f"[{i}/{len(matches)}] {sm.date} vs {sm.m.opponent or sm.match}"

            def on_progress(p: RenderProgress, where=where) -> None:
                if p.phase == "audio":
                    say((done[0] / total_frames) * 80.0, f"{where}: audio", "audio")
                    return
                pct = (done[0] + p.frames_done) / total_frames * 80.0
                say(pct, f"{where}: {p.frames_done}/{p.frames_total} frames")

            home, away = _branding(team, sm.tournament, sm.date, sm.match, sm.m, roster)
            media_dir = paths.match_dir(team, sm.tournament, sm.date, sm.match)
            part = work / f"part_{i:03d}.mp4"
            with MatchRenderer(
                sm.m, media_dir, home=home, away=away,
                home_roster=roster, away_roster=sm.m.opponent_roster,
            ) as r:
                ranges = sm.reel.source_ranges()
                spans = r.segment_offsets(ranges)
                part = r.render(
                    part,
                    settings=part_settings,
                    progress=on_progress,
                    source_frame_ranges=ranges,
                    cancel_check=cancel_check,
                    captions=_reel_captions(sm.reel, spans, home, away),
                )
            done[0] += sm.reel.total_frames
            duration, part_fps, has_audio = season.probe_part(part)
            if duration <= 0:
                logger.warning("season reel: empty part for %s, left out", where)
                continue
            fps = max(fps, part_fps)

            card = work / f"card_{i:03d}.png"
            img = _card(team, sm, roster, label, part, jersey)
            img.save(card)
            at = sum(p.duration for p in pieces)
            pieces.append(season.Piece(card, True, season.CARD_SECONDS))
            pieces.append(season.Piece(part, False, duration, has_audio))
            plays = len(sm.reel.segments)
            lines.append(
                f"{_timestamp(at)} {paths.format_date_for_template(sm.date)}"
                f" vs {sm.m.opponent or sm.match}"
                f" ({plays} play{'' if plays == 1 else 's'})"
            )

        if not pieces:
            raise RuntimeError("Every match rendered empty; nothing to join")

        # A source at 59.94 and another at 29.97 meet at the faster rate:
        # repeating frames costs nothing visible, dropping them does.
        fps = min(fps or 30.0, 60.0)
        season.join(
            pieces,
            output,
            settings,
            fps=fps,
            progress=lambda f: say(80.0 + f * 19.0, f"Joining {len(matches)} matches", "encode"),
            cancel_check=cancel_check,
        )
        output.with_suffix(output.suffix + ".chapters.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        JOBS.update(job.id, phase="thumbnail", message="building thumbnail")
        _season_thumbnail(
            team, roster, jersey, label, len(matches), matches[0].date[:4], output
        )
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    skipped = f"; {len(no_footage)} match(es) without footage left out" if no_footage else ""
    JOBS.update(job.id, message=f"{label}: {len(matches)} matches{skipped}")
    JOBS.mark_done(job.id, rel)


def season_remote_path(team: str, roster: Roster, file_path: Path, jersey: Optional[int]) -> str:
    """Where a season reel lands on OneDrive.

    The team's folder template, up to the first segment that names
    something only a match has (tournament, date, opponent...), then a
    "Season highlights" folder: the reels sit beside the season's
    tournaments rather than inside one of them.
    """
    from .render_job import _clean_value, _safe_segment_or_blank

    match_only = ("{tournament", "{date", "{opponent", "{match")
    team_name = _clean_value(roster.team_name or team.replace("_", " "))
    kept: list[str] = []
    for seg in (roster.upload.onedrive_folder or "").split("/"):
        if any(tok in seg for tok in match_only):
            break
        seg = seg.replace("{team}", team_name)
        cleaned = _safe_segment_or_blank(seg)
        if cleaned:
            kept.append(cleaned)
    kept.append("Season highlights")
    label, _ = player_label(roster, jersey) if jersey is not None else ("", "")
    name = _safe_segment_or_blank(f"{label}. {team_name}. Season Highlight Reel".lstrip(". "))
    return "/".join(kept + [f"{name}{file_path.suffix}"])


def run_season_upload(job: RenderJob, cancel_check) -> None:
    from ..upload import onedrive
    from .render_job import _fmt_mb, _write_onedrive_sidecar

    payload = job.payload or {}
    rel = str(payload.get("filename") or "")
    root = paths.season_reels_dir(job.team)
    file_path = (root / rel).resolve()
    if not rel or root.resolve() not in file_path.parents or not file_path.is_file():
        raise RuntimeError(f"Season reel not found: {rel}")
    roster = scanner.load_team_roster(job.team)
    jersey, _ = paths.parse_player_folder(Path(rel).parts[0])
    remote = season_remote_path(job.team, roster, file_path, jersey)

    last_emit = [0.0]

    def on_progress(pct: float, done: int, total: int) -> None:
        now = time.time()
        if now - last_emit[0] > 0.4:
            JOBS.update(job.id, percent=pct, phase="uploading",
                        message=f"{_fmt_mb(done)} / {_fmt_mb(total)}")
            last_emit[0] = now

    logger.info("season reel upload %s -> OneDrive: %s", job.id, remote)
    result = onedrive.upload_file(
        file_path=file_path,
        remote_path=remote,
        share=roster.upload.onedrive_share_links,
        progress_cb=on_progress,
        cancel_check=cancel_check,
    )
    thumb = thumbnail_path(file_path)
    thumbed = bool(result.item_id and thumb.is_file() and onedrive.set_thumbnail(result.item_id, thumb))
    described = False
    chapters = file_path.with_suffix(file_path.suffix + ".chapters.txt")
    if result.item_id and chapters.is_file():
        described = onedrive.set_description(
            result.item_id, chapters.read_text(encoding="utf-8").strip()
        )
    _write_onedrive_sidecar(file_path, result, is_reel=True, thumbed=thumbed, described=described)
    JOBS.update(job.id, message="Uploaded to OneDrive")
    JOBS.mark_done(job.id, result.share_url or result.web_url or "")
