import { useRef, useState } from "react";

/**
 * Square logo control that sits next to a team's color swatch.
 *
 * Deliberately dumb: the caller owns the upload/delete calls (team logos
 * go to `/teams/{team}/logo`, opponent logos to the match-scoped
 * endpoint), this component only handles picking a file, previewing, and
 * showing progress/errors.
 *
 * Preview strategy: the stored filename is fixed (`logo.png`), so the
 * browser would happily serve a stale cached image after a replacement.
 * The caller passes a changing `version` (a timestamp) which is appended
 * to the URL as a cache-buster. Until the first successful load we hide
 * the <img> so a missing logo shows the "+" placeholder instead of a
 * broken-image icon.
 */
interface Props {
  /** URL of the current logo, or null when none is set. */
  url: string | null;
  /** Alt text / tooltip subject, e.g. "Eastlake". */
  label: string;
  /** Called with the picked file; should upload and then bump `url`. */
  onUpload: (file: File) => Promise<void>;
  /** Called when the user clears the logo. Omit to hide the remove action. */
  onRemove?: () => Promise<void>;
  /** Edge length in px. Default 28 (matches the color swatch height). */
  size?: number;
  disabled?: boolean;
}

const ACCEPT = "image/png,image/webp,image/jpeg,image/gif,image/bmp";

export function LogoPicker({
  url,
  label,
  onUpload,
  onRemove,
  size = 28,
  disabled = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // `url` being set doesn't guarantee the file exists (a roster can
  // reference a logo that was deleted outside the app), so trust the
  // <img> load event rather than the prop alone.
  const [loaded, setLoaded] = useState(false);

  async function pick(file: File | undefined) {
    if (!file) return;
    setErr(null);
    setBusy(true);
    try {
      await onUpload(file);
      setLoaded(false); // force the <img> to re-evaluate the new URL
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(e: React.MouseEvent) {
    e.stopPropagation();
    if (!onRemove) return;
    setErr(null);
    setBusy(true);
    try {
      await onRemove();
      setLoaded(false);
    } catch (e2) {
      setErr(String(e2));
    } finally {
      setBusy(false);
    }
  }

  const showImage = !!url;
  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        style={{ display: "none" }}
        onChange={(e) => {
          void pick(e.target.files?.[0]);
          // Reset so picking the SAME file again still fires onChange.
          e.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled || busy}
        title={
          err ??
          (showImage
            ? `${label} logo - click to replace`
            : `Add a ${label} logo (used for YouTube thumbnails)`)
        }
        aria-label={`${label} logo`}
        style={{
          width: size,
          height: size,
          padding: 0,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
          borderRadius: 4,
          borderColor: err ? "var(--danger)" : undefined,
          background: "var(--bg)",
          cursor: disabled ? "not-allowed" : "pointer",
          flex: "0 0 auto",
        }}
      >
        {busy ? (
          <span style={{ fontSize: 10 }}>…</span>
        ) : (
          <>
            {showImage && (
              <img
                src={url}
                alt=""
                onLoad={() => setLoaded(true)}
                onError={() => setLoaded(false)}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "contain",
                  display: loaded ? "block" : "none",
                }}
              />
            )}
            {!loaded && (
              <span style={{ fontSize: 12, color: "var(--text-dim)" }}>+</span>
            )}
          </>
        )}
      </button>
      {loaded && onRemove && !disabled && (
        <button
          type="button"
          onClick={remove}
          title={`Remove ${label} logo`}
          aria-label={`Remove ${label} logo`}
          style={{
            position: "absolute",
            top: -6,
            right: -6,
            width: 14,
            height: 14,
            padding: 0,
            lineHeight: "12px",
            fontSize: 10,
            borderRadius: 7,
            background: "var(--bg-elev-2)",
          }}
        >
          ×
        </button>
      )}
    </span>
  );
}
