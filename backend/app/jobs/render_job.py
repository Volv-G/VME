"""Execute a single render job synchronously.

Called by the dispatcher thread (one job at a time). The previous
`kick_off_render(...)` helper that spawned its own thread is gone -
concurrency is now owned by the dispatcher, not individual job calls.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..library import paths, scanner
from ..render import naming as render_naming
from ..render.batch import (
    BatchClip,
    focused_from_match,
    highlights_from_match,
)
from ..render.overlays.scoreboard import TeamBranding
from ..render.reels import build_chapters, reels_from_match
from ..render.renderer import MatchRenderer, RenderCancelled, RenderProgress
from ..render.settings import apply_container_extension
from ..render.thumbnail import ThumbnailSpec, thumbnail_path, write_thumbnail
from .manager import JOBS, RenderJob

# Upload module is imported lazily inside `_run_upload` so the queue
# can still operate (and the dispatcher start cleanly) on installs that
# don't have the google-api-python-client stack.

logger = logging.getLogger(__name__)


# Default half-window used for previews when the client doesn't override it.
DEFAULT_PREVIEW_SECONDS_AROUND = 30.0


def _team_branding(job: RenderJob, m, home_roster) -> tuple[TeamBranding, TeamBranding]:
    """Scoreboard branding for a job (see `_branding`)."""
    return _branding(job.team, job.tournament, job.date, job.match, m, home_roster)


def _branding(
    team: str, tournament: str, date: str, match: str, m, home_roster
) -> tuple[TeamBranding, TeamBranding]:
    """Scoreboard branding for both sides: name, color and logo file.

    Logos are stored as a filename relative to the roster's own folder -
    the team folder for our roster, the match folder for the opponent's
    (each match has its own opponent). Resolved to absolute paths here so
    the overlay never has to know the media layout. A recorded-but-missing
    file resolves to None and simply renders without a logo.
    """
    home_logo = _resolve_logo(
        paths.team_dir(team),
        home_roster.team_logo_path,
        paths.TEAM_LOGO_STEM,
    )
    away_logo = _resolve_logo(
        paths.match_dir(team, tournament, date, match),
        m.opponent_roster.team_logo_path,
        paths.OPPONENT_LOGO_STEM,
    )
    home = TeamBranding(
        name=home_roster.team_name or team,
        color=home_roster.team_color or "#2d8a4e",
        logo_path=home_logo,
        player_photos=_player_photos(team, home_roster),
    )
    away = TeamBranding(
        name=m.opponent_roster.team_name or m.opponent or "Away",
        color=m.opponent_roster.team_color or "#8a2d2d",
        logo_path=away_logo,
    )
    return home, away


def _resolve_logo(
    directory: Path, filename: Optional[str], stem: str
) -> Optional[str]:
    """Absolute path of a stored logo, or None.

    Falls back to scanning for `<stem>.<ext>` so a logo dropped into the
    folder by hand (or a roster written before the field existed) is
    still picked up.
    """
    if filename:
        candidate = directory / Path(filename).name
        if candidate.is_file():
            return str(candidate)
    found = paths.find_logo(directory, stem)
    return str(found) if found is not None else None



# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------


def generate_thumbnail(
    team: str,
    tournament: str,
    date: str,
    match: str,
    render_path: Path,
    *,
    m=None,
    home_roster=None,
) -> Optional[Path]:
    """Write `<render>.thumbnail.jpg` for an existing render file.

    Single source of truth for thumbnails: called right after a render
    finishes AND by the "regenerate" endpoint, so a regenerated image is
    identical to what the render would have produced (modulo colors /
    logos the user changed in between - which is the whole point of
    being able to regenerate).

    The subject line and score are inferred from the render's location:
    a file under `reels/<team>/<player>/` is one player's highlight reel
    and gets their name instead of the match result.

    Returns the sidecar path, or None if anything went wrong - thumbnail
    failures never fail a render.
    """
    try:
        if m is None:
            m = scanner.load_or_create_match(team, tournament, date, match)
        if home_roster is None:
            home_roster = scanner.load_team_roster(team)
        home, away = _branding(team, tournament, date, match, m, home_roster)

        renders_root = paths.renders_dir(team, tournament, date, match)
        try:
            rel_parts = render_path.relative_to(renders_root).parts
        except ValueError:
            rel_parts = (render_path.name,)

        subject = ""
        home_image = home.logo_path
        home_badge = ""
        is_photo = False
        if len(rel_parts) > 2 and rel_parts[0] == "reels":
            jersey, player_name = paths.parse_player_folder(rel_parts[2])
            label = f"#{jersey} {player_name}".strip() if jersey is not None else player_name
            subject = f"{label}  ·  Highlights" if label else "Highlights"
            # A reel is about the player, not the club: their photo takes
            # the home crest's place, and failing that a jersey-number
            # badge - so twelve reels from one match are still tellable
            # apart at thumbnail size.
            home_image = _resolve_player_photo(team, home_roster, jersey)
            is_photo = home_image is not None
            if home_image is None and jersey is not None:
                home_badge = f"#{jersey}"

        spec = ThumbnailSpec(
            home_name=home.name,
            away_name=away.name,
            home_color=home.color,
            away_color=away.color,
            home_logo=home_image,
            away_logo=away.logo_path,
            home_badge=home_badge,
            home_is_photo=is_photo,
            caption=_thumbnail_caption(team, tournament, date, match, m, home_roster),
            subject=subject,
            backdrop=_grab_backdrop(render_path),
        )
        return write_thumbnail(render_path, spec)
    except Exception:
        logger.warning(
            "thumbnail generation failed for %s", render_path, exc_info=True
        )
        return None


def _player_photos(team: str, roster) -> dict[int, str]:
    """Jersey number -> absolute photo path, for players who have one.

    Resolved once per render rather than per popup: the popup overlay
    runs per frame and has no idea where the media tree lives.
    """
    out: dict[int, str] = {}
    if roster is None:
        return out
    for player in roster.players:
        found = _resolve_player_photo(team, roster, player.number)
        if found:
            out[player.number] = found
    return out


def _resolve_player_photo(team: str, roster, jersey: Optional[int]) -> Optional[str]:
    """Absolute path of a player's photo, or None.

    Falls back to scanning `players/<NN>.<ext>` so a photo dropped in by
    hand is picked up even when the roster entry wasn't updated.
    """
    if jersey is None:
        return None
    player = roster.find_by_number(jersey)
    if player is not None and player.profile_pic_path:
        candidate = paths.player_photo_file(team, player.profile_pic_path)
        if candidate.is_file():
            return str(candidate)
    found = paths.find_logo(
        paths.player_photo_dir(team), paths.player_photo_stem(jersey)
    )
    return str(found) if found is not None else None


def _thumbnail_caption(team, tournament, date, match, m, roster) -> str:
    """Top strip: date, tournament abbreviation, match number.

    Reuses the upload template variables so the thumbnail says exactly
    what the video title says (same date format, same abbreviation).
    """
    try:
        from ..upload.templates import build_vars

        v = build_vars(
            team=team,
            tournament=tournament,
            date=date,
            match=match,
            match_obj=m,
            roster=roster,
            tournament_info=scanner.load_tournament_info(team, tournament),
        )
        bits = [v.date, v.tournament_abbr]
        if v.match_index and v.match_index != "?":
            bits.append(f"Match {v.match_index}")
        return "  ·  ".join(b for b in bits if b)
    except Exception:
        logger.debug("could not build thumbnail caption", exc_info=True)
        return paths.format_date_for_template(date)


def _grab_backdrop(render_path: Path):
    """One representative frame from the render, as an RGB array.

    Taken at 42% of the duration - far enough in to be actual play (a
    match render opens on warmups / an empty court) without being the
    post-match handshake. Returns None on any failure; the thumbnail
    then uses a flat background.
    """
    try:
        from moviepy import VideoFileClip

        with VideoFileClip(str(render_path)) as clip:
            t = max(0.0, min(clip.duration * 0.42, clip.duration - 0.1))
            return clip.get_frame(t)
    except Exception:
        logger.warning(
            "could not read a backdrop frame from %s", render_path, exc_info=True
        )
        return None


def run_render(job: RenderJob) -> None:
    """Run `job` synchronously in the current thread.

    All status transitions go through `JOBS.update(...)`; the dispatcher
    is responsible for catching exceptions and translating them into a
    failed status. We do still catch `RenderCancelled` here because we
    need to clean up the partial output file before re-raising.

    Dispatch on `job.kind`:
      - "full" / "preview" -> single-output path via MatchRenderer.
      - "highlights" / "focused_highlights" -> batch path producing one
        file per detected span (see `app/render/batch.py`).
      - "player_reels" -> one file per PLAYER, all of their plays
        concatenated, plus a chapter sidecar (see `app/render/reels.py`).
        Deliberately separate from "highlights": both can be run on the
        same match and neither replaces the other.
    """
    JOBS.mark_started(job.id)

    cancel_event = job.cancel_event

    def cancel_check() -> bool:
        return cancel_event.is_set()

    # Uploads don't need a Match loaded - they only need the existing
    # render file on disk - so dispatch them before the match-load.
    if job.kind == "youtube_upload":
        _run_upload(job, cancel_check)
        return

    m = scanner.load_or_create_match(job.team, job.tournament, job.date, job.match)
    if not m.clips:
        raise RuntimeError("Match has no clips to render")

    if job.kind in ("highlights", "focused_highlights"):
        _run_batch(job, m, cancel_check)
        return

    if job.kind == "player_reels":
        _run_player_reels(job, m, cancel_check)
        return

    home_roster = scanner.load_team_roster(job.team)
    home, away = _team_branding(job, m, home_roster)

    # Resolve a source frame range when a playhead_frame was supplied.
    source_frame_range: Optional[tuple[int, int]] = None
    if job.playhead_frame is not None:
        half = float(
            job.seconds_around
            if job.seconds_around is not None
            else DEFAULT_PREVIEW_SECONDS_AROUND
        )
        half_frames = int(round(half * (m.fps or 30.0)))
        total_src = m.total_frames()
        start = max(0, job.playhead_frame - half_frames)
        end = min(total_src, job.playhead_frame + half_frames)
        if end <= start:
            raise RuntimeError(
                "Preview window is empty (check playhead_frame / seconds_around)"
            )
        source_frame_range = (start, end)
        logger.info(
            "preview render: playhead=%d half=%.1fs -> source frames [%d, %d)",
            job.playhead_frame,
            half,
            start,
            end,
        )

    renders = paths.renders_dir(job.team, job.tournament, job.date, job.match)
    renders.mkdir(parents=True, exist_ok=True)

    # Resolve the output filename via the team's full-render template.
    # The default template (set in NamingConfig) is `{label}_{timestamp}.mp4`,
    # which reproduces the legacy behavior verbatim. We still wrap in a
    # try/except so a broken user template never crashes the dispatcher -
    # we fall back to the default in that case and log the failure.
    tournament_info = scanner.load_tournament_info(job.team, job.tournament)
    full_vars = render_naming.build_full_render_vars(
        team=job.team,
        tournament=job.tournament,
        date=job.date,
        match=job.match,
        match_obj=m,
        home_team_name=home_roster.team_name or job.team,
        tournament_abbreviation=tournament_info.abbreviation or "",
        tournament_full_name=tournament_info.full_name or "",
        label=job.label or "render",
        timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    try:
        output_filename = render_naming.render_naming_template(
            home_roster.naming.full_render_template, full_vars
        )
    except render_naming.TemplateError as exc:
        logger.warning(
            "full-render template %r failed (%s); falling back to default",
            home_roster.naming.full_render_template,
            exc,
        )
        output_filename = render_naming.render_naming_template(
            render_naming.DEFAULT_FULL_RENDER_TEMPLATE, full_vars
        )

    # Previews are temporary scratch outputs - their filenames carry a
    # `preview_` prefix so they're visually distinct from full renders
    # AND so `_is_full_render` (scanner.py) excludes them from team
    # dashboards / YouTube upload eligibility. The team's template might
    # not include `{label}` at all, so we enforce the prefix here on the
    # FINAL basename rather than relying on template content.
    if job.kind == "preview":
        parts = output_filename.rsplit("/", 1)
        leaf = parts[-1]
        if not leaf.lower().startswith("preview_"):
            parts[-1] = f"preview_{leaf}"
            output_filename = "/".join(parts)

    # Naming templates spell out an extension (legacy default: .mp4) but
    # the actual container comes from render_settings.json (default:
    # QuickTime/.mov for DNxHR), so normalize it here - the renderer does
    # the same to the real path and this keeps the recorded filename,
    # download link and file on disk in sync.
    output_filename = apply_container_extension(output_filename)

    output_path = renders / output_filename
    # Templates may carry forward slashes (subfolders) - ensure the
    # destination directory exists before the renderer tries to write.
    output_path.parent.mkdir(parents=True, exist_ok=True)

    media_dir = paths.match_dir(job.team, job.tournament, job.date, job.match)

    last_pct = [0.0]
    last_emit = [time.time()]

    def on_progress(p: RenderProgress) -> None:
        now = time.time()
        if p.phase == "audio":
            # MoviePy writes the whole audio track before the first video
            # frame; leave the percent bar alone (it tracks video frames)
            # and just say what's happening so the job doesn't look hung.
            if now - last_emit[0] > 0.4:
                JOBS.update(
                    job.id,
                    phase="audio",
                    message=f"writing audio track {p.percent:.0f}%",
                )
                last_emit[0] = now
            return
        if p.percent - last_pct[0] >= 0.5 or now - last_emit[0] > 0.4:
            JOBS.update(
                job.id,
                percent=p.percent,
                phase=p.phase,
                message=f"{p.frames_done}/{p.frames_total} frames",
            )
            last_pct[0] = p.percent
            last_emit[0] = now

    try:
        with MatchRenderer(
            m,
            media_dir,
            home=home,
            away=away,
            home_roster=home_roster,
            away_roster=m.opponent_roster,
        ) as r:
            r.render(
                output_path,
                progress=on_progress,
                source_frame_range=source_frame_range,
                cancel_check=cancel_check,
            )
    except RenderCancelled:
        # Clean up the partial output before letting the dispatcher mark
        # the job cancelled.
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "failed to remove partial render output: %s", output_path
            )
        raise

    # Thumbnail last, and only for shareable output: previews are
    # scratch. Generation reads one frame back off the finished file, so
    # it has to happen after the encode, and it's best-effort - the
    # render is already done and successful either way.
    if job.kind != "preview":
        JOBS.update(job.id, phase="thumbnail", message="building thumbnail")
        generate_thumbnail(
            job.team,
            job.tournament,
            job.date,
            job.match,
            output_path,
            m=m,
            home_roster=home_roster,
        )

    # `output_filename` may be a relative subpath (e.g. `pre/foo.mp4`)
    # when the template puts the file in a subfolder. We keep it relative
    # to the renders root because the download endpoint resolves
    # filenames relative to that root via its {filename:path} converter.
    JOBS.mark_done(job.id, output_filename)


# ---------------------------------------------------------------------------
# Batch path (highlights, focused_highlights)
# ---------------------------------------------------------------------------


def _run_batch(
    job: RenderJob,
    m,  # Match
    cancel_check,  # Callable[[], bool]
) -> None:
    """Render one mp4 per detected span; report aggregate progress.

    Output goes under the match's renders folder in a `highlights/` or
    `focused/` subtree, organized by team and player. The job's
    `output_filename` is set to the first produced file so the UI's
    download link still works; the full list is discoverable via the
    recursive `/renders` listing.
    """
    home_roster = scanner.load_team_roster(job.team)
    home, away = _team_branding(job, m, home_roster)

    # Detect spans up front so we know `total` for progress reporting and
    # can fail fast if there's nothing to render. We thread the team's
    # naming templates + tournament info through so the per-clip paths
    # respect user-configured output naming.
    tournament_info = scanner.load_tournament_info(job.team, job.tournament)
    batch_kwargs = dict(
        team=job.team,
        tournament=job.tournament,
        date=job.date,
        match_name=job.match,
        tournament_info=tournament_info,
        naming_config=home_roster.naming,
    )
    if job.kind == "highlights":
        clips = highlights_from_match(
            m, home_roster, home_roster.team_name or job.team, **batch_kwargs
        )
        what = "highlight"
    elif job.kind == "focused_highlights":
        clips = focused_from_match(
            m, home_roster, home_roster.team_name or job.team, **batch_kwargs
        )
        what = "focused clip"
    else:
        raise RuntimeError(f"Unknown batch kind: {job.kind!r}")

    if not clips:
        JOBS.update(
            job.id,
            message=f"No {what}s found in this match",
        )
        # Treat "nothing to do" as success - the job ran to completion,
        # there was just no output. Avoids a confusing FAILED row when
        # the user runs highlights on an unannotated match.
        JOBS.mark_done(job.id, "")
        return

    media_dir = paths.match_dir(job.team, job.tournament, job.date, job.match)
    renders_root = paths.renders_dir(
        job.team, job.tournament, job.date, job.match
    )
    renders_root.mkdir(parents=True, exist_ok=True)

    # Aggregate progress: total output frames across all sub-clips, plus
    # a running tally of frames committed by sub-clips that have already
    # finished. The renderer's per-frame progress callback adds to this
    # so the bar moves smoothly across the whole batch.
    total_frames = sum(max(0, c.end_frame - c.start_frame) for c in clips)
    done_before_current = [0]
    current_total = [0]
    last_pct = [0.0]
    last_emit = [time.time()]

    def on_progress(p: RenderProgress) -> None:
        # `p.frames_done` is local to the current sub-clip. We re-base it
        # onto the batch total. Throttle SSE updates the same way the
        # single-render path does to avoid spamming the manager.
        now = time.time()
        if p.phase == "audio":
            if now - last_emit[0] > 0.4:
                JOBS.update(
                    job.id,
                    phase="audio",
                    message=(
                        f"[{current_total[0] + 1}/{len(clips)}] "
                        f"writing audio track {p.percent:.0f}%"
                    ),
                )
                last_emit[0] = now
            return
        overall = (done_before_current[0] + p.frames_done) / max(1, total_frames) * 100.0
        if overall - last_pct[0] >= 0.5 or now - last_emit[0] > 0.4:
            JOBS.update(
                job.id,
                percent=overall,
                phase=p.phase,
                message=(
                    f"{done_before_current[0] + p.frames_done}"
                    f"/{total_frames} frames "
                    f"({current_total[0]} clip(s) done of {len(clips)})"
                ),
            )
            last_pct[0] = overall
            last_emit[0] = now

    first_output: Optional[str] = None

    with MatchRenderer(
        m,
        media_dir,
        home=home,
        away=away,
        home_roster=home_roster,
        away_roster=m.opponent_roster,
    ) as r:
        for i, bc in enumerate(clips, start=1):
            if cancel_check():
                raise RenderCancelled("Render cancelled by user")

            relative_path = apply_container_extension(bc.relative_path)
            output_path = renders_root / relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Brief status update so the UI shows which clip is starting
            # even before the first per-frame progress tick arrives.
            JOBS.update(
                job.id,
                message=f"[{i}/{len(clips)}] {bc.label}",
            )
            try:
                r.render(
                    output_path,
                    progress=on_progress,
                    source_frame_range=(bc.start_frame, bc.end_frame),
                    cancel_check=cancel_check,
                )
            except RenderCancelled:
                # Drop the partial sub-clip before re-raising. Earlier
                # completed clips stay on disk - they're independently
                # useful and re-running the batch is idempotent for
                # those (same filenames).
                try:
                    output_path.unlink(missing_ok=True)
                except Exception:
                    logger.warning(
                        "failed to remove partial batch output: %s",
                        output_path,
                    )
                raise

            done_before_current[0] += max(0, bc.end_frame - bc.start_frame)
            current_total[0] = i
            if first_output is None:
                first_output = relative_path

    # `output_filename` is used by the UI as a download link target; with
    # nested subfolders the relative_path is the right thing to record.
    JOBS.mark_done(job.id, first_output or "")


# ---------------------------------------------------------------------------
# Player reels
# ---------------------------------------------------------------------------


def _run_player_reels(
    job: RenderJob,
    m,  # Match
    cancel_check,  # Callable[[], bool]
) -> None:
    """Render one reel per player: all of that player's plays in one file.

    Each reel is a single encode of several source ranges concatenated
    (`MatchRenderer.render(source_frame_ranges=...)`), so chapter offsets
    are exact and there's no intermediate concat step.

    Alongside every reel we write a `<reel>.chapters.txt` sidecar holding
    the timestamp list. The YouTube upload path appends it to the video
    description, which is what makes a 12-minute reel navigable instead
    of forcing 11 separate uploads.
    """
    home_roster = scanner.load_team_roster(job.team)
    home, away = _team_branding(job, m, home_roster)

    tournament_info = scanner.load_tournament_info(job.team, job.tournament)
    reels = reels_from_match(
        m,
        home_roster,
        home_roster.team_name or job.team,
        team=job.team,
        tournament=job.tournament,
        date=job.date,
        match_name=job.match,
        tournament_info=tournament_info,
        naming_config=home_roster.naming,
    )
    if not reels:
        JOBS.update(job.id, message="No player events found in this match")
        JOBS.mark_done(job.id, "")
        return

    media_dir = paths.match_dir(job.team, job.tournament, job.date, job.match)
    renders_root = paths.renders_dir(
        job.team, job.tournament, job.date, job.match
    )
    renders_root.mkdir(parents=True, exist_ok=True)

    fps = m.clips[0].fps if m.clips and m.clips[0].fps > 0 else (m.fps or 30.0)
    total_frames = sum(r.total_frames for r in reels)
    done_before_current = [0]
    current_total = [0]
    last_pct = [0.0]
    last_emit = [time.time()]

    def on_progress(p: RenderProgress) -> None:
        now = time.time()
        if p.phase == "audio":
            if now - last_emit[0] > 0.4:
                JOBS.update(
                    job.id,
                    phase="audio",
                    message=(
                        f"[{current_total[0] + 1}/{len(reels)}] "
                        f"writing audio track {p.percent:.0f}%"
                    ),
                )
                last_emit[0] = now
            return
        overall = (
            (done_before_current[0] + p.frames_done)
            / max(1, total_frames)
            * 100.0
        )
        if overall - last_pct[0] >= 0.5 or now - last_emit[0] > 0.4:
            JOBS.update(
                job.id,
                percent=overall,
                phase=p.phase,
                message=(
                    f"{done_before_current[0] + p.frames_done}/{total_frames} "
                    f"frames ({current_total[0]} reel(s) done of {len(reels)})"
                ),
            )
            last_pct[0] = overall
            last_emit[0] = now

    first_output: Optional[str] = None

    with MatchRenderer(
        m,
        media_dir,
        home=home,
        away=away,
        home_roster=home_roster,
        away_roster=m.opponent_roster,
    ) as r:
        for i, spec in enumerate(reels, start=1):
            if cancel_check():
                raise RenderCancelled("Render cancelled by user")

            relative_path = apply_container_extension(spec.relative_path)
            output_path = renders_root / relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            JOBS.update(
                job.id,
                message=f"[{i}/{len(reels)}] {spec.label}",
            )
            try:
                r.render(
                    output_path,
                    progress=on_progress,
                    source_frame_ranges=spec.source_ranges(),
                    cancel_check=cancel_check,
                )
            except RenderCancelled:
                try:
                    output_path.unlink(missing_ok=True)
                except Exception:
                    logger.warning(
                        "failed to remove partial reel output: %s", output_path
                    )
                raise

            _write_chapter_sidecar(r, spec, output_path, fps)
            generate_thumbnail(
                job.team,
                job.tournament,
                job.date,
                job.match,
                output_path,
                m=m,
                home_roster=home_roster,
            )

            done_before_current[0] += spec.total_frames
            current_total[0] = i
            if first_output is None:
                first_output = relative_path

    JOBS.mark_done(job.id, first_output or "")


def _write_chapter_sidecar(renderer, spec, output_path: Path, fps: float) -> None:
    """Write `<reel>.chapters.txt` next to a rendered reel.

    Offsets come from the renderer so they match the file exactly (a
    segment that fell entirely inside a cut region isn't in the output
    and must not shift every later timestamp).

    The list is written even when YouTube won't honor it as chapters
    (fewer than 3 plays, or a play shorter than the 10 s minimum):
    bare timestamps in a description are still auto-linked into seekable
    links, so they remain useful. The reason is recorded as a comment
    line so the user can see why the chapter bar is missing.
    """
    try:
        spans = renderer.segment_offsets(spec.source_ranges())
        chapters = build_chapters(spans, spec.segments, fps)
        if not chapters.lines:
            return
        body = chapters.as_text()
        if not chapters.valid:
            body += (
                "\n\n# NOTE: YouTube will not show a chapter bar for this "
                f"reel ({chapters.reason}); the timestamps above still work "
                "as clickable links in the description."
            )
            logger.info(
                "reel %s: chapters not YouTube-valid (%s)",
                output_path.name,
                chapters.reason,
            )
        output_path.with_suffix(output_path.suffix + ".chapters.txt").write_text(
            body + "\n", encoding="utf-8"
        )
    except Exception:
        # A missing chapter file must never fail an otherwise good render.
        logger.warning("could not write chapter sidecar for %s", output_path, exc_info=True)


# ---------------------------------------------------------------------------
# YouTube upload path
# ---------------------------------------------------------------------------


def _run_upload(job: RenderJob, cancel_check) -> None:
    """Upload a previously-rendered file to YouTube.

    Reads the upload metadata from `job.payload` (filename, title,
    description, privacy, playlist, tags). On success writes a
    `<file>.youtube.json` sidecar next to the render so the UI can
    show the upload state on subsequent listings, and stores the
    YouTube URL in `output_filename` so the existing job-done UI can
    surface it as a clickable link.

    Cancellation: the long upload loop polls `cancel_check()` between
    chunks (see `youtube.upload_video`). A cancelled upload aborts at
    the next chunk boundary; the partial YouTube upload session is
    abandoned (no recovery - YouTube doesn't expose a stable resume
    handle across process restarts).
    """
    payload = job.payload or {}
    filename = payload.get("filename")
    if not filename:
        raise RuntimeError("Upload job is missing `filename` in payload")
    title = payload.get("title") or ""
    description = payload.get("description") or ""
    privacy_status = payload.get("privacy_status") or "unlisted"
    playlist_id = payload.get("playlist_id") or None
    tags = list(payload.get("tags") or [])

    renders = paths.renders_dir(job.team, job.tournament, job.date, job.match)
    file_path = renders / filename
    if not file_path.is_file():
        raise RuntimeError(f"Render file not found: {file_path}")

    # NOTE: chapters are NOT merged here. The enqueue endpoint expands
    # the reel description template - which can position `{chapters}`
    # itself, or gets the block appended - so the persisted job already
    # carries the final strings. See
    # `api/renders.py::enqueue_youtube_upload`.

    # Lazy import: keeps the dispatcher healthy on machines without the
    # google-* deps installed.
    from ..upload.youtube import upload_video

    last_emit = [time.time()]
    last_pct = [0.0]

    def on_progress(pct: float, done: int, total: int) -> None:
        now = time.time()
        # Throttle to the same cadence as renders so SSE listeners get
        # a smooth bar without spamming the manager.
        if pct - last_pct[0] >= 0.5 or now - last_emit[0] > 0.4:
            JOBS.update(
                job.id,
                percent=pct,
                phase="uploading",
                message=f"{_fmt_mb(done)} / {_fmt_mb(total)}",
            )
            last_pct[0] = pct
            last_emit[0] = now

    # Make sure there IS a thumbnail to attach. Renders produced before
    # thumbnails existed - or by a build where generation failed - would
    # otherwise upload bare and need a manual regenerate per video.
    # Generated before the insert so the slow part (seeking a frame) is
    # not sitting between the upload finishing and the thumbnail call.
    if not thumbnail_path(file_path).is_file():
        JOBS.update(job.id, phase="thumbnail", message="Generating thumbnail")
        generate_thumbnail(job.team, job.tournament, job.date, job.match, file_path)

    logger.info(
        "upload job %s -> YouTube: file=%s privacy=%s playlist=%s",
        job.id,
        file_path,
        privacy_status,
        playlist_id,
    )
    result = upload_video(
        file_path=file_path,
        title=title,
        description=description,
        privacy_status=privacy_status,
        tags=tags,
        playlist_id=playlist_id,
        progress_cb=on_progress,
        cancel_check=cancel_check,
    )

    # Attach the thumbnail. `thumbnails.set` is a separate API call -
    # `videos.insert` has no thumbnail field - so this is the earliest
    # possible moment: right after the insert, as soon as there is a
    # video id. Best-effort, because custom thumbnails require a
    # verified channel and a 403 here must not fail an upload that
    # otherwise succeeded.
    thumbnail_synced = _attach_thumbnail(job, file_path, result.video_id)

    # Persist the upload record alongside the file. Subsequent listings
    # (team dashboard, render panel) pick this up via
    # `scanner.load_youtube_sidecar`. We record BOTH the privacy we
    # requested AND what YouTube actually applied - they can diverge
    # when the OAuth app is in Testing mode (see upload_video for the
    # full explanation), and showing the requested value would lie to
    # the user about the video's actual state.
    actual_privacy = result.actual_privacy_status or privacy_status
    scanner.save_youtube_sidecar(
        file_path,
        {
            "video_id": result.video_id,
            "video_url": result.video_url,
            "uploaded_at": time.time(),
            "title": title,
            "privacy_status": actual_privacy,
            "requested_privacy_status": privacy_status,
            "playlist_id": playlist_id,
            "thumbnail_synced": thumbnail_synced,
            # What YouTube is serving, so a later regenerate can tell
            # "the image changed" from "nothing to push".
            "thumbnail_digest": (
                scanner.file_digest(thumbnail_path(file_path))
                if thumbnail_synced
                else None
            ),
        },
    )
    # Surface the YouTube URL via `output_filename` so existing job-row
    # UI renders a clickable link (it already treats output_filename
    # as a download-link target). When YouTube downgraded the privacy
    # we also stash a hint on the job's `message` so the user sees the
    # mismatch in the queue UI without having to dig into the log file.
    if actual_privacy != privacy_status:
        JOBS.update(
            job.id,
            message=(
                f"Uploaded, but YouTube forced privacy={actual_privacy!r} "
                f"(requested {privacy_status!r}). OAuth app likely in "
                "Testing mode."
            ),
        )
    JOBS.mark_done(job.id, result.video_url)


def _attach_thumbnail(job: Optional[RenderJob], file_path: Path, video_id: str) -> bool:
    """Best-effort `thumbnails.set` for a render's sidecar image.

    Returns True when YouTube accepted it. Failures are logged, surfaced
    on the job message when there is one, and otherwise swallowed: the
    common cause is an unverified channel, which the user can fix later
    and re-push with the regenerate endpoint.
    """
    thumb = thumbnail_path(file_path)
    if not thumb.is_file():
        return False
    from ..upload.youtube import set_thumbnail

    try:
        set_thumbnail(video_id, thumb)
        logger.info("attached thumbnail %s to video %s", thumb.name, video_id)
        return True
    except Exception as exc:
        logger.warning(
            "could not set thumbnail for video %s: %s", video_id, exc
        )
        if job is not None:
            JOBS.update(
                job.id,
                message=f"Uploaded; thumbnail queued for retry ({exc})",
            )
        # The usual cause is YouTube's per-channel thumbnail rate limit,
        # which clears on its own. Hand it to the background worker
        # instead of making the user re-push twelve reels by hand.
        from ..upload.thumbnail_sync import WORKER as THUMBNAIL_SYNC

        THUMBNAIL_SYNC.wake()
        return False


def read_chapter_sidecar(file_path: Path) -> str:
    """Chapter/timestamp lines for a render, or "" when there are none.

    Comment lines (the "why no chapter bar" operator note written by
    `_write_chapter_sidecar`) are stripped - they're not for viewers.
    Public because the upload-enqueue endpoint needs them to expand the
    `{chapters}` placeholder.
    """
    sidecar = file_path.with_suffix(file_path.suffix + ".chapters.txt")
    if not sidecar.is_file():
        return ""
    try:
        lines = [
            ln.rstrip()
            for ln in sidecar.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
    except OSError:
        logger.warning("could not read chapter sidecar %s", sidecar, exc_info=True)
        return ""
    return "\n".join(lines)


def _fmt_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def kick_off_immediate(job: RenderJob) -> None:
    """Run `job` in a fresh daemon thread, independent of the dispatcher.

    Used for preview renders: the user wants instant feedback, so we
    don't make them wait for the queue to be active or for an in-flight
    full render to finish. The job is still tracked in the registry,
    persisted, and emits SSE progress like any other.

    Pre-conditions: `job.immediate` must be True. The dispatcher excludes
    immediate jobs from `next_pending()`, so there's no risk of the
    same job running twice.
    """
    if not job.immediate:
        raise ValueError(
            "kick_off_immediate called for a non-immediate job"
        )

    def _run() -> None:
        try:
            run_render(job)
        except RenderCancelled:
            JOBS.mark_cancelled(job.id)
        except Exception as exc:
            logger.exception("immediate render %s failed: %s", job.id, exc)
            JOBS.mark_failed(job.id, str(exc))

    threading.Thread(
        target=_run, name=f"render-immediate-{job.id}", daemon=True
    ).start()
