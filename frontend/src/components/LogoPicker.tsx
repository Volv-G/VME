import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { LogoEditor, renderLogo, type LogoEdit } from "./LogoEditor";
import { Modal } from "./Modal";
import type { LogoCleanup } from "../types/api";

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
 *
 * Clicking it opens a small menu - upload a file, or paste an image
 * from the clipboard ("Copy image" in a browser, a screenshot). Ctrl+V
 * pastes too, while the menu is open or the pointer is over the logo;
 * a paste into a text field is left alone.
 *
 * Either way the image opens in the logo editor first (LogoEditor, the
 * same one as the club search's): background taken out, trimmed,
 * cropped to a rectangle or circle; only what is saved there goes to
 * onUpload, always a PNG. With onColors the editor's eyedropper and the
 * logo's own colours are offered too, saved alongside.
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
  /** Offer the logo's colours (palette, eyedropper); called on save
   *  with those chosen, in order - the first is the main one. */
  onColors?: (colors: string[]) => Promise<void>;
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
  onColors,
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
  // A new address (a replaced logo comes with a new `version`) waits for
  // its own load event. Reset here and only here: resetting after an
  // upload raced the new image's load - when it had already loaded, the
  // reset stuck and the "+" stayed until a page reload.
  useEffect(() => setLoaded(false), [url]);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  // The image being edited before it's saved.
  const [editing, setEditing] = useState<{ id: string; initial?: Partial<LogoCleanup> } | null>(null);
  const [menu, setMenu] = useState(false);

  // The menu closes on Esc or a click anywhere else.
  useEffect(() => {
    if (!menu) return;
    const away = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenu(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [menu]);

  /** "Edit image": the current logo back into the editor. It was saved
   *  cut out already, so it opens with background removal off. */
  async function editCurrent() {
    if (!url) return;
    setMenu(false);
    setErr(null);
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const blob = await r.blob();
      // Saved as wanted already: every cleanup off, so it opens exactly
      // as it is (a trim would take off a margin left on purpose).
      await pick(new File([blob], "logo.png", { type: blob.type || "image/png" }), {
        remove_bg: false,
        trim: false,
        square: false,
      });
    } catch (e) {
      setErr(`The logo couldn't be opened: ${e instanceof Error ? e.message : e}`);
    }
  }

  /** "Paste image": read the clipboard directly. Browsers allow that
   *  only over https (or on localhost) and may ask first; Ctrl+V works
   *  everywhere, so the message points there when it can't. */
  async function pasteFromClipboard() {
    setErr(null);
    try {
      if (!navigator.clipboard?.read) throw new Error("unavailable");
      for (const item of await navigator.clipboard.read()) {
        const type = item.types.find((t) => t.startsWith("image/"));
        if (!type) continue;
        const blob = await item.getType(type);
        setMenu(false);
        void pick(new File([blob], "pasted." + (type.split("/")[1] || "png"), { type }));
        return;
      }
      setErr("No image on the clipboard - copy one first (right-click an image, Copy image)");
    } catch (e) {
      if (e instanceof Error && e.message === "unavailable") {
        setErr("This browser can't read the clipboard from a button here - press Ctrl+V instead");
      } else {
        setErr("The clipboard couldn't be read - press Ctrl+V instead");
      }
    }
  }

  /** A picked or pasted file: into the editor. */
  async function pick(file: File | undefined, initial?: Partial<LogoCleanup>) {
    if (!file) return;
    setErr(null);
    setBusy(true);
    try {
      const keepBackground = !initial && (await seeThroughCorners(file).catch(() => false));
      const { id } = await api.uploadLogoSource(file);
      setEditing({ id, initial: initial ?? (keepBackground ? { remove_bg: false } : undefined) });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save(file: File, colors: string[]) {
    await onUpload(file);
    if (colors.length && onColors) await onColors(colors);
    setEditing(null);
  }

  // The paste listener lives on the document (a button gets no paste of
  // its own) and only while this logo is pointed at or focused.
  const pickRef = useRef(pick);
  pickRef.current = pick;
  const armed = (hovered || focused || menu) && !disabled && !busy && !editing;
  useEffect(() => {
    if (!armed) return;
    const onPaste = (e: ClipboardEvent) => {
      // Already taken (the club search panel takes pastes while open).
      if (e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if (typing && !wrapRef.current?.contains(t)) return;
      const img = Array.from(e.clipboardData?.files ?? []).find((x) => x.type.startsWith("image/"));
      e.preventDefault();
      if (!img) {
        setErr("No image on the clipboard - copy one first (right-click an image, Copy image)");
        return;
      }
      const ext = img.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
      setMenu(false);
      void pickRef.current(new File([img], img.name || `pasted.${ext}`, { type: img.type }));
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [armed]);

  async function remove(e: React.MouseEvent) {
    e.stopPropagation();
    if (!onRemove) return;
    setErr(null);
    setBusy(true);
    try {
      await onRemove();
    } catch (e2) {
      setErr(String(e2));
    } finally {
      setBusy(false);
    }
  }

  const showImage = !!url;
  return (
    <span
      ref={wrapRef}
      style={{ position: "relative", display: "inline-block" }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={(e) => {
        if (!wrapRef.current?.contains(e.relatedTarget as Node | null)) setFocused(false);
      }}
    >
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
        onClick={() => {
          setErr(null);
          setMenu((m) => !m);
        }}
        disabled={disabled || busy}
        title={
          err ??
          (showImage
            ? `${label} logo - click to upload or paste another`
            : `Add a ${label} logo (used for YouTube thumbnails) - click to upload or paste one`)
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
          // Lit while it's pointed at or focused: that's when Ctrl+V
          // pastes into it.
          borderColor: err ? "var(--danger)" : armed ? "var(--accent)" : undefined,
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
      {menu && (
        <span className="logo-menu" role="menu">
          {loaded && (
            <button type="button" onClick={() => void editCurrent()}>
              Edit image…
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setMenu(false);
              inputRef.current?.click();
            }}
          >
            Upload image…
          </button>
          <button type="button" onClick={() => void pasteFromClipboard()}>
            Paste image
          </button>
          <span className="muted">or press Ctrl+V now</span>
          {err && <span className="logo-menu-err">{err}</span>}
        </span>
      )}
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
      {editing && (
        <LogoEditDialog
          source={editing.id}
          initial={editing.initial}
          label={label}
          withColors={!!onColors}
          onCancel={() => setEditing(null)}
          onSave={save}
        />
      )}
    </span>
  );
}

/** Already transparent at the corners: a logo cut out before, whose
 *  background needs no removing. */
async function seeThroughCorners(file: File): Promise<boolean> {
  const bmp = await createImageBitmap(file);
  const c = document.createElement("canvas");
  c.width = c.height = 32;
  const ctx = c.getContext("2d")!;
  ctx.drawImage(bmp, 0, 0, 32, 32);
  bmp.close();
  const d = ctx.getImageData(0, 0, 32, 32).data;
  return [0, 31, 32 * 31, 32 * 32 - 1].every((p) => d[p * 4 + 3] < 128);
}

function LogoEditDialog({
  source,
  initial,
  label,
  withColors,
  onCancel,
  onSave,
}: {
  source: string;
  initial?: Partial<LogoCleanup>;
  label: string;
  withColors: boolean;
  onCancel: () => void;
  onSave: (file: File, colors: string[]) => Promise<void>;
}) {
  const [edit, setEdit] = useState<LogoEdit | null>(null);
  const [picks, setPicks] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (hex: string) =>
    setChosen((c) => (c.includes(hex) ? c.filter((x) => x !== hex) : c.length < 6 ? [...c, hex] : c));
  const pickColor = (hex: string) => {
    setPicks((p) => (p.includes(hex) ? p : [...p, hex].slice(-8)));
    setChosen((c) => (c.includes(hex) || c.length >= 6 ? c : [...c, hex]));
  };

  async function save() {
    if (!edit) return;
    setSaving(true);
    setErr(null);
    try {
      const blob = await renderLogo(edit);
      await onSave(new File([blob], "logo.png", { type: "image/png" }), chosen);
    } catch (e) {
      setErr(String(e));
      setSaving(false);
    }
  }

  const rows: [string, string[]][] = [];
  if (edit?.colors.length) rows.push(["From the logo", edit.colors]);
  if (picks.length) rows.push(["Picked", picks]);
  const saveLabel = chosen.length
    ? "Save logo and " + chosen.length + (chosen.length === 1 ? " colour" : " colours")
    : "Save logo";
  return (
    <Modal open title={label + " logo"} onClose={onCancel} width="min(680px, 94vw)">
      <LogoEditor
        source={source}
        initial={initial}
        onChange={setEdit}
        onPick={withColors ? pickColor : undefined}
      />
      {withColors && rows.length > 0 && (
        <>
          <h3 className="club-lookup-h">Colours</h3>
          {rows.map(([title, hexes]) => (
            <div key={title} className="club-lookup-colors">
              <span className="muted club-lookup-colsrc">{title}</span>
              {hexes.map((h) => {
                const n = chosen.indexOf(h);
                return (
                  <button
                    key={h}
                    className={"club-lookup-swatch" + (n >= 0 ? " picked" : "")}
                    style={{ background: h }}
                    title={h}
                    onClick={() => toggle(h)}
                  >
                    {n >= 0 && <span>{n + 1}</span>}
                  </button>
                );
              })}
            </div>
          ))}
          <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>
            {chosen.length
              ? "Numbered in the order picked; 1 is the main colour. They replace the current colours."
              : "Pick colours to replace the current ones, or leave them as they are."}
          </p>
        </>
      )}
      {err && <div className="error">{err}</div>}
      <div className="toolbar" style={{ marginTop: 12, justifyContent: "flex-end" }}>
        <button onClick={onCancel}>Cancel</button>
        <button className="primary" onClick={() => void save()} disabled={!edit || saving}>
          {saving ? "Saving…" : saveLabel}
        </button>
      </div>
    </Modal>
  );
}
