import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ClipDto, EventDto } from "../../types/api";
import { analyzeCuts } from "../cutAnalysis";
import { analyzeFocus } from "../focusAnalysis";
import { EVENT_COLORS } from "../eventStyle";

interface Props {
  clips: ClipDto[];
  events: EventDto[];
  currentFrame: number;
  selectedEventId: number | null;
  onSeek: (globalFrame: number) => void;
  onSelectEvent: (eventId: number) => void;
}

const TIMELINE_HEIGHT = 96;
const RULER_HEIGHT = 18;
const CLIP_HEIGHT = 30;
const EVENT_BAND_TOP = RULER_HEIGHT + CLIP_HEIGHT + 4;

const MIN_ZOOM = 1;
// Absolute ceiling. The effective cap is derived from the current
// viewport so the canvas backing store can never exceed the browser's
// max canvas size - see `maxZoom` below.
const MAX_ZOOM = 200;
const TICK_TARGET_PX = 90;
// Hard ceiling on canvas pixel dimensions. Browsers reject canvases
// larger than this (Chrome/Edge ~16384 px per side, Safari sometimes
// less). A rejected canvas throws on draw, React unmounts, and the
// whole editor goes blank - which is what over-zooming used to do.
const MAX_CANVAS_PX = 16384;
// Tick intervals in seconds. The first one whose on-screen spacing is >= the
// target px width is picked as the labeled tick. We try sub-second intervals
// when zoomed in enough that even 1s isn't tight; otherwise round numbers.
const TICK_LADDER_SEC = [
  0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200,
];

function fmtTime(seconds: number): string {
  const sign = seconds < 0 ? "-" : "";
  const total = Math.abs(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total - h * 3600 - m * 60;
  // Show fractional seconds only when the tick interval is sub-second.
  const sStr = s < 10
    ? (Number.isInteger(s) ? `0${s.toFixed(0)}` : `0${s.toFixed(1)}`)
    : (Number.isInteger(s) ? s.toFixed(0) : s.toFixed(1));
  if (h > 0) return `${sign}${h}:${m.toString().padStart(2, "0")}:${sStr}`;
  return `${sign}${m}:${sStr}`;
}

function pickTickSec(pxPerSec: number): number {
  for (const candidate of TICK_LADDER_SEC) {
    if (candidate * pxPerSec >= TICK_TARGET_PX) return candidate;
  }
  return TICK_LADDER_SEC[TICK_LADDER_SEC.length - 1];
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

export function Timeline({
  clips,
  events,
  currentFrame,
  selectedEventId,
  onSeek,
  onSelectEvent,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [viewWidth, setViewWidth] = useState(800);
  const [zoom, setZoom] = useState(1);

  // After a zoom change we want to keep a specific frame stuck under the
  // cursor. The handler stashes target details here; a layout effect applies
  // the new scrollLeft synchronously after the canvas re-renders.
  const pendingScrollRef = useRef<{ frame: number; cursorX: number } | null>(null);

  const totalFrames = useMemo(
    () => clips.reduce((s, c) => s + c.frame_count, 0),
    [clips]
  );

  const fps = clips[0]?.fps || 30;

  const clipOffsets = useMemo(() => {
    const offsets: number[] = [];
    let acc = 0;
    for (const c of clips) {
      offsets.push(acc);
      acc += c.frame_count;
    }
    return offsets;
  }, [clips]);

  // Derived max zoom: the largest factor that keeps the canvas backing
  // store (viewWidth * zoom * dpr) under MAX_CANVAS_PX. Recomputed on
  // every render so it adapts to window resizes and HiDPI changes.
  const maxZoom = useMemo(() => {
    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    const ceiling = MAX_CANVAS_PX / Math.max(1, viewWidth * dpr);
    return clamp(Math.floor(ceiling * 10) / 10, MIN_ZOOM, MAX_ZOOM);
  }, [viewWidth]);

  // If a window-resize / DPR change drops `maxZoom` below the current
  // zoom, snap back so we never produce an oversized canvas on the next
  // render.
  useEffect(() => {
    if (zoom > maxZoom) setZoom(maxZoom);
  }, [maxZoom, zoom]);

  const contentWidth = Math.max(viewWidth * zoom, viewWidth);

  // Watch the container's width.
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 0 && w !== viewWidth) setViewWidth(w);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [viewWidth]);

  // Reset zoom if the timeline becomes empty (avoids weird state across matches).
  useEffect(() => {
    if (totalFrames === 0 && zoom !== 1) setZoom(1);
  }, [totalFrames, zoom]);

  // Render.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = contentWidth * dpr;
    canvas.height = TIMELINE_HEIGHT * dpr;
    canvas.style.width = `${contentWidth}px`;
    canvas.style.height = `${TIMELINE_HEIGHT}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = "#0e1116";
    ctx.fillRect(0, 0, contentWidth, TIMELINE_HEIGHT);

    if (totalFrames === 0) {
      ctx.fillStyle = "#8b949e";
      ctx.font = "12px sans-serif";
      ctx.fillText("Add clips to see the timeline", 12, 18);
      return;
    }

    const px = (frame: number) => (frame / totalFrames) * contentWidth;

    // Visible window for tick culling (avoid drawing thousands of off-screen ticks).
    const container = containerRef.current;
    const visibleStart = container ? container.scrollLeft : 0;
    const visibleEnd = visibleStart + viewWidth;

    // Ruler: smart tick spacing.
    ctx.strokeStyle = "#2a313c";
    ctx.fillStyle = "#8b949e";
    ctx.font = "10px ui-monospace, monospace";
    const totalSec = totalFrames / fps;
    const pxPerSec = contentWidth / totalSec;
    const tickSec = pickTickSec(pxPerSec);
    const minorSec = tickSec / (tickSec >= 1 ? 5 : 2); // ~5 minor between major (or 2 for sub-second)
    const minorPx = minorSec * pxPerSec;

    // Minor ticks (no label) - only when they aren't too crammed.
    if (minorPx >= 6) {
      const startSec = Math.floor((visibleStart / pxPerSec) / minorSec) * minorSec;
      ctx.beginPath();
      for (let s = startSec; s * pxPerSec <= visibleEnd; s += minorSec) {
        if (s < 0) continue;
        const f = s * fps;
        if (f > totalFrames) break;
        const x = px(f);
        ctx.moveTo(x, RULER_HEIGHT - 3);
        ctx.lineTo(x, RULER_HEIGHT);
      }
      ctx.stroke();
    }

    // Major ticks + labels.
    const startSec = Math.floor((visibleStart / pxPerSec) / tickSec) * tickSec;
    for (let s = startSec; s * pxPerSec <= visibleEnd + TICK_TARGET_PX; s += tickSec) {
      if (s < 0) continue;
      const f = s * fps;
      if (f > totalFrames) break;
      const x = px(f);
      ctx.beginPath();
      ctx.moveTo(x, RULER_HEIGHT - 7);
      ctx.lineTo(x, RULER_HEIGHT);
      ctx.stroke();
      ctx.fillText(fmtTime(s), x + 3, 11);
    }

    // Clips bar.
    let acc = 0;
    clips.forEach((clip, i) => {
      const x0 = px(acc);
      const x1 = px(acc + clip.frame_count);
      ctx.fillStyle = i % 2 === 0 ? "#1c232c" : "#161b22";
      ctx.fillRect(x0, RULER_HEIGHT, x1 - x0, CLIP_HEIGHT);
      ctx.strokeStyle = "#2a313c";
      ctx.strokeRect(x0 + 0.5, RULER_HEIGHT + 0.5, x1 - x0 - 1, CLIP_HEIGHT - 1);

      // Filename, clipped to the clip's bounds so it never bleeds into neighbours.
      const labelMargin = 6;
      const innerWidth = x1 - x0 - labelMargin * 2;
      if (innerWidth >= 24) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(x0 + labelMargin, RULER_HEIGHT, innerWidth, CLIP_HEIGHT);
        ctx.clip();
        ctx.fillStyle = "#e6edf3";
        ctx.font = "11px sans-serif";
        ctx.fillText(clip.filename, x0 + labelMargin, RULER_HEIGHT + CLIP_HEIGHT - 10);
        ctx.restore();
      }
      acc += clip.frame_count;
    });

    // Cut regions (matched cut_start <-> cut_end pairs).
    const clipMap = new Map(clips.map((c, i) => [c.id, { idx: i, offset: clipOffsets[i] }]));
    const { regions: cuts, orphanIds: cutOrphans } = analyzeCuts(events);
    const { orphanIds: focusOrphans } = analyzeFocus(events);
    // Union so the timeline marker renders the focus-orphan FocusIn
    // dot in red (same hollow-ring treatment as orphan cuts) - matches
    // the EventList row badge.
    const orphanIds = new Set([...cutOrphans, ...focusOrphans]);
    for (const r of cuts) {
      const x0 = px(r.start);
      const x1 = px(r.end);
      ctx.fillStyle = "rgba(248, 81, 73, 0.18)";
      ctx.fillRect(x0, RULER_HEIGHT, x1 - x0, CLIP_HEIGHT);
      // A subtle outline so the region reads even on a short cut.
      ctx.strokeStyle = "rgba(248, 81, 73, 0.55)";
      ctx.lineWidth = 1;
      ctx.strokeRect(x0 + 0.5, RULER_HEIGHT + 0.5, Math.max(0, x1 - x0 - 1), CLIP_HEIGHT - 1);
    }

    // Events.
    for (const ev of events) {
      const f = eventGlobalFrame(ev, clipMap);
      if (f === null) continue;
      const x = px(f);
      // Off-screen culling.
      if (x < visibleStart - 8 || x > visibleEnd + 8) continue;
      const isOrphan = orphanIds.has(ev.id);
      const color = isOrphan ? "#f85149" : (EVENT_COLORS[ev.type] || EVENT_COLORS.default);
      ctx.strokeStyle = color;
      ctx.lineWidth = ev.id === selectedEventId ? 3 : 1.5;
      ctx.beginPath();
      ctx.moveTo(x, EVENT_BAND_TOP);
      ctx.lineTo(x, TIMELINE_HEIGHT - 4);
      ctx.stroke();
      // Orphan cuts get a hollow ring so they stand out from valid markers.
      if (isOrphan) {
        ctx.fillStyle = "#0e1116";
        ctx.beginPath();
        ctx.arc(x, EVENT_BAND_TOP, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();
      } else {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(x, EVENT_BAND_TOP, 4, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Playhead.
    const playX = px(currentFrame);
    ctx.strokeStyle = "#ffd33d";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playX, 0);
    ctx.lineTo(playX, TIMELINE_HEIGHT);
    ctx.stroke();
  }, [
    viewWidth,
    contentWidth,
    totalFrames,
    fps,
    clips,
    events,
    currentFrame,
    selectedEventId,
    clipOffsets,
    zoom,
  ]);

  // After a zoom change, place the cursor-anchored frame back under the cursor.
  useLayoutEffect(() => {
    const pending = pendingScrollRef.current;
    if (!pending || !containerRef.current || totalFrames === 0) return;
    pendingScrollRef.current = null;
    const target = (pending.frame / totalFrames) * contentWidth - pending.cursorX;
    containerRef.current.scrollLeft = clamp(target, 0, contentWidth - viewWidth);
  }, [zoom, contentWidth, viewWidth, totalFrames]);

  // Re-render the canvas on horizontal scroll so labels/events update for the
  // visible window.
  const [, setScrollTick] = useState(0);
  function handleScroll() {
    setScrollTick((n) => n + 1);
  }

  // Wheel handling: vertical wheel = zoom, horizontal wheel (deltaX or
  // shift+wheel) = pan. We attach a native non-passive listener so we can
  // actually preventDefault - React's synthetic onWheel is passive and
  // can't suppress browser defaults like Firefox's alt+wheel history nav.
  const stateRef = useRef({ totalFrames, viewWidth, contentWidth, zoom, maxZoom });
  stateRef.current = { totalFrames, viewWidth, contentWidth, zoom, maxZoom };

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const handler = (e: WheelEvent) => {
      const s = stateRef.current;
      if (s.totalFrames === 0) return;

      // Always intercept so the page never scrolls / navigates from a wheel
      // gesture over the timeline.
      e.preventDefault();

      // Horizontal pan: explicit deltaX (trackpad / horizontal wheel) or
      // shift+wheel (the conventional "pan" modifier on most browsers).
      if (e.deltaX !== 0) {
        container.scrollLeft += e.deltaX;
      }
      if (e.shiftKey && e.deltaY !== 0) {
        container.scrollLeft += e.deltaY;
        return;
      }
      if (e.deltaY === 0) return;

      // Vertical wheel → zoom, centered on the cursor.
      const rect = container.getBoundingClientRect();
      const cursorX = clamp(e.clientX - rect.left, 0, s.viewWidth);
      const frame =
        ((container.scrollLeft + cursorX) / s.contentWidth) * s.totalFrames;
      const factor = Math.exp(-e.deltaY * 0.0025);
      const next = clamp(s.zoom * factor, MIN_ZOOM, s.maxZoom);
      if (Math.abs(next - s.zoom) < 1e-3) return;
      pendingScrollRef.current = { frame, cursorX };
      setZoom(next);
    };
    container.addEventListener("wheel", handler, { passive: false });
    return () => container.removeEventListener("wheel", handler);
  }, []);

  function handleClick(e: React.MouseEvent<HTMLCanvasElement>) {
    if (totalFrames === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left; // canvas-relative; already accounts for scroll
    const y = e.clientY - rect.top;
    const frame = clamp(Math.round((x / contentWidth) * totalFrames), 0, totalFrames - 1);
    const clipMap = new Map(clips.map((c, i) => [c.id, { idx: i, offset: clipOffsets[i] }]));

    // First: precise hit on an event marker (used to be the only
    // event-selection path). Tight tolerance so the user can still
    // drop a seek between two adjacent events.
    if (y >= EVENT_BAND_TOP - 8) {
      const tolerance = (8 / contentWidth) * totalFrames;
      let best: { ev: EventDto; dist: number } | null = null;
      for (const ev of events) {
        const f = eventGlobalFrame(ev, clipMap);
        if (f === null) continue;
        const d = Math.abs(f - frame);
        if (d <= tolerance && (best === null || d < best.dist)) best = { ev, dist: d };
      }
      if (best) {
        onSelectEvent(best.ev.id);
        return;
      }
    }

    // Plain seek anywhere else on the timeline (ruler / clips bar /
    // empty space). Also focus the closest event so the event list
    // scrolls to where the user is looking - keeping the two views in
    // sync without a second click.
    onSeek(frame);
    let closest: { id: number; dist: number } | null = null;
    for (const ev of events) {
      const f = eventGlobalFrame(ev, clipMap);
      if (f === null) continue;
      const d = Math.abs(f - frame);
      if (closest === null || d < closest.dist) closest = { id: ev.id, dist: d };
    }
    if (closest) onSelectEvent(closest.id);
  }

  const zoomPct = Math.round(zoom * 100);

  return (
    <div style={{ position: "relative" }}>
      <div
        ref={containerRef}
        className="timeline-scroll"
        onScroll={handleScroll}
        style={{
          overflowX: "auto",
          overflowY: "hidden",
          height: TIMELINE_HEIGHT,
          background: "#0e1116",
        }}
      >
        <canvas
          ref={canvasRef}
          className="timeline-canvas"
          onClick={handleClick}
          style={{ display: "block", height: TIMELINE_HEIGHT, cursor: "pointer" }}
        />
      </div>
      <div
        style={{
          position: "absolute",
          top: 4,
          right: 8,
          fontSize: 10,
          color: "#8b949e",
          background: "rgba(14,17,22,0.8)",
          padding: "2px 6px",
          borderRadius: 3,
          pointerEvents: zoom > 1 ? "auto" : "none",
          userSelect: "none",
          fontFamily: "ui-monospace, monospace",
        }}
        title="Scroll to zoom · Shift+scroll or sideways scroll to pan"
        onClick={() => {
          if (zoom > 1) {
            pendingScrollRef.current = null;
            setZoom(1);
          }
        }}
      >
        {zoom > 1 ? `${zoomPct}% (click to reset)` : "scroll to zoom · shift+scroll to pan"}
      </div>
    </div>
  );
}

function eventGlobalFrame(
  ev: EventDto,
  clipMap: Map<string, { idx: number; offset: number }>
): number | null {
  if (ev.global_frame !== null && ev.global_frame !== undefined) return ev.global_frame;
  if (ev.type === "clip_transition") {
    const fromId = ev.payload["from_clip_id"] as string | undefined;
    if (!fromId) return null;
    const m = clipMap.get(fromId);
    if (!m) return null;
    return m.offset;
  }
  return null;
}

