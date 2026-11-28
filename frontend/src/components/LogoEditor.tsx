import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { LogoCleanup } from "../types/api";
import { ColorPicker } from "./ColorPicker";

/**
 * Cleans up and crops a logo: the found badge in the club search, or an
 * image uploaded or pasted into any logo picker.
 *
 * The cleanup happens on the server (logo_tools: background taken out,
 * margin trimmed, squared), which hands back a PNG with transparency;
 * the crop happens here, on that PNG - drag to pan, wheel or slider to
 * zoom, and a rectangle or circle frame dragged and resized over it.
 * Outside a circle comes out see-through. A frame may reach past the
 * image, which leaves a margin.
 *
 * The background is saved too: see-through, white or any colour, filling
 * the frame (a disc, for a circle). The stage shows it, and a small
 * "Result" shows exactly what will be saved.
 *
 * "Pick colour" turns the stage into an eyedropper: a click passes the
 * colour under the pointer to `onPick` (the caller adds it to the
 * colours being chosen).
 *
 * The caller gets the state through `onChange` and makes the final PNG
 * with `renderLogo` when the user saves.
 */

export const CHECKER = "repeating-conic-gradient(#8a8a8a 0 25%, #c4c4c4 0 50%) 50% / 14px 14px";
export const DEFAULT_CLEANUP: LogoCleanup = { remove_bg: true, tolerance: 40, holes: false, trim: true, square: false };

export type Shape = "none" | "rect" | "circle";
/** The crop, in pixels of the cleaned-up image. */
export type Frame = { x: number; y: number; w: number; h: number };

export interface LogoEdit {
  blob: Blob;
  url: string;
  /** The cleaned-up badge's main colours. */
  colors: string[];
  width: number;
  height: number;
  shape: Shape;
  frame: Frame | null;
  /** Saved behind the logo (inside the frame); null = see-through. */
  fill: string | null;
}

/** The PNG to save: the cleaned-up image, cropped as framed, on its
 *  background. */
export async function renderLogo(e: LogoEdit): Promise<Blob> {
  const cropped = e.shape !== "none" && !!e.frame;
  if (!cropped && !e.fill) return e.blob;
  const f = cropped && e.frame ? e.frame : { x: 0, y: 0, w: e.width, h: e.height };
  const bmp = await createImageBitmap(e.blob);
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(f.w));
  c.height = Math.max(1, Math.round(f.h));
  const ctx = c.getContext("2d")!;
  if (e.shape === "circle") {
    ctx.beginPath();
    ctx.ellipse(c.width / 2, c.height / 2, c.width / 2, c.height / 2, 0, 0, Math.PI * 2);
    ctx.clip();
  }
  if (e.fill) {
    ctx.fillStyle = e.fill;
    ctx.fillRect(0, 0, c.width, c.height);
  }
  ctx.drawImage(bmp, -f.x, -f.y, e.width, e.height);
  bmp.close();
  return new Promise((resolve, reject) =>
    c.toBlob((b) => (b ? resolve(b) : reject(new Error("Couldn't make the PNG"))), "image/png")
  );
}

/** "320 × 320 px" for what renderLogo will make. */
export function editSize(e: LogoEdit): string {
  const f = e.shape !== "none" && e.frame ? e.frame : { w: e.width, h: e.height };
  return `${Math.round(f.w)} × ${Math.round(f.h)} px`;
}

function defaultFrame(shape: Shape, w: number, h: number): Frame | null {
  if (shape === "rect") return { x: 0, y: 0, w, h };
  if (shape === "circle") {
    const s = Math.min(w, h);
    return { x: (w - s) / 2, y: (h - s) / 2, w: s, h: s };
  }
  return null;
}

const hex2 = (n: number) => n.toString(16).padStart(2, "0");

export function LogoEditor({
  source,
  initial,
  onChange,
  onPick,
}: {
  /** A club-lookup candidate id (a found image or an upload). */
  source: string;
  initial?: Partial<LogoCleanup>;
  onChange: (edit: LogoEdit | null) => void;
  onPick?: (hex: string) => void;
}) {
  const [opts, setOpts] = useState<LogoCleanup>({ ...DEFAULT_CLEANUP, ...initial });
  const [preview, setPreview] = useState<Omit<LogoEdit, "shape" | "frame" | "fill"> | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fill, setFill] = useState<string | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [shape, setShape] = useState<Shape>("none");
  const [frame, setFrame] = useState<Frame | null>(null);
  const [picking, setPicking] = useState(false);
  const [hoverHex, setHoverHex] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);
  const pixels = useRef<ImageData | null>(null);
  const sizeRef = useRef<{ w: number; h: number } | null>(null);
  const shapeRef = useRef(shape);
  shapeRef.current = shape;

  // Another image: start uncropped.
  useEffect(() => {
    setShape("none");
    setFrame(null);
    sizeRef.current = null;
  }, [source]);

  // The cleaned-up image, again whenever an option changes; the slider
  // waits for a pause.
  useEffect(() => {
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      setPreviewing(true);
      setError(null);
      try {
        const r = await api.lookupImage(source, opts, ctl.signal);
        const [w, h] = r.size.split("x").map(Number);
        const bmp = await createImageBitmap(r.blob);
        const width = w || bmp.width;
        const height = h || bmp.height;
        // Kept for the eyedropper.
        const c = document.createElement("canvas");
        c.width = bmp.width;
        c.height = bmp.height;
        const ctx = c.getContext("2d")!;
        ctx.drawImage(bmp, 0, 0);
        bmp.close();
        pixels.current = ctx.getImageData(0, 0, c.width, c.height);
        if (ctl.signal.aborted) return;
        const url = URL.createObjectURL(r.blob);
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = url;
        // Keep the crop where it was when the size changes (a different
        // trim): the same place relative to the image.
        const old = sizeRef.current;
        if (old && (old.w !== width || old.h !== height)) {
          const fx = width / old.w;
          const fy = height / old.h;
          const circle = shapeRef.current === "circle";
          setFrame((f) => {
            if (!f) return f;
            const n = { x: f.x * fx, y: f.y * fy, w: f.w * fx, h: f.h * fy };
            if (!circle) return n;
            const s = Math.min(n.w, n.h);
            return { x: n.x + (n.w - s) / 2, y: n.y + (n.h - s) / 2, w: s, h: s };
          });
        }
        sizeRef.current = { w: width, h: height };
        setPreview({ blob: r.blob, url, colors: r.colors, width, height });
      } catch (e) {
        if (!ctl.signal.aborted) {
          setError(String(e instanceof Error ? e.message : e));
          setPreview(null);
        }
      } finally {
        if (!ctl.signal.aborted) setPreviewing(false);
      }
    }, 250);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, opts]);
  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current); }, []);

  useEffect(() => {
    onChange(preview ? { ...preview, shape, frame, fill } : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview, shape, frame, fill]);

  // What Save will store, drawn small; after a pause, so dragging the
  // frame doesn't redraw it every move.
  useEffect(() => {
    if (!preview) {
      setResultUrl(null);
      return;
    }
    let live = true;
    let made: string | null = null;
    const t = setTimeout(async () => {
      try {
        const blob = await renderLogo({ ...preview, shape, frame, fill });
        if (!live) return;
        made = URL.createObjectURL(blob);
        setResultUrl(made);
      } catch {
        /* the stage still shows it */
      }
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
      if (made) URL.revokeObjectURL(made);
    };
  }, [preview, shape, frame, fill]);

  function chooseShape(s: Shape) {
    setShape(s);
    setFrame(preview ? defaultFrame(s, preview.width, preview.height) : null);
  }

  function sample(x: number, y: number): string | null {
    const d = pixels.current;
    if (!d || !preview) return null;
    // The eyedropper's copy may be smaller than the reported size.
    const px = Math.floor((x * d.width) / preview.width);
    const py = Math.floor((y * d.height) / preview.height);
    if (px < 0 || py < 0 || px >= d.width || py >= d.height) return null;
    const i = (py * d.width + px) * 4;
    if (d.data[i + 3] < 128) return null; // see-through: no colour there
    return `#${hex2(d.data[i])}${hex2(d.data[i + 1])}${hex2(d.data[i + 2])}`;
  }

  const set = (patch: Partial<LogoCleanup>) => setOpts((o) => ({ ...o, ...patch }));
  const small = { padding: "2px 8px", fontSize: 12 };

  return (
    <div className="logo-editor">
      <div>
        {preview ? (
          <CropStage
            src={preview.url}
            width={preview.width}
            height={preview.height}
            background={fill ?? CHECKER}
            shape={shape}
            frame={frame}
            onFrame={setFrame}
            dim={previewing}
            picking={picking && !!onPick}
            onHover={(x, y) => setHoverHex(x === null || y === null ? null : sample(x, y))}
            onPickAt={(x, y) => {
              const h = sample(x, y);
              if (h && onPick) onPick(h);
            }}
          />
        ) : (
          <div className="crop-stage crop-empty" style={{ background: fill ?? CHECKER }}>
            {!error && <span className="muted">…</span>}
          </div>
        )}
        <div className="toolbar" style={{ marginTop: 6, gap: 4 }} title="Saved behind the logo, inside the crop">
          <span className="muted" style={{ fontSize: 12, marginRight: 2 }}>Background</span>
          <button onClick={() => setFill(null)} className={fill === null ? "primary" : ""} style={small}>
            See-through
          </button>
          <button onClick={() => setFill("#ffffff")} className={fill === "#ffffff" ? "primary" : ""} style={small}>
            White
          </button>
          <ColorPicker
            value={fill && fill !== "#ffffff" ? fill : "#1b1d22"}
            onChange={(hex) => setFill(hex)}
            title="Background colour"
          />
        </div>
      </div>
      <div className="logo-editor-opts">
        <div className="toolbar" style={{ gap: 4 }} title="Crop: drag the frame to move it, its corners to resize">
          <span className="muted" style={{ fontSize: 12, marginRight: 2 }}>Crop</span>
          {(["none", "rect", "circle"] as Shape[]).map((s) => (
            <button key={s} onClick={() => chooseShape(s)} className={shape === s ? "primary" : ""} style={small}>
              {s === "none" ? "None" : s === "rect" ? "▭ Rectangle" : "◯ Circle"}
            </button>
          ))}
        </div>
        {onPick && (
          <div className="toolbar" style={{ gap: 6 }}>
            <button
              onClick={() => setPicking((p) => !p)}
              className={picking ? "primary" : ""}
              style={small}
              title="Click the logo to add the colour under the pointer"
            >
              🎨 Pick colour
            </button>
            {picking && (
              <span
                className="logo-editor-hover"
                style={{ background: hoverHex ?? "transparent" }}
                title={hoverHex ?? "Point at the logo"}
              />
            )}
            {picking && <span className="muted" style={{ fontSize: 12 }}>{hoverHex ?? "click the logo"}</span>}
          </div>
        )}
        <label>
          <input type="checkbox" checked={opts.remove_bg} onChange={(e) => set({ remove_bg: e.target.checked })} />{" "}
          Remove background
        </label>
        {opts.remove_bg && (
          <>
            <label title="How different from the background a colour must be to stay">
              Tolerance{" "}
              <input
                type="range"
                min={5}
                max={150}
                value={opts.tolerance}
                onChange={(e) => set({ tolerance: Number(e.target.value) })}
              />{" "}
              <span className="muted">{opts.tolerance}</span>
            </label>
            <label title="Also clear areas enclosed by the badge, like the inside of an O">
              <input type="checkbox" checked={opts.holes} onChange={(e) => set({ holes: e.target.checked })} /> Also
              enclosed areas
            </label>
          </>
        )}
        <label>
          <input type="checkbox" checked={opts.trim} onChange={(e) => set({ trim: e.target.checked })} /> Trim margin
        </label>
        <label>
          <input type="checkbox" checked={opts.square} onChange={(e) => set({ square: e.target.checked })} /> Make
          square
        </label>
        <div className="logo-editor-result">
          <span className="logo-editor-result-img" style={{ background: CHECKER }}>
            {resultUrl && <img src={resultUrl} alt="What will be saved" />}
          </span>
          <span className="muted" style={{ fontSize: 12 }}>
            Result: PNG{fill ? "" : " with transparency"}
            {preview ? ` · ${editSize({ ...preview, shape, frame, fill })}` : ""}
          </span>
        </div>
        {error && <div className="error">{error}</div>}
      </div>
    </div>
  );
}

const VIEW = 280;
const ZOOM_MAX = 8;
/** Below 1 the image shrinks inside the view - room to draw a frame
 *  well past it, for a logo small within a transparent margin. */
const ZOOM_MIN = 0.2;
const HANDLE = 10;

type Drag =
  | { kind: "pan"; sx: number; sy: number; px: number; py: number }
  | { kind: "move"; sx: number; sy: number; f: Frame }
  | { kind: "resize"; ax: number; ay: number };

/** The image under a movable, zoomable view, with the crop frame on top. */
function CropStage({
  src,
  width: iw,
  height: ih,
  background,
  shape,
  frame,
  onFrame,
  dim,
  picking,
  onHover,
  onPickAt,
}: {
  src: string;
  width: number;
  height: number;
  background: string;
  shape: Shape;
  frame: Frame | null;
  onFrame: (f: Frame) => void;
  dim: boolean;
  picking: boolean;
  onHover: (x: number | null, y: number | null) => void;
  onPickAt: (x: number, y: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<Drag | null>(null);
  // At 1x the image fills the stage, as it fills a logo tile - a margin
  // here read as one in the image. Zooming out makes room around it.
  const fit = Math.min(VIEW / iw, VIEW / ih) || 1;
  const scale = fit * zoom;
  const ox = VIEW / 2 + pan.x - (iw * scale) / 2;
  const oy = VIEW / 2 + pan.y - (ih * scale) / 2;
  const toImg = (vx: number, vy: number) => ({ x: (vx - ox) / scale, y: (vy - oy) / scale });
  const view = useRef({ zoom, pan, ox, oy, scale });
  view.current = { zoom, pan, ox, oy, scale };

  // Wheel zoom about the pointer. A native listener: React's is passive
  // and couldn't keep the page from scrolling.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const vx = e.clientX - r.left;
      const vy = e.clientY - r.top;
      const v = view.current;
      const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, v.zoom * Math.exp(-e.deltaY * 0.0015)));
      zoomAbout(z, vx, vy);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [iw, ih]);

  /** Zoom to `z`, keeping the image point under (vx, vy) where it is. */
  function zoomAbout(z: number, vx: number, vy: number) {
    const v = view.current;
    const px = (vx - v.ox) / v.scale;
    const py = (vy - v.oy) / v.scale;
    const s = fit * z;
    setZoom(z);
    setPan({ x: vx - px * s - VIEW / 2 + (iw * s) / 2, y: vy - py * s - VIEW / 2 + (ih * s) / 2 });
  }

  const fv = frame && shape !== "none"
    ? { x: ox + frame.x * scale, y: oy + frame.y * scale, w: frame.w * scale, h: frame.h * scale }
    : null;
  const corners = fv
    ? [
        [fv.x, fv.y],
        [fv.x + fv.w, fv.y],
        [fv.x, fv.y + fv.h],
        [fv.x + fv.w, fv.y + fv.h],
      ]
    : [];

  function point(e: React.PointerEvent) {
    const r = ref.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function down(e: React.PointerEvent) {
    if (e.button !== 0) return;
    const p = point(e);
    if (picking) {
      const q = toImg(p.x, p.y);
      onPickAt(q.x, q.y);
      return;
    }
    ref.current!.setPointerCapture(e.pointerId);
    const hit = corners.findIndex(([cx, cy]) => Math.abs(p.x - cx) <= HANDLE && Math.abs(p.y - cy) <= HANDLE);
    if (fv && frame && hit >= 0) {
      // The opposite corner stays put.
      const [ax, ay] = corners[3 - hit];
      const a = toImg(ax, ay);
      drag.current = { kind: "resize", ax: a.x, ay: a.y };
    } else if (fv && frame && p.x >= fv.x && p.x <= fv.x + fv.w && p.y >= fv.y && p.y <= fv.y + fv.h) {
      drag.current = { kind: "move", sx: p.x, sy: p.y, f: frame };
    } else {
      drag.current = { kind: "pan", sx: p.x, sy: p.y, px: pan.x, py: pan.y };
    }
  }

  function move(e: React.PointerEvent) {
    const p = point(e);
    if (picking) {
      const q = toImg(p.x, p.y);
      onHover(q.x, q.y);
      return;
    }
    const d = drag.current;
    if (!d) return;
    if (d.kind === "pan") {
      setPan({ x: d.px + p.x - d.sx, y: d.py + p.y - d.sy });
    } else if (d.kind === "move") {
      onFrame({ ...d.f, x: d.f.x + (p.x - d.sx) / scale, y: d.f.y + (p.y - d.sy) / scale });
    } else {
      const q = toImg(p.x, p.y);
      let w = q.x - d.ax;
      let h = q.y - d.ay;
      if (shape === "circle") {
        const s = Math.max(Math.abs(w), Math.abs(h));
        w = Math.sign(w || 1) * s;
        h = Math.sign(h || 1) * s;
      }
      const min = 8;
      if (Math.abs(w) < min) w = Math.sign(w || 1) * min;
      if (Math.abs(h) < min) h = Math.sign(h || 1) * min;
      onFrame({ x: Math.min(d.ax, d.ax + w), y: Math.min(d.ay, d.ay + h), w: Math.abs(w), h: Math.abs(h) });
    }
  }

  function up() {
    drag.current = null;
  }

  const cursor = picking ? "crosshair" : drag.current?.kind === "pan" ? "grabbing" : "grab";
  const outline = fv
    ? shape === "circle"
      ? `M ${fv.x} ${fv.y + fv.h / 2} a ${fv.w / 2} ${fv.h / 2} 0 1 0 ${fv.w} 0 a ${fv.w / 2} ${fv.h / 2} 0 1 0 ${-fv.w} 0 Z`
      : `M ${fv.x} ${fv.y} h ${fv.w} v ${fv.h} h ${-fv.w} Z`
    : "";

  return (
    <div>
      <div
        ref={ref}
        className="crop-stage"
        style={{ background, cursor }}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
        onPointerLeave={() => picking && onHover(null, null)}
      >
        <img
          src={src}
          alt="Logo, cleaned up"
          draggable={false}
          style={{ left: ox, top: oy, width: iw * scale, height: ih * scale, opacity: dim ? 0.5 : 1 }}
        />
        {fv && (
          <svg width={VIEW} height={VIEW} viewBox={`0 0 ${VIEW} ${VIEW}`}>
            <path d={`M 0 0 H ${VIEW} V ${VIEW} H 0 Z ${outline}`} fill="rgba(0,0,0,0.5)" fillRule="evenodd" />
            <path d={outline} fill="none" stroke="#fff" strokeWidth={1.5} strokeDasharray="5 4" />
            {shape === "circle" && (
              <rect x={fv.x} y={fv.y} width={fv.w} height={fv.h} fill="none" stroke="rgba(255,255,255,0.35)" />
            )}
            {corners.map(([cx, cy], i) => (
              <rect key={i} x={cx - 5} y={cy - 5} width={10} height={10} fill="#fff" stroke="#000" strokeWidth={1} />
            ))}
          </svg>
        )}
      </div>
      <div className="toolbar" style={{ marginTop: 6, gap: 6 }}>
        <span className="muted" style={{ fontSize: 12 }}>Zoom</span>
        <input
          type="range"
          // Logarithmic: halving and doubling are the same distance.
          min={Math.log(ZOOM_MIN)}
          max={Math.log(ZOOM_MAX)}
          step={0.01}
          value={Math.log(zoom)}
          onChange={(e) => zoomAbout(Math.exp(Number(e.target.value)), VIEW / 2, VIEW / 2)}
          style={{ flex: 1, minWidth: 80 }}
          aria-label="Zoom"
        />
        <button
          style={{ padding: "2px 8px", fontSize: 12 }}
          onClick={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
          }}
        >
          Fit
        </button>
      </div>
    </div>
  );
}
