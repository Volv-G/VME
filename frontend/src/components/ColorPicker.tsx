import { useEffect, useRef, useState } from "react";
import { HexColorPicker } from "react-colorful";

interface Props {
  value: string;
  onChange: (hex: string) => void;
  /** Class applied to the trigger swatch. Lets callers reuse an existing
   *  visual (e.g. `team-swatch`) instead of the default 36x28 chip. */
  swatchClassName?: string;
  /** Extra inline styles merged over the default swatch styling. The
   *  background is always the current color. */
  swatchStyle?: React.CSSProperties;
  /** Tooltip / aria hint for the trigger. Defaults to "Pick color". */
  title?: string;
  /** Anchor the popover to the right edge of the swatch instead of the
   *  left - avoids overflowing when the swatch sits near a panel edge. */
  align?: "left" | "right";
}

interface EyeDropperResult {
  sRGBHex: string;
}

interface EyeDropperLike {
  open: () => Promise<EyeDropperResult>;
}

interface WindowWithEyeDropper extends Window {
  EyeDropper?: { new (): EyeDropperLike };
}

interface CapturedScreen {
  imageData: ImageData;
  width: number;
  height: number;
  dataUrl: string;
}

function rgbToHex(r: number, g: number, b: number): string {
  return (
    "#" +
    [r, g, b]
      .map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, "0"))
      .join("")
  );
}

export function ColorPicker({
  value,
  onChange,
  swatchClassName,
  swatchStyle,
  title = "Pick color",
  align = "left",
}: Props) {
  const [open, setOpen] = useState(false);
  const [hex, setHex] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [capture, setCapture] = useState<CapturedScreen | null>(null);
  const [capturing, setCapturing] = useState(false);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const swatchRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => { setHex(value); }, [value]);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      const t = e.target as Node;
      if (popoverRef.current?.contains(t) || swatchRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function commit(next: string) {
    setHex(next);
    if (/^#[0-9a-fA-F]{6}$/.test(next)) onChange(next);
  }

  async function pickFromScreen() {
    setError(null);

    // Chromium path: native EyeDropper API (no screen-share prompt, can hover live).
    const w = window as WindowWithEyeDropper;
    if (typeof w.EyeDropper === "function") {
      try {
        const dropper = new w.EyeDropper();
        const result = await dropper.open();
        commit(result.sRGBHex);
      } catch (err) {
        const aborted = err instanceof DOMException && err.name === "AbortError";
        if (!aborted) setError(err instanceof Error ? err.message : String(err));
      }
      return;
    }

    // Firefox / fallback: capture a frame via Screen Capture API and pick from it.
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      setError("Screen capture is not supported by this browser.");
      return;
    }

    let stream: MediaStream | null = null;
    setCapturing(true);
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: false,
      });
      const video = document.createElement("video");
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;
      await video.play();

      // Wait until the video produces a real frame.
      await new Promise<void>((resolve, reject) => {
        const start = performance.now();
        const check = () => {
          if (video.videoWidth > 0 && video.videoHeight > 0) {
            resolve();
          } else if (performance.now() - start > 5000) {
            reject(new Error("Timed out waiting for screen capture frame."));
          } else {
            requestAnimationFrame(check);
          }
        };
        check();
      });

      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) throw new Error("Canvas 2D context unavailable.");
      ctx.drawImage(video, 0, 0);

      // Stop the stream immediately - we only need a single frame.
      stream.getTracks().forEach((t) => t.stop());
      stream = null;

      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      setCapture({
        imageData,
        width: canvas.width,
        height: canvas.height,
        dataUrl: canvas.toDataURL("image/png"),
      });
    } catch (err) {
      const denied =
        err instanceof DOMException &&
        (err.name === "NotAllowedError" || err.name === "AbortError");
      if (!denied) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      setCapturing(false);
    }
  }

  return (
    <>
      <div style={{ position: "relative", display: "inline-block" }}>
        <button
          ref={swatchRef}
          type="button"
          className={swatchClassName}
          onClick={() => setOpen((v) => !v)}
          title={title}
          style={{
            // A caller-supplied class owns the geometry (e.g. the 14px
            // `.team-swatch`); inline styles would win over the class, so
            // the default chip sizing is only applied when there is none.
            ...(swatchClassName
              ? { padding: 0 }
              : {
                  width: 36,
                  height: 28,
                  padding: 0,
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.2)",
                }),
            ...swatchStyle,
            background: hex,
            cursor: "pointer",
          }}
          aria-label={`Color ${hex}`}
        />
        {open && (
          <div
            ref={popoverRef}
            style={{
              position: "absolute",
              zIndex: 1000,
              top: "calc(100% + 6px)",
              ...(align === "right" ? { right: 0 } : { left: 0 }),
              background: "var(--bg-elev)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: 12,
              boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
            }}
          >
            <HexColorPicker color={hex} onChange={commit} />
            <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
              <span style={{ color: "var(--text-dim)", fontSize: 12 }}>#</span>
              <input
                value={hex.replace(/^#/, "")}
                onChange={(e) => commit("#" + e.target.value.replace(/^#/, ""))}
                style={{ width: 80, fontFamily: "ui-monospace, monospace" }}
                maxLength={6}
                spellCheck={false}
              />
              <button
                type="button"
                onClick={pickFromScreen}
                disabled={capturing}
                title="Pick a color from anywhere on screen"
                style={{ marginLeft: "auto" }}
              >
                {capturing ? "Capturing..." : "Pick from screen"}
              </button>
            </div>
            {error && (
              <div
                style={{
                  marginTop: 8,
                  fontSize: 11,
                  color: "var(--text-dim)",
                  maxWidth: 220,
                  lineHeight: 1.4,
                }}
              >
                {error}
              </div>
            )}
          </div>
        )}
      </div>
      {capture && (
        <ScreenPickerModal
          capture={capture}
          onPick={(picked) => {
            commit(picked);
            setCapture(null);
          }}
          onCancel={() => setCapture(null)}
        />
      )}
    </>
  );
}

interface ModalProps {
  capture: CapturedScreen;
  onPick: (hex: string) => void;
  onCancel: () => void;
}

interface HoverInfo {
  cx: number;
  cy: number;
  px: number;
  py: number;
  hex: string;
}

function ScreenPickerModal({ capture, onPick, onCancel }: ModalProps) {
  const imgRef = useRef<HTMLImageElement | null>(null);
  const magCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hover, setHover] = useState<HoverInfo | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  function sampleAt(clientX: number, clientY: number): HoverInfo | null {
    const img = imgRef.current;
    if (!img) return null;
    const rect = img.getBoundingClientRect();
    const relX = clientX - rect.left;
    const relY = clientY - rect.top;
    if (relX < 0 || relY < 0 || relX > rect.width || relY > rect.height) return null;
    const px = Math.max(
      0,
      Math.min(capture.width - 1, Math.floor((relX / rect.width) * capture.width))
    );
    const py = Math.max(
      0,
      Math.min(capture.height - 1, Math.floor((relY / rect.height) * capture.height))
    );
    const i = (py * capture.width + px) * 4;
    const data = capture.imageData.data;
    return {
      cx: clientX,
      cy: clientY,
      px,
      py,
      hex: rgbToHex(data[i], data[i + 1], data[i + 2]),
    };
  }

  function onMove(e: React.MouseEvent) {
    setHover(sampleAt(e.clientX, e.clientY));
  }

  function onClick(e: React.MouseEvent) {
    const sampled = sampleAt(e.clientX, e.clientY);
    if (sampled) onPick(sampled.hex);
  }

  // Draw the magnifier whenever hover changes.
  useEffect(() => {
    if (!hover) return;
    const canvas = magCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const zoom = 12;
    const radius = 6; // pixels in each direction around cursor
    const size = (radius * 2 + 1) * zoom;
    if (canvas.width !== size) canvas.width = size;
    if (canvas.height !== size) canvas.height = size;
    ctx.imageSmoothingEnabled = false;
    const data = capture.imageData.data;
    for (let dy = -radius; dy <= radius; dy++) {
      for (let dx = -radius; dx <= radius; dx++) {
        const sx = hover.px + dx;
        const sy = hover.py + dy;
        let r = 24;
        let g = 24;
        let b = 24;
        if (sx >= 0 && sx < capture.width && sy >= 0 && sy < capture.height) {
          const i = (sy * capture.width + sx) * 4;
          r = data[i];
          g = data[i + 1];
          b = data[i + 2];
        }
        ctx.fillStyle = `rgb(${r},${g},${b})`;
        ctx.fillRect((dx + radius) * zoom, (dy + radius) * zoom, zoom, zoom);
      }
    }
    // Crosshair around the center pixel.
    const cx = radius * zoom;
    const cy = radius * zoom;
    ctx.strokeStyle = "rgba(0,0,0,0.9)";
    ctx.lineWidth = 1;
    ctx.strokeRect(cx - 0.5, cy - 0.5, zoom + 1, zoom + 1);
    ctx.strokeStyle = "rgba(255,255,255,0.95)";
    ctx.strokeRect(cx + 0.5, cy + 0.5, zoom - 1, zoom - 1);
  }, [hover, capture]);

  const magSize = (6 * 2 + 1) * 12;
  const tooltipLeft = hover
    ? Math.min(hover.cx + 18, window.innerWidth - magSize - 24)
    : 0;
  const tooltipTop = hover
    ? Math.min(hover.cy + 18, window.innerHeight - magSize - 56)
    : 0;

  return (
    <div
      role="dialog"
      aria-label="Pick a color from your screen"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 10000,
        background: "#000",
        cursor: "crosshair",
        userSelect: "none",
      }}
      onMouseMove={onMove}
      onClick={onClick}
    >
      <img
        ref={imgRef}
        src={capture.dataUrl}
        alt=""
        draggable={false}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "contain",
          display: "block",
          pointerEvents: "none",
        }}
      />
      <div
        style={{
          position: "absolute",
          top: 12,
          left: 12,
          right: 12,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          color: "#fff",
          fontSize: 13,
          background: "rgba(0,0,0,0.65)",
          padding: "6px 10px",
          borderRadius: 4,
          pointerEvents: "none",
        }}
      >
        <span>Click anywhere to pick a color. Press Esc to cancel.</span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onCancel();
          }}
          style={{ pointerEvents: "auto" }}
        >
          Cancel
        </button>
      </div>
      {hover && (
        <div
          style={{
            position: "fixed",
            left: tooltipLeft,
            top: tooltipTop,
            background: "rgba(0,0,0,0.85)",
            border: "1px solid rgba(255,255,255,0.25)",
            borderRadius: 6,
            padding: 6,
            pointerEvents: "none",
            color: "#fff",
            fontSize: 12,
            fontFamily: "ui-monospace, monospace",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 6,
          }}
        >
          <canvas
            ref={magCanvasRef}
            style={{ imageRendering: "pixelated", border: "1px solid #444" }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div
              style={{
                width: 12,
                height: 12,
                background: hover.hex,
                border: "1px solid #fff",
              }}
            />
            <span>{hover.hex.toUpperCase()}</span>
          </div>
        </div>
      )}
    </div>
  );
}
