import { useCallback, useEffect, useRef, useState } from "react";

/** The chosen square, in source-image pixels. */
export type Crop = { left: number; top: number; side: number };

/** Longest edge kept from the picked file; plenty for a 1024 px result. */
const SOURCE_MAX = 2048;
/** Exported photo edge, before clamping to what the crop really holds. */
const OUT_MAX = 1024;
const OUT_MIN = 256;
/** How far in one can zoom: the circle shows at least 1/8 of the short edge. */
const ZOOM_MAX = 8;
/** The circle's share of the frame; the rest shows what's being cut off. */
const CIRCLE = 0.78;

/**
 * Pick where the round player photo sits: drag to pan, wheel / pinch /
 * slider to zoom, rotate a quarter turn for sideways shots. The result is
 * a square JPEG of exactly the circle, so VME's overlays and the site
 * (which only re-crop non-square photos) show what was chosen here.
 */
export function PhotoCropper({
  file,
  initial,
  onDone,
  onCancel,
}: {
  file: File;
  initial?: { crop: Crop; turns: number };
  onDone: (result: { base64: string; url: string; crop: Crop; turns: number }) => void;
  onCancel: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [decoded, setDecoded] = useState<HTMLCanvasElement | null>(null);
  const [turns, setTurns] = useState(initial?.turns ?? 0);
  const [crop, setCrop] = useState<Crop | null>(null);
  const [size, setSize] = useState(320);
  const [error, setError] = useState<string | null>(null);

  // Decode once, shrunk so a 48 MP phone photo doesn't drag every redraw.
  useEffect(() => {
    let live = true;
    createImageBitmap(file)
      .then((bitmap) => {
        const scale = Math.min(1, SOURCE_MAX / Math.max(bitmap.width, bitmap.height));
        const c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(bitmap.width * scale));
        c.height = Math.max(1, Math.round(bitmap.height * scale));
        c.getContext("2d")!.drawImage(bitmap, 0, 0, c.width, c.height);
        bitmap.close();
        if (live) setDecoded(c);
      })
      .catch(() => live && setError("That file couldn't be read as a photo."));
    return () => {
      live = false;
    };
  }, [file]);

  // The decoded image turned by `turns` quarter turns.
  const [source, setSource] = useState<HTMLCanvasElement | null>(null);
  useEffect(() => {
    if (!decoded) return;
    const t = ((turns % 4) + 4) % 4;
    if (t === 0) {
      setSource(decoded);
      return;
    }
    const c = document.createElement("canvas");
    c.width = t % 2 ? decoded.height : decoded.width;
    c.height = t % 2 ? decoded.width : decoded.height;
    const ctx = c.getContext("2d")!;
    ctx.translate(c.width / 2, c.height / 2);
    ctx.rotate((t * Math.PI) / 2);
    ctx.drawImage(decoded, -decoded.width / 2, -decoded.height / 2);
    setSource(c);
  }, [decoded, turns]);

  // A fresh source gets the starting crop: the whole short edge,
  // centered across and nudged up toward the face, as VME crops.
  const firstSource = useRef(true);
  useEffect(() => {
    if (!source) return;
    if (firstSource.current && initial) {
      setCrop(clamp(initial.crop, source));
    } else {
      const side = Math.min(source.width, source.height);
      setCrop({ left: (source.width - side) / 2, top: (source.height - side) * 0.22, side });
    }
    firstSource.current = false;
    // `initial` only matters for the first source.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  // Frame size follows the layout (phones vs desktop).
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setSize(Math.round(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const diameter = size * CIRCLE;
  const origin = (size - diameter) / 2;

  // Redraw: the photo, a dimmed surround, the circle's edge.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !source || !crop) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const s = diameter / crop.side;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(source, origin - crop.left * s, origin - crop.top * s, source.width * s, source.height * s);
    ctx.beginPath();
    ctx.rect(0, 0, size, size);
    ctx.arc(size / 2, size / 2, diameter / 2, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0, 0, 0, 0.6)";
    ctx.fill("evenodd");
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, diameter / 2, 0, Math.PI * 2);
    ctx.lineWidth = 2;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
    ctx.stroke();
  }, [source, crop, size, diameter, origin]);

  /** Zoom to `side(current)`, keeping the image point under frame point (px, py) still. */
  const zoomTo = useCallback(
    (side: (c: Crop) => number, px = size / 2, py = size / 2) => {
      if (!source) return;
      setCrop((c) => {
        if (!c) return c;
        const next = Math.min(Math.max(side(c), minSide(source)), maxSide(source));
        const ix = c.left + ((px - origin) * c.side) / diameter;
        const iy = c.top + ((py - origin) * c.side) / diameter;
        return clamp(
          { left: ix - ((px - origin) * next) / diameter, top: iy - ((py - origin) * next) / diameter, side: next },
          source,
        );
      });
    },
    [source, size, origin, diameter],
  );

  const panBy = useCallback(
    (dx: number, dy: number) => {
      if (!source) return;
      setCrop((c) =>
        c && clamp({ ...c, left: c.left - (dx * c.side) / diameter, top: c.top - (dy * c.side) / diameter }, source),
      );
    },
    [source, diameter],
  );

  // Pointers: one drags, two pinch (and drag by their midpoint).
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  function local(e: React.PointerEvent) {
    const r = canvasRef.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  function onPointerDown(e: React.PointerEvent) {
    canvasRef.current!.setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, local(e));
  }
  function onPointerMove(e: React.PointerEvent) {
    const map = pointers.current;
    const prev = map.get(e.pointerId);
    if (!prev || !crop) return;
    const now = local(e);
    if (map.size === 1) {
      panBy(now.x - prev.x, now.y - prev.y);
    } else if (map.size === 2) {
      const other = [...map.entries()].find(([id]) => id !== e.pointerId)![1];
      const before = Math.hypot(prev.x - other.x, prev.y - other.y);
      const after = Math.hypot(now.x - other.x, now.y - other.y);
      panBy((now.x - prev.x) / 2, (now.y - prev.y) / 2);
      if (before > 0 && after > 0) zoomTo((c) => (c.side * before) / after, (now.x + other.x) / 2, (now.y + other.y) / 2);
    }
    map.set(e.pointerId, now);
  }
  function onPointerUp(e: React.PointerEvent) {
    pointers.current.delete(e.pointerId);
  }

  // Wheel / trackpad zoom; React's onWheel is passive, so attach by hand.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el || !crop) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomTo((c) => c.side * Math.exp(e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [crop, zoomTo]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (!crop) return;
    const step = e.shiftKey ? 40 : 10;
    const keys: Record<string, () => void> = {
      ArrowLeft: () => panBy(step, 0),
      ArrowRight: () => panBy(-step, 0),
      ArrowUp: () => panBy(0, step),
      ArrowDown: () => panBy(0, -step),
      "+": () => zoomTo((c) => c.side / 1.1),
      "=": () => zoomTo((c) => c.side / 1.1),
      "-": () => zoomTo((c) => c.side * 1.1),
    };
    const action = keys[e.key];
    if (action) {
      e.preventDefault();
      action();
    }
  }

  function done() {
    if (!source || !crop) return;
    const out = Math.round(Math.min(OUT_MAX, Math.max(OUT_MIN, crop.side)));
    const c = document.createElement("canvas");
    c.width = out;
    c.height = out;
    const ctx = c.getContext("2d")!;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(source, crop.left, crop.top, crop.side, crop.side, 0, 0, out, out);
    const url = c.toDataURL("image/jpeg", 0.9);
    onDone({ base64: url.slice(url.indexOf(",") + 1), url, crop, turns });
  }

  if (error) return <p className="error">{error}</p>;

  // The slider runs from "whole short edge" (0) to fully zoomed in (100),
  // on a log scale so each step feels the same.
  const zoom =
    source && crop ? Math.round((100 * Math.log(maxSide(source) / crop.side)) / Math.log(ZOOM_MAX)) : 0;

  return (
    <div className="cropper">
      <canvas
        ref={canvasRef}
        className="cropper-frame"
        tabIndex={0}
        aria-label="Photo position: drag to move, pinch or scroll to zoom, arrow keys to nudge"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onKeyDown}
      />
      {!crop && <p className="muted small">Loading photo…</p>}
      <div className="cropper-tools">
        <button type="button" onClick={() => source && crop && zoomTo((c) => c.side * 1.25)} aria-label="Zoom out">
          −
        </button>
        <input
          type="range"
          min={0}
          max={100}
          value={zoom}
          aria-label="Zoom"
          onChange={(e) =>
            source && zoomTo(() => maxSide(source) / Math.pow(ZOOM_MAX, Number(e.target.value) / 100))
          }
        />
        <button type="button" onClick={() => source && crop && zoomTo((c) => c.side / 1.25)} aria-label="Zoom in">
          +
        </button>
        <button type="button" onClick={() => setTurns((t) => t + 1)} title="Rotate a quarter turn" aria-label="Rotate">
          ↻
        </button>
      </div>
      <p className="muted small">Drag to move the photo, pinch or scroll to zoom. What's inside the circle is kept.</p>
      <div className="actions">
        <button type="button" className="primary" onClick={done} disabled={!crop}>
          Use this
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function maxSide(img: HTMLCanvasElement) {
  return Math.min(img.width, img.height);
}

function minSide(img: HTMLCanvasElement) {
  return Math.min(maxSide(img), Math.max(32, maxSide(img) / ZOOM_MAX));
}

/** Keep the square inside the image (the circle never shows empty space). */
function clamp(c: Crop, img: HTMLCanvasElement): Crop {
  const side = Math.min(Math.max(c.side, minSide(img)), maxSide(img));
  return {
    side,
    left: Math.min(Math.max(c.left, 0), img.width - side),
    top: Math.min(Math.max(c.top, 0), img.height - side),
  };
}
