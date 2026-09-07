"""Load encoder settings and translate them into MoviePy/ffmpeg arguments.

`render_settings.json` uses **HandBrake's preset schema**, so a preset
exported from the HandBrake GUI (Presets -> right-click -> Export) can be
dropped in as-is, and the file can equally be imported back into
HandBrake. Three shapes are accepted:

* a full export: ``{"PresetList": [ {...}, ... ], "VersionMajor": ...}``
* a bare preset object: ``{"PresetName": ..., "VideoEncoder": ...}``
* HandBrake's own ``presets.json`` layout, where ``PresetList`` holds
  folders with a ``ChildrenArray`` of presets

Which preset is used, in order: the ``VME_RENDER_PRESET`` environment
variable (by name), then the one flagged ``"Default": true``, then the
first one in the file.

VME renders through ffmpeg (via MoviePy), not through HandBrake, so the
preset is *translated* rather than executed: `load_settings()` returns the
flat kwargs dict the renderer already understood (codec / bitrate /
audio_codec / ffmpeg_params / ...). Two VME extensions to the schema are
allowed because HandBrake has no equivalent encoders:
``FileFormat: "av_mov"`` and ``VideoEncoder: "dnxhd" | "prores"``.

Keys deliberately ignored (HandBrake-only concepts that don't apply to a
frame-server pipeline): every ``Picture*`` filter (deinterlace, denoise,
detelecine, crop, pad...), subtitles, chapters, multi-track audio beyond
the first, and framerate mode - VME always renders at the source clips'
frame rate. ``PictureWidth``/``PictureHeight`` ARE honored as a maximum
output size when ``PictureUseMaximumSize`` is set.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# HandBrake -> ffmpeg vocabularies
# --------------------------------------------------------------------------

# HandBrake `FileFormat` -> container extension. `av_mov` is a VME
# extension (HandBrake only muxes mp4/mkv/webm).
_FILE_FORMAT_EXTENSIONS: dict[str, str] = {
    "av_mp4": ".mp4",
    "av_mkv": ".mkv",
    "av_webm": ".webm",
    "av_mov": ".mov",
}

# HandBrake `VideoEncoder` -> ffmpeg encoder name.
_VIDEO_ENCODERS: dict[str, str] = {
    "x264": "libx264",
    "x264_10bit": "libx264",
    "x265": "libx265",
    "x265_10bit": "libx265",
    "x265_12bit": "libx265",
    "nvenc_h264": "h264_nvenc",
    "nvenc_h265": "hevc_nvenc",
    "nvenc_h265_10bit": "hevc_nvenc",
    "nvenc_av1": "av1_nvenc",
    "nvenc_av1_10bit": "av1_nvenc",
    "qsv_h264": "h264_qsv",
    "qsv_h265": "hevc_qsv",
    "qsv_h265_10bit": "hevc_qsv",
    "qsv_av1": "av1_qsv",
    "vce_h264": "h264_amf",
    "vce_h265": "hevc_amf",
    "vce_h265_10bit": "hevc_amf",
    "vce_av1": "av1_amf",
    "svt_av1": "libsvtav1",
    "svt_av1_10bit": "libsvtav1",
    "mpeg4": "mpeg4",
    "mpeg2": "mpeg2video",
    "VP8": "libvpx",
    "VP9": "libvpx-vp9",
    "VP9_10bit": "libvpx-vp9",
    "theora": "libtheora",
    # VME extensions - intermediate/mastering codecs HandBrake lacks.
    "dnxhd": "dnxhd",
    "prores": "prores_ks",
}

# Encoders whose 10/12-bit HandBrake variants imply a deeper pix_fmt.
_TEN_BIT_ENCODERS = {
    "x264_10bit",
    "x265_10bit",
    "nvenc_h265_10bit",
    "nvenc_av1_10bit",
    "qsv_h265_10bit",
    "vce_h265_10bit",
    "svt_av1_10bit",
    "VP9_10bit",
}

# HandBrake `AudioEncoder` -> ffmpeg encoder. `copy:*` entries mean
# passthrough, which is meaningless here (VME synthesizes a new audio
# track), so they resolve to the preset's AudioEncoderFallback.
_AUDIO_ENCODERS: dict[str, str] = {
    "av_aac": "aac",
    "ca_aac": "aac",
    "ca_haac": "aac",
    "fdk_aac": "libfdk_aac",
    "fdk_haac": "libfdk_aac",
    "mp3": "libmp3lame",
    "lame": "libmp3lame",
    "ac3": "ac3",
    "eac3": "eac3",
    "flac16": "flac",
    "flac24": "flac",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "truehd": "truehd",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
}

# HandBrake `AudioMixdown` -> channel count.
_MIXDOWN_CHANNELS: dict[str, int] = {
    "mono": 1,
    "left_only": 1,
    "right_only": 1,
    "stereo": 2,
    "dpl1": 2,
    "dpl2": 2,
    "5point1": 6,
    "6point1": 7,
    "7point1": 8,
}

# NVENC in ffmpeg takes p1..p7, not HandBrake's word presets.
_NVENC_PRESETS: dict[str, str] = {
    "fastest": "p1",
    "faster": "p2",
    "fast": "p3",
    "medium": "p4",
    "slow": "p5",
    "slower": "p6",
    "slowest": "p7",
}

# HandBrake's VideoQualityType enum.
_QUALITY_TARGET_SIZE = 0  # deprecated in HandBrake, treated as "encoder default"
_QUALITY_AVG_BITRATE = 1
_QUALITY_CONSTANT = 2


# --------------------------------------------------------------------------
# Preset selection
# --------------------------------------------------------------------------


def _iter_presets(data: Any) -> list[dict[str, Any]]:
    """Flatten any of the accepted file shapes into a list of presets."""
    if isinstance(data, dict) and "PresetList" in data:
        out: list[dict[str, Any]] = []
        for entry in data.get("PresetList") or []:
            if not isinstance(entry, dict):
                continue
            children = entry.get("ChildrenArray")
            if entry.get("Folder") or children:
                out.extend(c for c in (children or []) if isinstance(c, dict))
            else:
                out.append(entry)
        return out
    if isinstance(data, dict) and "PresetName" in data:
        return [data]
    return []


def _pick_preset(
    presets: list[dict[str, Any]], name: Optional[str]
) -> Optional[dict[str, Any]]:
    if not presets:
        return None
    if name:
        for p in presets:
            if str(p.get("PresetName", "")).lower() == name.lower():
                return p
        logger.warning(
            "render preset %r not found; falling back to the default preset", name
        )
    for p in presets:
        if p.get("Default"):
            return p
    return presets[0]


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------


def _video_quality_params(preset: dict[str, Any], encoder: str) -> list[str]:
    """ffmpeg rate-control flags for the preset's quality mode.

    HandBrake has two modes: average bitrate and constant quality. The
    knob name for constant quality is encoder-specific, hence the branch.

    Bitrate is emitted as an explicit `-b:v` rather than through
    MoviePy's `bitrate=` kwarg, which passes the ambiguous, deprecated
    `-b` (applies to every stream, not just video).
    """
    qtype = preset.get("VideoQualityType", _QUALITY_CONSTANT)
    if qtype == _QUALITY_AVG_BITRATE:
        kbps = int(preset.get("VideoAvgBitrate") or 0)
        return ["-b:v", f"{kbps}k"] if kbps > 0 else []
    if qtype == _QUALITY_CONSTANT:
        q = preset.get("VideoQualitySlider")
        if q is None:
            return []
        q_str = f"{float(q):g}"
        if encoder.endswith("_nvenc"):
            # VBR with a quality target; -b:v 0 keeps it from degrading
            # into capped-VBR at ffmpeg's default bitrate.
            return ["-rc", "vbr", "-cq", q_str, "-b:v", "0"]
        if encoder.endswith("_qsv"):
            return ["-global_quality", q_str]
        if encoder.endswith("_amf"):
            return ["-rc", "cqp", "-qp_i", q_str, "-qp_p", q_str]
        return ["-crf", q_str]
    return []


def _pix_fmt(preset: dict[str, Any], ff_encoder: str) -> Optional[str]:
    """Pick an explicit pixel format.

    MoviePy feeds ffmpeg 8-bit RGB, so the only real choice is how deep
    the encoder should go. yuv420p is the safe/compatible default; the
    HandBrake 10-bit encoder variants and DNxHR HQX ask for more.
    """
    hb_encoder = str(preset.get("VideoEncoder", ""))
    profile = str(preset.get("VideoProfile", "") or "").lower()
    if ff_encoder == "dnxhd":
        return "yuv422p10le" if "hqx" in profile or "444" in profile else "yuv422p"
    if ff_encoder == "prores_ks":
        return "yuv422p10le"
    if hb_encoder in _TEN_BIT_ENCODERS or profile in ("main10", "main 10"):
        return "yuv420p10le"
    return "yuv420p"


def _audio_settings(preset: dict[str, Any]) -> dict[str, Any]:
    """First audio track of the preset -> MoviePy audio kwargs.

    VME always produces exactly one synthesized track, so extra entries
    in `AudioList` (e.g. a 5.1 passthrough companion) are ignored, and a
    `copy:*` encoder resolves to `AudioEncoderFallback`.
    """
    tracks = preset.get("AudioList") or []
    track = tracks[0] if tracks and isinstance(tracks[0], dict) else {}
    hb_codec = str(track.get("AudioEncoder", "") or "")
    if hb_codec.startswith("copy") or not hb_codec:
        hb_codec = str(preset.get("AudioEncoderFallback", "av_aac") or "av_aac")
    if hb_codec == "none":
        return {"audio": False}

    out: dict[str, Any] = {
        "audio": True,
        "audio_codec": _AUDIO_ENCODERS.get(hb_codec, hb_codec),
    }
    bitrate = int(track.get("AudioBitrate") or 0)
    # PCM/FLAC are not bitrate-driven; passing -b:a there is meaningless.
    if bitrate > 0 and not out["audio_codec"].startswith(("pcm_", "flac")):
        out["audio_bitrate"] = f"{bitrate}k"
    channels = _MIXDOWN_CHANNELS.get(str(track.get("AudioMixdown", "") or ""))
    if channels:
        out["audio_channels"] = channels
    rate = track.get("AudioSamplerate")
    if rate and str(rate) != "auto":
        try:
            out["audio_fps"] = int(float(rate))
        except (TypeError, ValueError):
            pass
    return out


def translate_preset(preset: dict[str, Any]) -> dict[str, Any]:
    """HandBrake preset -> the flat settings dict the renderer consumes."""
    hb_encoder = str(preset.get("VideoEncoder", "x264") or "x264")
    encoder = _VIDEO_ENCODERS.get(hb_encoder, hb_encoder)
    file_format = str(preset.get("FileFormat", "av_mp4") or "av_mp4")
    ext = _FILE_FORMAT_EXTENSIONS.get(file_format, ".mp4")

    params: list[str] = _video_quality_params(preset, encoder)

    # MoviePy ALWAYS emits `-preset <value>` (defaulting to "medium"), so
    # the translated speed preset goes through that kwarg instead of
    # ffmpeg_params - otherwise the command carries two -preset flags.
    speed = str(preset.get("VideoPreset", "") or "")
    if speed and encoder.endswith("_nvenc"):
        # NVENC in ffmpeg wants p1..p7, not HandBrake's words.
        speed = _NVENC_PRESETS.get(speed, speed)

    profile = str(preset.get("VideoProfile", "") or "")
    if profile and profile != "auto":
        params += ["-profile:v", profile]
    level = str(preset.get("VideoLevel", "") or "")
    if level and level != "auto":
        params += ["-level", level]
    tune = str(preset.get("VideoTune", "") or "")
    if tune:
        params += ["-tune", tune]

    pix_fmt = _pix_fmt(preset, encoder)
    if pix_fmt:
        params += ["-pix_fmt", pix_fmt]

    color_range = str(preset.get("VideoColorRange", "") or "")
    if color_range and color_range != "auto":
        params += ["-color_range", "tv" if color_range == "limited" else "pc"]

    extra = str(preset.get("VideoOptionExtra", "") or "").strip()
    if extra:
        if encoder == "libx264":
            params += ["-x264-params", extra]
        elif encoder == "libx265":
            params += ["-x265-params", extra]
        else:
            logger.warning(
                "ignoring VideoOptionExtra %r: no mapping for encoder %s",
                extra,
                encoder,
            )

    # HEVC in mp4/mov needs the hvc1 tag for Apple/QuickTime playback -
    # ffmpeg defaults to hev1, HandBrake writes hvc1.
    if encoder in ("hevc_nvenc", "libx265", "hevc_qsv", "hevc_amf") and ext in (
        ".mp4",
        ".mov",
    ):
        params += ["-tag:v", "hvc1"]

    if preset.get("Optimize") and ext in (".mp4", ".mov"):
        params += ["-movflags", "+faststart"]

    settings: dict[str, Any] = {
        "preset_name": preset.get("PresetName", "unnamed"),
        "container": ext.lstrip("."),
        "codec": encoder,
        "ffmpeg_params": params,
    }
    if speed:
        settings["preset"] = speed
    settings.update(_audio_settings(preset))

    # Output size cap. HandBrake would rescale; the renderer turns this
    # into a scale filter only when the source actually exceeds it.
    if preset.get("PictureUseMaximumSize"):
        w = int(preset.get("PictureWidth") or 0)
        h = int(preset.get("PictureHeight") or 0)
        if w > 0 and h > 0:
            settings["max_width"] = w
            settings["max_height"] = h

    threads = preset.get("VmeThreads")
    if threads:
        settings["threads"] = int(threads)
    return settings


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

# Used when the settings file is missing or unreadable: HandBrake's own
# "Fast 1080p30"-style x264 defaults, expressed in the same schema so the
# fallback path exercises the same translation code.
_FALLBACK_PRESET: dict[str, Any] = {
    "PresetName": "VME fallback (x264 CQ22)",
    "FileFormat": "av_mp4",
    "VideoEncoder": "x264",
    "VideoPreset": "medium",
    "VideoProfile": "high",
    "VideoLevel": "4.1",
    "VideoQualityType": _QUALITY_CONSTANT,
    "VideoQualitySlider": 22,
    "Optimize": True,
    "AudioList": [
        {"AudioEncoder": "av_aac", "AudioBitrate": 160, "AudioMixdown": "stereo"}
    ],
}


def load_settings(
    path: str | Path | None = None, preset_name: Optional[str] = None
) -> dict[str, Any]:
    """Load `render_settings.json` and translate the selected preset."""
    if path is None:
        path = Path(__file__).parent / "render_settings.json"
    path = Path(path)
    if preset_name is None:
        preset_name = os.environ.get("VME_RENDER_PRESET") or None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load render settings %s: %s", path, exc)
        return translate_preset(_FALLBACK_PRESET)

    preset = _pick_preset(_iter_presets(data), preset_name)
    if preset is None:
        logger.warning("No presets found in %s; using the built-in fallback", path)
        return translate_preset(_FALLBACK_PRESET)

    settings = translate_preset(preset)
    logger.info(
        "render preset %r -> codec=%s container=%s",
        settings.get("preset_name"),
        settings.get("codec"),
        settings.get("container"),
    )
    return settings


def list_presets(path: str | Path | None = None) -> list[str]:
    """Names of every preset in the settings file (for UI / diagnostics)."""
    if path is None:
        path = Path(__file__).parent / "render_settings.json"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return [str(p.get("PresetName", "unnamed")) for p in _iter_presets(data)]


def output_extension(settings: dict[str, Any] | None = None) -> str:
    """File extension (dot included) matching the configured container.

    The muxer is chosen by ffmpeg from the output filename, so every
    place that builds a render filename must agree with the codec in
    the settings (e.g. DNxHR cannot be written into an .mp4).
    """
    if settings is None:
        settings = load_settings()
    container = str(settings.get("container") or "mp4").lower().lstrip(".")
    ext = f".{container}"
    return ext if ext in _FILE_FORMAT_EXTENSIONS.values() else ext


def apply_container_extension(
    name: str | Path, settings: dict[str, Any] | None = None
) -> Any:
    """Return `name` with its suffix replaced by the container extension.

    Accepts (and returns) either a str or a Path so it can be used both
    on relative template output (`highlights/.../foo.mp4`) and on
    absolute output paths.
    """
    ext = output_extension(settings)
    if isinstance(name, Path):
        return name.with_suffix(ext)
    p = Path(str(name))
    return p.with_suffix(ext).as_posix() if str(name) else str(name)


def scale_filter(size: tuple[int, int], settings: dict[str, Any]) -> Optional[str]:
    """`-vf` scale expression when the frame exceeds the preset's max size.

    HandBrake's `PictureUseMaximumSize` means "downscale to fit, never
    upscale, keep aspect". `force_original_aspect_ratio=decrease` plus
    rounding to even dimensions reproduces that for any source.
    """
    max_w = int(settings.get("max_width") or 0)
    max_h = int(settings.get("max_height") or 0)
    if max_w <= 0 or max_h <= 0:
        return None
    w, h = size
    if w <= max_w and h <= max_h:
        return None
    return (
        f"scale={max_w}:{max_h}:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2"
    )
