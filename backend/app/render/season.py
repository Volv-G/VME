"""Season reels: one player's plays from every match, joined into one file.

Each match is rendered on its own first - the plays, with their captions,
exactly as the match's player reel shows them - and preceded by a title
card naming the match. The pieces are then joined in ONE ffmpeg pass,
because they cannot simply be concatenated: a phone records at 59.94,
a camera at 29.97 or 50, at different sizes, and the renderer writes
each match at its own source's rate and size. The join brings every
piece to one size and rate, gives every piece a stereo track (a title
card has no sound of its own), and fades each one in from and out to
black - the cards slowly, the plays briefly, so the cut between matches
reads as a page turn rather than a glitch.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..library.ffmpeg_tools import ffmpeg_path, ffprobe_path

logger = logging.getLogger(__name__)

# How long a match's title card is on screen, fades included.
CARD_SECONDS = 3.0
CARD_FADE = 0.6
# The plays fade in from black after the card and out before the next.
PART_FADE = 0.3
AUDIO_RATE = 48000


@dataclass
class Piece:
    """One input of the join: a title card image, or a rendered part."""

    path: Path
    is_card: bool
    # Seconds. For a card it is CARD_SECONDS; for a part, probed.
    duration: float
    has_audio: bool = True


def probe_part(path: Path) -> tuple[float, float, bool]:
    """(duration seconds, frame rate, has audio) of a rendered part."""
    out = subprocess.run(
        [
            ffprobe_path(), "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,r_frame_rate",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, timeout=60,
    )

    data = json.loads(out.stdout or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    fps = 0.0
    has_audio = False
    for s in data.get("streams") or []:
        if s.get("codec_type") == "audio":
            has_audio = True
        elif s.get("codec_type") == "video" and not fps:
            num, _, den = str(s.get("r_frame_rate") or "0/1").partition("/")
            try:
                fps = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                fps = 0.0
    return duration, fps, has_audio


def _filter_graph(pieces: list[Piece], width: int, height: int, fps: float) -> tuple[list[str], str]:
    """ffmpeg inputs and the filter graph that joins them.

    Input order: each piece's own input, and right after it a silent
    source for any piece without sound, so every segment of the concat
    has the audio stream it requires.
    """
    args: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    n = 0
    for k, p in enumerate(pieces):
        if p.is_card:
            args += ["-loop", "1", "-framerate", f"{fps:.6f}", "-t", f"{p.duration:.3f}", "-i", str(p.path)]
        else:
            args += ["-i", str(p.path)]
        v_in = f"{n}:v"
        n += 1
        if p.is_card or not p.has_audio:
            args += [
                "-f", "lavfi", "-t", f"{p.duration:.3f}",
                "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo",
            ]
            a_in = f"{n}:a"
            n += 1
        else:
            a_in = f"{n - 1}:a"
        fade = CARD_FADE if p.is_card else PART_FADE
        fade = min(fade, p.duration / 3)
        out_at = max(0.0, p.duration - fade)
        chains.append(
            f"[{v_in}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:.6f},"
            f"format=yuv420p,trim=duration={p.duration:.3f},setpts=PTS-STARTPTS,"
            f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={out_at:.3f}:d={fade:.3f}[v{k}]"
        )
        chains.append(
            f"[{a_in}]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,"
            f"atrim=duration={p.duration:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={out_at:.3f}:d={fade:.3f}[a{k}]"
        )
        labels.append(f"[v{k}][a{k}]")
    chains.append(f"{''.join(labels)}concat=n={len(pieces)}:v=1:a=1[v][a]")
    return args, ";\n".join(chains)


def join(
    pieces: list[Piece],
    output: Path,
    settings: dict,
    *,
    fps: float,
    progress: Optional[Callable[[float], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Path:
    """Join `pieces` into `output`, encoded with the team's render settings.

    `progress` gets 0..1 as the encode moves through the joined length.
    Raises RuntimeError on an ffmpeg failure; on cancel the process is
    killed, the partial output removed, and `RenderCancelled` raised.
    """
    from .renderer import RenderCancelled

    width = int(settings.get("max_width") or 1920)
    height = int(settings.get("max_height") or 1080)
    inputs, graph = _filter_graph(pieces, width, height, fps)
    total = sum(p.duration for p in pieces) or 1.0

    video = ["-c:v", settings.get("codec", "libx264")]
    if settings.get("preset"):
        video += ["-preset", str(settings["preset"])]
    video += list(settings.get("ffmpeg_params") or [])
    audio = ["-c:a", settings.get("audio_codec") or "aac"]
    if settings.get("audio_bitrate"):
        audio += ["-b:a", str(settings["audio_bitrate"])]

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        # A season of matches is a long graph; a script file keeps it out
        # of the command line, which Windows caps at 32k characters.
        script = Path(tmp) / "graph.txt"
        script.write_text(graph, encoding="utf-8")
        errlog = Path(tmp) / "ffmpeg.err"
        cmd = [
            ffmpeg_path(), "-y", "-loglevel", "error", "-nostats",
            *inputs,
            "-filter_complex_script", str(script),
            "-map", "[v]", "-map", "[a]",
            "-r", f"{fps:.6f}",
            *video, *audio,
            "-progress", "pipe:1",
            str(output),
        ]
        logger.info("season reel: joining %d pieces into %s", len(pieces), output.name)
        with open(errlog, "w", encoding="utf-8") as err:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
            assert proc.stdout is not None
            try:
                for line in proc.stdout:
                    if cancel_check is not None and cancel_check():
                        proc.kill()
                        proc.wait()
                        output.unlink(missing_ok=True)
                        raise RenderCancelled("Render cancelled by user")
                    if line.startswith("out_time_us=") and progress is not None:
                        try:
                            us = int(line.split("=", 1)[1])
                        except ValueError:
                            continue
                        progress(max(0.0, min(1.0, us / 1e6 / total)))
                code = proc.wait()
            except BaseException:
                if proc.poll() is None:
                    proc.kill()
                raise
        if code != 0:
            output.unlink(missing_ok=True)
            tail = errlog.read_text(encoding="utf-8", errors="replace").strip()[-600:]
            raise RuntimeError(f"ffmpeg exited {code} joining the season reel: {tail}")
    return output
