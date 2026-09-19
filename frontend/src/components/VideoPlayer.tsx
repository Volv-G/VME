import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  TransformWrapper,
  TransformComponent,
  type ReactZoomPanPinchRef,
} from "react-zoom-pan-pinch";
import { api } from "../api/client";
import { useHotkeyAction } from "../hotkeys";
import type { ClipDto } from "../types/api";

export interface VideoPlayerHandle {
  /** Seek to a global frame across all clips. */
  seekToGlobalFrame: (globalFrame: number) => void;
  /** Get current global frame index. */
  getGlobalFrame: () => number;
  /** Pause playback. */
  pause: () => void;
}

interface Props {
  team: string;
  tournament: string;
  date: string;
  match: string;
  clips: ClipDto[];
  fps: number;
  /** Called continuously while playing/seeking with the latest global frame. */
  onFrame?: (globalFrame: number) => void;
}

/** Resolves a global frame to (clip index, local frame). */
function resolveClip(clips: ClipDto[], globalFrame: number): { clipIdx: number; localFrame: number } | null {
  let offset = 0;
  for (let i = 0; i < clips.length; i++) {
    const fc = clips[i].frame_count;
    if (globalFrame < offset + fc) return { clipIdx: i, localFrame: globalFrame - offset };
    offset += fc;
  }
  if (clips.length === 0) return null;
  return { clipIdx: clips.length - 1, localFrame: clips[clips.length - 1].frame_count - 1 };
}

function clipOffset(clips: ClipDto[], idx: number): number {
  let offset = 0;
  for (let i = 0; i < idx; i++) offset += clips[i].frame_count;
  return offset;
}

export const VideoPlayer = forwardRef<VideoPlayerHandle, Props>(function VideoPlayer(
  { team, tournament, date, match, clips, fps, onFrame },
  ref
) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const zoomRef = useRef<ReactZoomPanPinchRef | null>(null);
  // The frame we last asked the video to display. Used to chain rapid step
  // clicks off the intended position instead of the stale, mid-seek
  // video.currentTime. Cleared on 'seeked' / 'play' / clip change.
  const targetFrameRef = useRef<number | null>(null);
  const [activeClipIdx, setActiveClipIdx] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  // Last-requested playback rate (1, 2, 4...). Mirrored in a ref so the
  // clip-switch resume path can apply it to the freshly loaded <video>.
  const [playbackRate, setPlaybackRate] = useState(1);
  const playbackRateRef = useRef(1);

  const activeClip = clips[activeClipIdx];

  // Read the frame the browser is actually displaying. floor(+eps) is the
  // robust choice: when the browser is mid-frame it's showing floor(t*fps),
  // and the eps absorbs IEEE-754 drift on exact frame boundaries
  // (e.g. (1/30)*30 = 0.999...).
  function frameFromTime(t: number, clipFps: number): number {
    return Math.floor(t * clipFps + 1e-3);
  }
  // Convert a frame index to a seek timestamp in the *middle* of that frame's
  // display interval. Decoders are far more reliable about landing on the
  // intended frame when given a mid-interval timestamp than a frame-boundary
  // one (some snap backwards to the prior keyframe boundary on exact-N/fps).
  function timeForFrame(frame: number, clipFps: number): number {
    return (frame + 0.5) / clipFps;
  }

  // Reset transform when the active clip changes (different aspect ratio etc).
  useEffect(() => {
    zoomRef.current?.resetTransform();
  }, [activeClipIdx]);

  // Keep onFrame fresh without retriggering animation effect
  const onFrameRef = useRef(onFrame);
  useEffect(() => { onFrameRef.current = onFrame; }, [onFrame]);

  useImperativeHandle(ref, () => ({
    seekToGlobalFrame(globalFrame: number) {
      const r = resolveClip(clips, globalFrame);
      if (!r) return;
      switchToClip(r.clipIdx, r.localFrame, false);
    },
    getGlobalFrame() {
      const v = videoRef.current;
      if (!v || !activeClip) return 0;
      const localFrame =
        targetFrameRef.current ?? frameFromTime(v.currentTime, activeClip.fps || fps);
      return clipOffset(clips, activeClipIdx) + localFrame;
    },
    pause() { videoRef.current?.pause(); },
  }), [activeClipIdx, activeClip, clips, fps]);

  const pendingSeekRef = useRef<number | null>(null);
  // Set when a clip-switch should auto-resume playback after the new src loads
  // (continuous playback across clip boundaries, manual play-from-end-of-timeline).
  const playOnLoadRef = useRef(false);
  // Hide the <video> while the new src is loading + the pending seek lands,
  // so users don't see frame 0 of the new clip flash before the requested
  // frame appears. The black .editor-video container shows through.
  // The ref carries the same value as the state but updates *synchronously*
  // so the RAF reporter and the in-flight stale rAF closure both see "true"
  // before the next animation frame, suppressing intermediate frame reports
  // that would otherwise jerk the timeline playhead.
  const [isSwitching, setIsSwitching] = useState(false);
  const isSwitchingRef = useRef(false);
  function setSwitching(v: boolean) {
    isSwitchingRef.current = v;
    setIsSwitching(v);
  }

  // The single funnel for changing the active clip. Setting `isSwitchingRef`
  // synchronously here is what actually prevents intermediate frame reports;
  // every other clip-switch call goes through this.
  function switchToClip(newIdx: number, localSeekFrame: number, autoPlay: boolean) {
    if (newIdx === activeClipIdx) {
      // Same-clip "switch" still needs to honour the requested seek.
      const v = videoRef.current;
      if (v && clips[newIdx]) {
        targetFrameRef.current = localSeekFrame;
        v.currentTime = timeForFrame(localSeekFrame, clips[newIdx].fps || fps);
        if (autoPlay) void v.play();
      }
      return;
    }
    pendingSeekRef.current = localSeekFrame;
    playOnLoadRef.current = autoPlay;
    setSwitching(true);
    setActiveClipIdx(newIdx);
  }

  // When the active clip changes, apply any pending seek after src loads,
  // then optionally resume playback.
  //
  // Bound as a React prop (`onLoadedMetadata`), NOT via addEventListener in
  // an effect. The listener version lost the race: changing `src` starts
  // loading at commit time, while a passive effect runs after paint, so a
  // clip whose metadata was already in the browser cache could fire
  // `loadedmetadata` into a gap where no handler was attached. The pending
  // seek then never ran and `isSwitching` never cleared - the <video> stayed
  // hidden behind the black container and playback appeared to freeze at the
  // clip boundary. React attaches prop handlers before the element can emit
  // anything, which removes the race entirely.
  function handleLoadedMetadata() {
    const v = videoRef.current;
    if (!v || !activeClip) return;
    if (pendingSeekRef.current !== null) {
      const f = pendingSeekRef.current;
      pendingSeekRef.current = null;
      targetFrameRef.current = f;
      const t = timeForFrame(f, activeClip.fps || fps);
      // If the element is somehow already there, no 'seeked' will fire
      // (the spec aborts a seek to the current position), so reveal now
      // rather than waiting for an event that isn't coming.
      if (Math.abs(v.currentTime - t) < 1e-6) setSwitching(false);
      else v.currentTime = t;
    } else {
      // No seek requested for this clip switch; safe to reveal immediately.
      setSwitching(false);
    }
    if (playOnLoadRef.current) {
      playOnLoadRef.current = false;
      v.playbackRate = playbackRateRef.current;
      void v.play().catch(() => {
        // Autoplay refusal or an aborted load: don't strand the user
        // behind a hidden video with no way back.
        setSwitching(false);
      });
    }
  }

  // Safety net for the reveal. Every path above ends in a 'seeked' or a
  // 'play', but a stalled network, a decode error or a browser that
  // swallows one of those events would otherwise leave the player hidden
  // forever with no indication why. Revealing a possibly-wrong frame is
  // strictly better than a black rectangle.
  useEffect(() => {
    if (!isSwitching) return;
    const t = window.setTimeout(() => {
      if (!isSwitchingRef.current) return;
      console.warn(
        "VideoPlayer: clip switch did not complete within 4s; revealing " +
          "anyway (readyState=" +
          (videoRef.current?.readyState ?? -1) +
          ", networkState=" +
          (videoRef.current?.networkState ?? -1) +
          ")"
      );
      pendingSeekRef.current = null;
      setSwitching(false);
    }, 4000);
    return () => window.clearTimeout(t);
  }, [isSwitching]);

  // RAF loop reporting current frame. Suppressed while we're mid-clip-switch
  // so we don't report stale frame-0-of-new-clip positions to the parent
  // (which would briefly snap the timeline playhead).
  useEffect(() => {
    let raf = 0;
    let stop = false;
    function tick() {
      if (stop) return;
      const v = videoRef.current;
      if (v && activeClip && !isSwitchingRef.current) {
        if (v.ended && !endedGuardRef.current) {
          // Guarded so a run of frames at the end can't queue several
          // switches for the same boundary.
          endedGuardRef.current = true;
          handleEnded();
        }
        if (onFrameRef.current) {
          const localFrame =
            targetFrameRef.current ??
            frameFromTime(v.currentTime, activeClip.fps || fps);
          const global = clipOffset(clips, activeClipIdx) + localFrame;
          onFrameRef.current(global);
        }
      }
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => { stop = true; cancelAnimationFrame(raf); };
  }, [activeClipIdx, activeClip, clips, fps]);

  // When the current clip ends, continue into the next one.
  function handleEnded() {
    if (activeClipIdx < clips.length - 1) {
      switchToClip(activeClipIdx + 1, 0, true);
    } else {
      setIsPlaying(false);
    }
  }

  // Backstop for a missing 'ended'. Browsers don't always fire it when a
  // media element reaches the end of a stream served by range requests -
  // playback just stops on the last frame, which reads as "the player got
  // stuck at the clip boundary". Polling `ended` costs nothing (the RAF
  // loop already runs) and makes the advance depend on state rather than
  // on an event arriving.
  const endedGuardRef = useRef(false);
  useEffect(() => {
    endedGuardRef.current = false;
  }, [activeClipIdx]);

  // Compute the current global frame from in-flight target (preferred) or
  // the actual displayed currentTime, then add deltaFrames in global space and
  // route the result back to (clipIdx, localFrame). This lets stepping cross
  // clip boundaries naturally.
  function jumpFrames(deltaFrames: number) {
    const v = videoRef.current;
    if (!v || !activeClip || deltaFrames === 0 || clips.length === 0) return;
    if (!v.paused) v.pause();

    const totalFrames = clips.reduce((s, c) => s + c.frame_count, 0);
    if (totalFrames === 0) return;

    const localCur = targetFrameRef.current ?? frameFromTime(v.currentTime, activeClip.fps || fps);
    const curGlobal = clipOffset(clips, activeClipIdx) + localCur;
    const nextGlobal = Math.max(0, Math.min(totalFrames - 1, curGlobal + deltaFrames));
    if (nextGlobal === curGlobal) return;

    const r = resolveClip(clips, nextGlobal);
    if (!r) return;

    switchToClip(r.clipIdx, r.localFrame, false);
  }

  function jumpToClipEdge(edge: "start" | "end") {
    const v = videoRef.current;
    if (!v || !activeClip) return;
    if (!v.paused) v.pause();
    const clipFps = activeClip.fps || fps;
    const lastFrame = activeClip.frame_count - 1;
    const cur = targetFrameRef.current ?? frameFromTime(v.currentTime, clipFps);

    if (edge === "end") {
      // Already at the end of this clip → jump to the end of the next clip.
      if (cur >= lastFrame && activeClipIdx < clips.length - 1) {
        const nextIdx = activeClipIdx + 1;
        switchToClip(nextIdx, clips[nextIdx].frame_count - 1, false);
        return;
      }
      targetFrameRef.current = lastFrame;
      v.currentTime = timeForFrame(lastFrame, clipFps);
    } else {
      // Already at the start of this clip → jump to the start of the previous clip.
      if (cur <= 0 && activeClipIdx > 0) {
        switchToClip(activeClipIdx - 1, 0, false);
        return;
      }
      targetFrameRef.current = 0;
      v.currentTime = timeForFrame(0, clipFps);
    }
  }

  // Generic play-at-rate / pause toggle. Pressing the same rate while it's
  // already playing pauses; pressing a different rate hot-swaps without
  // pausing.
  function playAt(rate: number) {
    const v = videoRef.current;
    if (!v || !activeClip || clips.length === 0) return;

    const sameRate = Math.abs(v.playbackRate - rate) < 0.01;
    if (!v.paused && sameRate) {
      v.pause();
      return;
    }

    playbackRateRef.current = rate;
    setPlaybackRate(rate);

    const clipFps = activeClip.fps || fps;
    const lastFrame = activeClip.frame_count - 1;
    const cur = frameFromTime(v.currentTime, clipFps);
    const isLastClip = activeClipIdx === clips.length - 1;

    // Pressing play at the very end of the timeline rewinds to the start.
    if (cur >= lastFrame && isLastClip && v.paused) {
      switchToClip(0, 0, true);
      return;
    }

    v.playbackRate = rate;
    if (v.paused) void v.play();
  }

  // Hotkeys. Bindings live in src/hotkeys/catalog.ts and can be customized
  // (and persisted) via the registry; here we only wire live handlers.
  useHotkeyAction("playback.toggle", () => playAt(playbackRateRef.current));
  useHotkeyAction("playback.play1x", () => playAt(1));
  useHotkeyAction("playback.play2x", () => playAt(2));
  useHotkeyAction("playback.play4x", () => playAt(4));
  useHotkeyAction("playback.back1s", () => jumpFrames(-(activeClip?.fps || fps)));
  useHotkeyAction("playback.fwd1s", () => jumpFrames(activeClip?.fps || fps));

  if (!activeClip) {
    return <div className="muted" style={{ padding: 24 }}>No clips. Upload a video to start.</div>;
  }

  const clipFps = activeClip.fps || fps;
  type Step = { label: string; title: string; action: () => void };
  const leftSteps: Step[] = [
    { label: "⏮", title: "Beginning of clip", action: () => jumpToClipEdge("start") },
    { label: "−1s", title: "Back 1 second", action: () => jumpFrames(-clipFps) },
    { label: "−10", title: "Back 10 frames", action: () => jumpFrames(-10) },
    { label: "−1", title: "Back 1 frame", action: () => jumpFrames(-1) },
  ];
  const rightSteps: Step[] = [
    { label: "+1", title: "Forward 1 frame", action: () => jumpFrames(1) },
    { label: "+10", title: "Forward 10 frames", action: () => jumpFrames(10) },
    { label: "+1s", title: "Forward 1 second", action: () => jumpFrames(clipFps) },
    { label: "⏭", title: "End of clip", action: () => jumpToClipEdge("end") },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="editor-video">
        <TransformWrapper
          ref={zoomRef}
          minScale={1}
          maxScale={16}
          initialScale={1}
          centerOnInit
          centerZoomedOut
          limitToBounds
          doubleClick={{ mode: "reset" }}
          wheel={{ step: 0.01 }}
          panning={{ velocityDisabled: true }}
        >
          <TransformComponent
            wrapperClass="vp-zoom-wrapper"
            contentClass="vp-zoom-content"
          >
            <video
              ref={videoRef}
              src={api.clipStreamUrl(team, tournament, date, match, activeClip.id)}
              controls={false}
              onPlay={() => {
                targetFrameRef.current = null;
                setSwitching(false);
                setIsPlaying(true);
              }}
              onPause={() => setIsPlaying(false)}
              onSeeked={() => {
                targetFrameRef.current = null;
                setSwitching(false);
              }}
              onLoadedMetadata={handleLoadedMetadata}
              onError={() => {
                console.warn(
                  "VideoPlayer: <video> error on clip",
                  activeClip?.filename,
                  videoRef.current?.error
                );
                setSwitching(false);
              }}
              onEnded={handleEnded}
              preload="metadata"
              draggable={false}
              style={{ visibility: isSwitching ? "hidden" : "visible" }}
            />
          </TransformComponent>
        </TransformWrapper>
      </div>
      <div className="player-controls">
        {leftSteps.map((s) => (
          <button key={s.title} onClick={s.action} title={s.title} className="step-btn">
            {s.label}
          </button>
        ))}
        <div className="play-group">
          {([1, 2, 4] as const).map((rate) => {
            const active = isPlaying && Math.abs(playbackRate - rate) < 0.01;
            const label = rate === 1 ? "▶" : `${rate}×`;
            const hotkey = rate === 1 ? " (S)" : rate === 2 ? " (W)" : "";
            return (
              <button
                key={rate}
                onClick={() => playAt(rate)}
                className={`play-btn${active ? " primary" : ""}`}
                title={active ? "Pause (Space)" : `Play at ${rate}×${hotkey}`}
              >
                {active ? "❚❚" : label}
              </button>
            );
          })}
        </div>
        {rightSteps.map((s) => (
          <button key={s.title} onClick={s.action} title={s.title} className="step-btn">
            {s.label}
          </button>
        ))}
        <select
          value={activeClipIdx}
          onChange={(e) => {
            switchToClip(parseInt(e.target.value, 10), 0, false);
          }}
        >
          {clips.map((c, i) => (
            <option key={c.id} value={i}>
              {i + 1}. {c.filename}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
});
