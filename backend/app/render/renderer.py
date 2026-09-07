"""Render a `Match` to an output video file using MoviePy + Pillow overlays."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np
from moviepy import AudioClip, VideoClip, VideoFileClip

from ..domain.match import Match
from ..domain.roster import Roster
from .frame_map import FrameEntry, FrameMap
from .frame_map_builder import build_frame_map
from .overlays.message import MessageOverlayRenderer
from .overlays.scoreboard import ScoreboardOverlay, TeamBranding
from .settings import apply_container_extension, load_settings, scale_filter

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
        source_frame_ranges: Optional[list[tuple[int, int]]] = None,
        cancel_check: Optional[CancelCheck] = None,
    ) -> Path:
        """Render the match to ``output_path``.

        ``source_frame_range`` (start, end) - inclusive start, exclusive end -
        in source/global frame coordinates. When provided, only the output
        frames whose primary input falls in that source range are rendered.
        Used to produce a preview around the playhead.

        ``source_frame_ranges`` renders SEVERAL ranges back-to-back into one
        file, in the order given - that's how player reels are produced.
        Ranges are honored as listed (no sorting, no merging), so the caller
        controls the running order and can compute chapter offsets from the
        same list.
        """
        if settings is None:
            settings = load_settings()
        # ffmpeg picks the muxer from the file extension, so force it to
        # match the configured container (default: QuickTime/.mov for the
        # DNxHR preset). Callers get the final path back from this method.
        output_path = apply_container_extension(Path(output_path), settings)
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

        # Everything below works off `sequence`: the output frame order,
        # expressed as indices into the FrameMap. A plain render is
        # `range(0, len(fmap))`, a preview is one slice of it, and a reel
        # is several slices concatenated. Using one representation keeps
        # make_frame / make_audio identical for all three.
        if source_frame_ranges:
            sequence, spans = self._concat_sequence(
                fmap, source_frame_ranges, clip_offsets
            )
            if not sequence:
                raise ValueError(
                    f"None of the {len(source_frame_ranges)} requested source "
                    "ranges maps to any output frames (all inside cut regions?)"
                )
            logger.info(
                "reel: %d/%d source ranges -> %d output frames",
                len(spans), len(source_frame_ranges), len(sequence),
            )
        elif source_frame_range is None:
            sequence = range(0, len(fmap))
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
            sequence = range(out_start, out_end)

        total = len(sequence)
        duration = total / fps
        size = self._size()

        start_time = time.time()
        frames_done = [0]

        def make_frame(t: float) -> np.ndarray:
            if cancel_check is not None and cancel_check():
                raise RenderCancelled("Render cancelled by user")
            i = sequence[min(int(t * fps), total - 1)]
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
                    fmap,
                    fps,
                    duration,
                    sequence,
                    cancel_check=cancel_check,
                    channels=settings.get("audio_channels"),
                )
                if audio is not None:
                    video = video.with_audio(audio)

            write_args = _build_write_args(settings, fps)
            # Preset asks for a smaller output than the source (HandBrake's
            # "maximum size"): let ffmpeg scale on the way out rather than
            # resampling every frame in Python.
            vf = scale_filter(size, settings)
            if vf:
                logger.info("scaling output %dx%d -> %s", size[0], size[1], vf)
                write_args["ffmpeg_params"] = list(
                    write_args.get("ffmpeg_params") or []
                ) + ["-vf", vf]
            # MoviePy derives its temp audio filename from the output's
            # BASENAME and joins it to `temp_audiofile_path` (default ""),
            # so the scratch WAV - ~600 MB for a full match - lands in the
            # service's working directory (the repo!) instead of next to
            # the output. Pin it to the render folder.
            write_args["temp_audiofile_path"] = str(output_path.parent)
            mp_logger = _make_audio_progress_logger(progress, total, start_time)
            if mp_logger is not None:
                write_args["logger"] = mp_logger
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

    def _concat_sequence(
        self,
        fmap: FrameMap,
        source_ranges: list[tuple[int, int]],
        clip_offsets: dict[str, int],
    ) -> tuple[list[int], list[tuple[int, int, int]]]:
        """Flatten several source ranges into one output frame order.

        Returns `(sequence, spans)` where `sequence` lists FrameMap indices
        in playback order and `spans` are `(out_start, out_end, index)`
        triples for each range that survived - the caller needs those to
        put chapter marks at the right timecodes. `index` is the position
        in `source_ranges`, because a range that maps to nothing (fully
        inside a cut region) is dropped and positional pairing would then
        mislabel every later chapter.
        """
        sequence: list[int] = []
        spans: list[tuple[int, int, int]] = []
        for idx, src in enumerate(source_ranges):
            start, end = self._source_range_to_output_range(
                fmap, src, clip_offsets
            )
            if end <= start:
                logger.warning(
                    "reel segment %s maps to no output frames; skipping", src
                )
                continue
            spans.append(
                (len(sequence), len(sequence) + (end - start), idx)
            )
            sequence.extend(range(start, end))
        return sequence, spans

    def segment_offsets(
        self, source_ranges: list[tuple[int, int]]
    ) -> list[tuple[int, int, int]]:
        """Output frame offsets each source range will occupy in a reel.

        Same computation `render(source_frame_ranges=...)` performs, exposed
        so a caller can build a chapter list without rendering first. Costs
        one FrameMap build.
        """
        fmap = build_frame_map(self.match)
        _, spans = self._concat_sequence(
            fmap, source_ranges, self._clip_offsets()
        )
        return spans

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
        # Resolve the readable inputs up front so the single-source fast
        # path can be detected before any pixels are touched.
        reads = [
            (inp, self._clips[inp.clip_id])
            for inp in entry.inputs
            if inp.clip_id in self._clips
        ]
        if not reads:
            return np.zeros((h, w, 3), dtype=np.uint8)

        first_inp = reads[0][0]
        primary_global_frame: Optional[int] = (
            clip_offsets.get(first_inp.clip_id, 0) + first_inp.local_frame
        )

        state = None
        if not self.skip_overlays and entry.scoreboard_visible:
            state = self.match.state_at_global_frame(primary_global_frame)

        # Fast path: one source at full weight and no color blend - which is
        # every frame outside a transition/fade, i.e. the overwhelming
        # majority. Staying in uint8 skips ~20 ms/frame at 1080p of pure
        # float32 up/down-casting that used to be done for nothing.
        if len(reads) == 1 and first_inp.weight >= 0.999 and entry.color_weight <= 0.0:
            out = self._read_frame(reads[0][1], first_inp.local_frame, w, h)
            if state is not None:
                out = self._scoreboard.apply(out, state)
        else:
            accum = np.zeros((h, w, 3), dtype=np.float32)
            for inp, clip in reads:
                arr = self._read_frame(clip, inp.local_frame, w, h)
                accum += arr.astype(np.float32) * float(inp.weight)

            # Scoreboard goes on before the color blend so it fades along
            # with the underlying frame during transitions.
            if state is not None:
                accum = self._scoreboard.apply(
                    np.clip(accum, 0, 255).astype(np.uint8), state
                ).astype(np.float32)

            if entry.color_weight > 0.0:
                cw = float(min(1.0, max(0.0, entry.color_weight)))
                color = np.array(entry.blend_color, dtype=np.float32)
                accum = accum * (1.0 - cw) + color * cw

            out = np.clip(accum, 0, 255).astype(np.uint8)

        # Message overlays applied last so they aren't dimmed by transitions.
        if not self.skip_overlays and entry.messages:
            out = self._messages.apply(out, entry.messages)

        return out

    def _read_frame(
        self, clip: VideoFileClip, local_frame: int, w: int, h: int
    ) -> np.ndarray:
        """Decode one source frame as uint8 HxWx3, letterboxed if needed."""
        t = max(0.0, min(local_frame / clip.fps, clip.duration - 1e-3))
        arr = clip.get_frame(t)
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8)
        if arr.shape[:2] != (h, w):
            arr = _letterbox(arr, w, h)
        return arr

    def _build_audio(
        self,
        fmap: FrameMap,
        fps: float,
        duration: float,
        sequence: "Sequence[int]",
        *,
        cancel_check: Optional[CancelCheck] = None,
        channels: Optional[int] = None,
    ) -> Optional[AudioClip]:
        clips_audio = {cid: c for cid, c in self._clips.items() if c.audio is not None}
        if not clips_audio:
            return None

        first = next(iter(clips_audio.values())).audio
        audio_fps = first.fps
        nchannels = first.nchannels
        # Preset mixdown (HandBrake `AudioMixdown`). Only ever narrows:
        # we can't invent channels the source doesn't have.
        out_channels = min(nchannels, int(channels)) if channels else nchannels
        # Output-frame -> FrameMap-index lookup, materialized once (a full
        # match is ~200k int64 = 1.6 MB, and make_audio is called per chunk).
        seq = np.fromiter(sequence, dtype=np.int64, count=len(sequence))
        slice_len = seq.shape[0]  # number of output frames

        def make_audio(t):
            if cancel_check is not None and cancel_check():
                raise RenderCancelled("Render cancelled by user")
            arr = np.atleast_1d(np.asarray(t))
            n = arr.shape[0]
            out = np.zeros((n, nchannels), dtype=np.float32)
            # t is local to the OUTPUT timeline; the sequence maps it back
            # to source frames. Grouping is by output index rather than by
            # FrameMap index because a reel may visit the same source frame
            # twice (overlapping spans), and each visit needs its own
            # timeline offset.
            local_frame_idx = np.clip((arr * fps).astype(int), 0, slice_len - 1)

            for li in np.unique(local_frame_idx):
                mask = local_frame_idx == li
                entry = fmap[seq[li]]
                samples = np.zeros((mask.sum(), nchannels), dtype=np.float32)
                t_offsets = arr[mask] - (li / fps)
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

            if out_channels < nchannels:
                # Downmix by averaging (HandBrake's stereo/mono mixdown);
                # picking a subset of channels would drop content.
                out = (
                    out.mean(axis=1, keepdims=True)
                    if out_channels == 1
                    else out[:, :out_channels]
                )

            return out if n > 1 else out[0]

        return AudioClip(make_audio, duration=duration, fps=audio_fps)


def _letterbox(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    """Resize an arbitrary-sized frame into (h,w,3), letterboxing on mismatch.

    Returns uint8 - the compositing pipeline stays in uint8 unless a
    transition actually needs float blending.
    """
    from PIL import Image

    img = Image.fromarray(arr.astype(np.uint8))
    img.thumbnail((w, h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return np.array(canvas)


def _make_audio_progress_logger(
    progress: Optional[ProgressCb], frames_total: int, start_time: float
) -> Optional[Any]:
    """Proglog logger that surfaces MoviePy's audio-writing pass.

    `write_videofile` renders the ENTIRE audio track to a temp WAV before
    it asks for the first video frame. On a full match that's minutes of
    apparent inactivity - the job sits at 0% with no message because our
    only progress source (`make_frame`) hasn't been called yet.

    MoviePy reports that pass through proglog's `chunk` bar, so we
    translate it into `RenderProgress(phase="audio")`. The writer's `t`
    (video) bar is ignored: `make_frame` already reports per-frame
    progress and is the more accurate source.
    """
    if progress is None:
        return None
    try:
        from proglog import ProgressBarLogger
    except Exception:  # pragma: no cover - proglog ships with moviepy
        return None

    class _AudioProgressLogger(ProgressBarLogger):
        def bars_callback(self, bar, attr, value, old_value=None):  # noqa: D102
            if bar != "chunk" or attr != "index":
                return
            try:
                total = self.bars[bar]["total"] or 0
                if total <= 0:
                    return
                fraction = min(1.0, max(0.0, float(value) / float(total)))
                progress(
                    RenderProgress(
                        frames_done=int(fraction * frames_total),
                        frames_total=frames_total,
                        elapsed_seconds=time.time() - start_time,
                        phase="audio",
                    )
                )
            except Exception:
                # Progress reporting must never break a render.
                logger.debug("audio progress callback failed", exc_info=True)

    return _AudioProgressLogger()


def _build_write_args(settings: dict[str, Any], fps: float) -> dict[str, Any]:
    args: dict[str, Any] = {
        "codec": settings.get("codec", "libx264"),
        "fps": fps,
        "logger": None,
    }
    # `preset` carries the ENCODER-SPECIFIC speed preset produced by the
    # HandBrake translation (p1..p7 for NVENC, slow/medium/... for x264):
    # MoviePy always emits `-preset`, so this is the only way to control
    # it without ending up with two conflicting flags.
    for key in (
        "preset",
        "threads",
        "bitrate",
        "ffmpeg_params",
        "audio_codec",
        "audio_bitrate",
        "audio_fps",
        "audio_nbytes",
    ):
        if settings.get(key):
            args[key] = settings[key]
    return args
