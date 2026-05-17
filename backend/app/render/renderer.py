"""Render a `Match` to an output video file using MoviePy + Pillow overlays."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from moviepy import AudioClip, VideoClip, VideoFileClip

from ..domain.match import Match
from ..domain.roster import Roster
from .frame_map import FrameEntry, FrameMap
from .frame_map_builder import build_frame_map
from .overlays.message import MessageOverlayRenderer
from .overlays.scoreboard import ScoreboardOverlay, TeamBranding
from .settings import load_settings

logger = logging.getLogger(__name__)


@dataclass
class RenderProgress:
    frames_done: int
    frames_total: int
    elapsed_seconds: float
    phase: str  # "video" | "audio" | "encode"

    @property
    def percent(self) -> float:
        return 100.0 * self.frames_done / max(1, self.frames_total)


ProgressCb = Callable[[RenderProgress], None]
CancelCheck = Callable[[], bool]


class RenderCancelled(RuntimeError):
    """Raised from inside make_frame/make_audio when the caller asks to stop."""


class MatchRenderer:
    """Render a Match into an mp4 file."""

    def __init__(
        self,
        match: Match,
        media_dir: Path,
        home: TeamBranding,
        away: TeamBranding,
        *,
        home_roster: Optional[Roster] = None,
        away_roster: Optional[Roster] = None,
        skip_overlays: bool = False,
    ) -> None:
        self.match = match
        self.media_dir = Path(media_dir)
        self.home = home
        self.away = away
        self.skip_overlays = skip_overlays

        self._clips: dict[str, VideoFileClip] = {}
        self._scoreboard = ScoreboardOverlay(home, away)
        self._messages = MessageOverlayRenderer(
            home_roster=home_roster,
            away_roster=away_roster or match.opponent_roster,
            home=home,
            away=away,
        )

    def __enter__(self) -> "MatchRenderer":
        for clip in self.match.clips:
            self._clips[clip.id] = VideoFileClip(str(self.media_dir / clip.filename))
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        for c in self._clips.values():
            try:
                c.close()
            except Exception:
                pass
        self._clips.clear()

    # --------------------------------------------------------------

    def render(
        self,
        output_path: str | Path,
        *,
        settings: Optional[dict[str, Any]] = None,
        progress: Optional[ProgressCb] = None,
        source_frame_range: Optional[tuple[int, int]] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> Path:
        """Render the match to ``output_path``.

        ``source_frame_range`` (start, end) - inclusive start, exclusive end -
        in source/global frame coordinates. When provided, only the output
        frames whose primary input falls in that source range are rendered.
        Used to produce a preview around the playhead.
        """
        if settings is None:
            settings = load_settings()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fmap = build_frame_map(self.match)
        if len(fmap) == 0:
            raise ValueError("Frame map is empty (no frames to render)")

        # The FrameMap has one entry per source frame, so the output must run
        # at the source clip's fps to play back at the correct speed. Falling
        # back to `match.fps` only if no clips loaded (shouldn't happen here).
        if self.match.clips and self.match.clips[0].fps > 0:
            fps = self.match.clips[0].fps
        else:
            fps = self.match.fps or 30.0
        clip_offsets = self._clip_offsets()

        if source_frame_range is None:
            out_start, out_end = 0, len(fmap)
        else:
            out_start, out_end = self._source_range_to_output_range(
                fmap, source_frame_range, clip_offsets
            )
            if out_end <= out_start:
                raise ValueError(
                    f"Preview frame range {source_frame_range} maps to an empty "
                    f"output slice (entire range may fall inside cut regions)"
                )
            logger.info(
                "preview slice: source %s -> output frames [%d, %d) of %d",
                source_frame_range, out_start, out_end, len(fmap),
            )

        total = out_end - out_start
        duration = total / fps
        size = self._size()

        start_time = time.time()
        frames_done = [0]

        def make_frame(t: float) -> np.ndarray:
            if cancel_check is not None and cancel_check():
                raise RenderCancelled("Render cancelled by user")
            i = min(out_start + int(t * fps), out_end - 1)
            entry = fmap[i]
            frame = self._compose_frame(entry, size, clip_offsets)
            frames_done[0] += 1
            if progress and frames_done[0] % 30 == 0:
                progress(
                    RenderProgress(
                        frames_done=frames_done[0],
                        frames_total=total,
                        elapsed_seconds=time.time() - start_time,
                        phase="video",
                    )
                )
            return frame

        video = VideoClip(make_frame, duration=duration).with_fps(fps)

        try:
            if settings.get("audio") and self._any_clip_has_audio():
                audio = self._build_audio(
                    fmap, fps, duration, out_start, out_end, cancel_check=cancel_check
                )
                if audio is not None:
                    video = video.with_audio(audio)

            write_args = _build_write_args(settings, fps)
            video.write_videofile(str(output_path), **write_args)
        finally:
            video.close()

        if progress:
            progress(
                RenderProgress(
                    frames_done=total,
                    frames_total=total,
                    elapsed_seconds=time.time() - start_time,
                    phase="encode",
                )
            )
        return output_path

    def _source_range_to_output_range(
        self,
        fmap: FrameMap,
        source_range: tuple[int, int],
        clip_offsets: dict[str, int],
    ) -> tuple[int, int]:
        """Find the contiguous output slice covering a source frame range.

        Walks the FrameMap once, using each entry's *primary* input to map
        to a source frame. Frames inside cut regions are absent from the
        FrameMap, so a source range straddling a cut just yields a slightly
        shorter output slice.
        """
        src_start, src_end = source_range
        out_start: Optional[int] = None
        out_end: Optional[int] = None
        for i, entry in enumerate(fmap):
            if not entry.inputs:
                continue
            inp = entry.inputs[0]
            src = clip_offsets.get(inp.clip_id, 0) + inp.local_frame
            if src < src_start:
                continue
            if src >= src_end:
                break
            if out_start is None:
                out_start = i
            out_end = i + 1
        if out_start is None:
            return (0, 0)
        return (out_start, out_end or out_start)

    # --------------------------------------------------------------

    def _size(self) -> tuple[int, int]:
        for clip in self.match.clips:
            if clip.id in self._clips:
                w, h = self._clips[clip.id].size
                return (int(w), int(h))
        return (1920, 1080)

    def _clip_offsets(self) -> dict[str, int]:
        return {c.id: (self.match.clip_offset(c.id) or 0) for c in self.match.clips}

    def _any_clip_has_audio(self) -> bool:
        return any(c.audio is not None for c in self._clips.values())

    def _compose_frame(
        self,
        entry: FrameEntry,
        size: tuple[int, int],
        clip_offsets: dict[str, int],
    ) -> np.ndarray:
        w, h = size
        if not entry.inputs:
            return np.zeros((h, w, 3), dtype=np.uint8)

        accum = np.zeros((h, w, 3), dtype=np.float32)
        primary_global_frame: Optional[int] = None

        for inp in entry.inputs:
            clip = self._clips.get(inp.clip_id)
            if clip is None:
                continue
            t = max(0.0, min(inp.local_frame / clip.fps, clip.duration - 1e-3))
            arr = clip.get_frame(t).astype(np.float32)
            if arr.shape[:2] != (h, w):
                arr = _letterbox(arr, w, h)
            accum += arr * inp.weight
            if primary_global_frame is None:
                primary_global_frame = clip_offsets.get(inp.clip_id, 0) + inp.local_frame

        # Apply scoreboard overlay (before color blend so the scoreboard fades along
        # with the underlying frame during transitions).
        if (
            not self.skip_overlays
            and entry.scoreboard_visible
            and primary_global_frame is not None
        ):
            state = self.match.state_at_global_frame(primary_global_frame)
            scoreboard_frame = self._scoreboard.apply(accum.astype(np.uint8), state).astype(
                np.float32
            )
            accum = scoreboard_frame

        if entry.color_weight > 0.0:
            cw = float(min(1.0, max(0.0, entry.color_weight)))
            color = np.array(entry.blend_color, dtype=np.float32)
            accum = accum * (1.0 - cw) + color * cw

        out = np.clip(accum, 0, 255).astype(np.uint8)

        # Message overlays applied last so they aren't dimmed by transitions.
        if not self.skip_overlays and entry.messages:
            out = self._messages.apply(out, entry.messages)

        return out

    def _build_audio(
        self,
        fmap: FrameMap,
        fps: float,
        duration: float,
        out_start: int = 0,
        out_end: Optional[int] = None,
        *,
        cancel_check: Optional[CancelCheck] = None,
    ) -> Optional[AudioClip]:
        clips_audio = {cid: c for cid, c in self._clips.items() if c.audio is not None}
        if not clips_audio:
            return None

        first = next(iter(clips_audio.values())).audio
        audio_fps = first.fps
        nchannels = first.nchannels
        slice_end = out_end if out_end is not None else len(fmap)
        slice_len = slice_end - out_start  # number of output frames

        def make_audio(t):
            if cancel_check is not None and cancel_check():
                raise RenderCancelled("Render cancelled by user")
            arr = np.atleast_1d(np.asarray(t))
            n = arr.shape[0]
            out = np.zeros((n, nchannels), dtype=np.float32)
            # t is local to the output (slice) timeline; offset into fmap.
            local_frame_idx = np.clip((arr * fps).astype(int), 0, slice_len - 1)
            frame_idx = local_frame_idx + out_start

            for fi in np.unique(frame_idx):
                mask = frame_idx == fi
                entry = fmap[fi]
                samples = np.zeros((mask.sum(), nchannels), dtype=np.float32)
                t_offsets = arr[mask] - ((fi - out_start) / fps)
                for inp in entry.inputs:
                    clip = clips_audio.get(inp.clip_id)
                    if clip is None or clip.audio is None:
                        continue
                    base_t = inp.local_frame / clip.fps
                    sample_times = np.clip(base_t + t_offsets, 0, clip.audio.duration - 1e-3)
                    try:
                        s = clip.audio.get_frame(sample_times)
                        if s.ndim == 1:
                            s = s.reshape(-1, 1)
                            if nchannels > 1:
                                s = np.tile(s, (1, nchannels))
                    except Exception:
                        continue
                    samples += s.astype(np.float32) * inp.weight
                if entry.color_weight > 0.0:
                    samples *= 1.0 - float(entry.color_weight)
                out[mask] = samples

            return out if n > 1 else out[0]

        return AudioClip(make_audio, duration=duration, fps=audio_fps)


def _letterbox(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    """Resize an arbitrary-sized frame into (h,w,3), letterboxing on mismatch."""
    from PIL import Image

    img = Image.fromarray(arr.astype(np.uint8))
    img.thumbnail((w, h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return np.array(canvas).astype(np.float32)


def _build_write_args(settings: dict[str, Any], fps: float) -> dict[str, Any]:
    args: dict[str, Any] = {
        "codec": settings.get("codec", "libx264"),
        "fps": fps,
        "logger": None,
    }
    for key in ("preset", "threads", "bitrate", "ffmpeg_params", "audio_codec", "audio_bitrate"):
        if settings.get(key):
            args[key] = settings[key]
    return args
