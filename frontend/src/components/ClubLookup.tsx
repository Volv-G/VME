import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { ClubDto, LookupCandidate, LookupResult } from "../types/api";
import { CHECKER, LogoEditor, renderLogo, type LogoEdit } from "./LogoEditor";

/**
 * Find a club's badge and colours online and choose from what turns up.
 *
 * Searches with the club's name and its hints (a city, a state, the
 * club's website - kept on the club for next time). A badge picked opens
 * in the logo editor (LogoEditor: cleaned up, cropped, and an eyedropper
 * for its colours); the colours found are grouped by where they came
 * from, the badge's own and the ones picked from it among them. "Use"
 * saves the badge and/or the colours chosen (in the order clicked; the
 * first is the main one) onto the club.
 *
 * An image of one's own goes in alongside the results: pasted (Ctrl+V
 * anywhere while this is open) or uploaded, it shows first in the list
 * and opens in the editor like a found one.
 */

export function ClubLookup({
  club,
  current,
  onClose,
  onSaved,
}: {
  club: ClubDto;
  /** The club's own colours, editable; the row hides them while this is open. */
  current: ReactNode;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [hint, setHint] = useState(club.hint ?? "");
  const [result, setResult] = useState<LookupResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [broken, setBroken] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<string | null>(null);
  const [edit, setEdit] = useState<LogoEdit | null>(null);
  const [picks, setPicks] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  // Pasted or uploaded images, newest first.
  const [own, setOwn] = useState<LookupCandidate[]>([]);
  const [adding, setAdding] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  async function addImage(file: File, how: "Pasted" | "Uploaded") {
    setAdding(true);
    setError(null);
    try {
      const { id } = await api.uploadLogoSource(file);
      const c: LookupCandidate = { id, url: null, source: how, title: file.name || how + " image", page: null };
      setOwn((o) => [c, ...o.filter((x) => x.id !== id)]);
      setPicked(id);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setAdding(false);
    }
  }
  // Ctrl+V anywhere while the panel is open. Only an image is taken: a
  // paste of text (into the hints box) goes where it was going.
  const addRef = useRef(addImage);
  addRef.current = addImage;
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const img = Array.from(e.clipboardData?.files ?? []).find((x) => x.type.startsWith("image/"));
      if (!img) return;
      e.preventDefault();
      const ext = img.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
      void addRef.current(new File([img], img.name || "pasted." + ext, { type: img.type }), "Pasted");
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, []);

  async function search() {
    setSearching(true);
    setError(null);
    setPicked(null);
    setBroken(new Set());
    try {
      if (hint.trim() !== (club.hint ?? "")) await api.updateClub(club.id, { hint: hint.trim() });
      setResult(await api.lookupClub(club.name, hint.trim()));
    } catch (e) {
      setError(String(e));
    } finally {
      setSearching(false);
    }
  }
  // Straight away on opening - right after the club is added, typically.
  useEffect(() => {
    void search();
  }, []);

  useEffect(() => {
    if (!picked) setEdit(null);
  }, [picked]);

  const toggle = (hex: string) =>
    setChosen((c) => (c.includes(hex) ? c.filter((x) => x !== hex) : c.length < 6 ? [...c, hex] : c));
  // From the eyedropper: shown in its own row, and chosen straight away.
  const pick = (hex: string) => {
    setPicks((p) => (p.includes(hex) ? p : [...p, hex].slice(-8)));
    setChosen((c) => (c.includes(hex) || c.length >= 6 ? c : [...c, hex]));
  };

  async function apply() {
    setSaving(true);
    setError(null);
    try {
      if (edit) await api.uploadClubLogo(club.id, new File([await renderLogo(edit)], "logo.png", { type: "image/png" }));
      if (chosen.length) await api.updateClub(club.id, { colors: chosen });
      await onSaved();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  // Colours by where they were found; the badge's own last.
  const groups = new Map<string, string[]>();
  for (const c of result?.colors ?? []) groups.set(c.source, [...(groups.get(c.source) ?? []), c.hex]);
  if (edit?.colors.length) groups.set("From the badge", edit.colors);
  if (picks.length) groups.set("Picked from the badge", picks);
  const candidates = [...own, ...(result?.candidates ?? [])].filter((c) => !broken.has(c.id));
  const applyLabel =
    edit && chosen.length
      ? `Use badge and ${chosen.length} colour${chosen.length === 1 ? "" : "s"}`
      : edit
        ? "Use badge"
        : chosen.length
          ? `Use ${chosen.length} colour${chosen.length === 1 ? "" : "s"}`
          : "Use";

  return (
    <div className="club-lookup">
      <div className="toolbar">
        <strong style={{ marginRight: 4 }}>Find {club.name} online</strong>
        <input
          value={hint}
          onChange={(e) => setHint(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void search()}
          placeholder="Narrow it down: city, state or website - e.g. Seattle, Washington State, nwjuniors.org"
          style={{ flex: "1 1 260px" }}
          aria-label="Search hints"
        />
        <button onClick={() => void search()} disabled={searching}>
          {searching ? "Searching…" : "Search"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/webp,image/jpeg,image/gif,image/bmp"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void addImage(f, "Uploaded");
          }}
        />
        <button onClick={() => fileRef.current?.click()} disabled={adding} title="Or paste one: Ctrl+V anywhere here">
          {adding ? "Adding…" : "Upload image"}
        </button>
        <button onClick={onClose}>Close</button>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>
        Have the badge already? Copy it (right-click an image, Copy image) and press Ctrl+V here, or upload it.
      </p>
      {error && <div className="error">{error}</div>}
      {result?.notes.map((n) => (
        <p key={n} className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>{n}</p>
      ))}

      {((result && !searching) || own.length > 0) && (
        <>
          <h3 className="club-lookup-h">Badges found</h3>
          {candidates.length === 0 ? (
            <p className="muted" style={{ fontSize: 13 }}>
              Nothing found. Most clubs aren't on Wikipedia: type the club's website into the box above
              (e.g. nwjuniors.org) and search again - its badge and colours are read from there.
            </p>
          ) : (
            <div className="club-lookup-grid">
              {candidates.map((c) => (
                <div key={c.id} className="club-lookup-item">
                <button
                  className={`club-lookup-cand${picked === c.id ? " picked" : ""}`}
                  onClick={() => setPicked(picked === c.id ? null : c.id)}
                  title={`${c.source}: ${c.title}`}
                >
                  <span className="club-lookup-thumb" style={{ background: CHECKER }}>
                    <img
                      src={api.lookupThumbUrl(c.id)}
                      alt=""
                      loading="lazy"
                      onError={() => setBroken((b) => new Set(b).add(c.id))}
                    />
                  </span>
                  <span className="club-lookup-src">{c.source}</span>
                </button>
                <SourceLinks url={c.url} page={c.page} />
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {picked && <LogoEditor source={picked} onChange={setEdit} onPick={pick} />}

      <h3 className="club-lookup-h">Colours</h3>
      <div className="club-lookup-colors">
        <span className="muted club-lookup-colsrc">Now</span>
        {current}
      </div>
      {groups.size > 0 && (
        <>
          {[...groups].map(([source, hexes]) => (
            <div key={source} className="club-lookup-colors">
              <span className="muted club-lookup-colsrc" title={source}>{source}</span>
              {hexes.map((h) => {
                const n = chosen.indexOf(h);
                return (
                  <button
                    key={h}
                    className={`club-lookup-swatch${n >= 0 ? " picked" : ""}`}
                    style={{ background: h }}
                    title={h}
                    onClick={() => toggle(h)}
                  >
                    {n >= 0 && <span>{n + 1}</span>}
                  </button>
                );
              })}
              <button style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => setChosen(hexes.slice(0, 6))}>
                These
              </button>
            </div>
          ))}
          <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>
            {chosen.length
              ? "Numbered in the order picked; 1 is the main colour. They replace the club's colours."
              : "Pick colours to replace the club's, or leave them as they are."}
          </p>
        </>
      )}

      <div className="toolbar" style={{ marginTop: 10 }}>
        <button className="primary" onClick={() => void apply()} disabled={saving || (!edit && !chosen.length)}>
          {saving ? "Saving…" : applyLabel}
        </button>
      </div>
    </div>
  );
}

const isWeb = (u: string | null): u is string => !!u && /^https?:\/\//i.test(u);

/** "nwjuniors.org/images/logo.png" - the address without its scheme. */
function shortUrl(u: string): string {
  return u.replace(/^https?:\/\/(www\.)?/i, "").replace(/[?#].*$/, "");
}

/** Where a found image is, and the page it was found on, as links. */
function SourceLinks({ url, page }: { url: string | null; page: string | null }) {
  const img = isWeb(url) ? url : null;
  const from = isWeb(page) && page !== img ? page : null;
  if (!img && !from) return null;
  return (
    <span className="club-lookup-links">
      {img && (
        <a href={img} target="_blank" rel="noopener noreferrer" title={img}>
          {shortUrl(img)}
        </a>
      )}
      {from && (
        <a href={from} target="_blank" rel="noopener noreferrer" title={`Found on ${from}`}>
          found on {shortUrl(from).split("/")[0]}
        </a>
      )}
    </span>
  );
}
