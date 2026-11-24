import { useState } from "react";
import type { PlayerInfo } from "../shared/api";
import { formatWhen, post, shrinkPhoto, useApi } from "./api";

/**
 * A parent's (or the admin's) form for their player: the name and photo.
 * Saved changes travel to VME, which applies them to its roster and
 * republishes - so they show here a few minutes later, not at once.
 */
export function PlayerEditor({ team, number }: { team: string; number: number }) {
  const res = useApi<PlayerInfo>(`/api/player/${encodeURIComponent(team)}/${number}`);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [shortName, setShortName] = useState("");
  const [photo, setPhoto] = useState<{ base64: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (res.state !== "ok") return null;
  const info = res.doc;

  function start() {
    setName(info.pending?.name ?? info.name);
    setShortName(info.pending?.short_name ?? info.short_name);
    setPhoto(null);
    setError(null);
    setOpen(true);
  }

  async function pick(file: File | undefined) {
    if (!file) return;
    try {
      const base64 = await shrinkPhoto(file);
      setPhoto({ base64, url: `data:image/jpeg;base64,${base64}` });
    } catch {
      setError("That file couldn't be read as a photo.");
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/player/${encodeURIComponent(team)}/${number}`, {
        name: name.trim() !== info.name ? name : null,
        short_name: shortName.trim() !== info.short_name ? shortName : null,
        photo_base64: photo?.base64 ?? null,
      });
      setOpen(false);
      res.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const changed = name.trim() !== info.name || shortName.trim() !== info.short_name || photo !== null;

  return (
    <section>
      {info.pending && !open && (
        <p className="notice small">
          Your changes from {formatWhen(info.pending.submitted_at)} are on their way to the team's video PC and will show
          here once it picks them up.
        </p>
      )}
      {!open ? (
        <button onClick={start}>✏️ Edit player</button>
      ) : (
        <div className="card form">
          <h2>Edit #{number}</h2>
          <label>
            Full name <span className="muted small">(used in videos; not shown on this site)</span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
          </label>
          <label>
            Name on the site <span className="muted small">(e.g. "Kate G"; empty = first name and initial)</span>
            <input value={shortName} onChange={(e) => setShortName(e.target.value)} maxLength={40} />
          </label>
          <label>
            Photo <span className="muted small">(a clear face shot works best)</span>
            <input type="file" accept="image/*" onChange={(e) => void pick(e.target.files?.[0])} />
          </label>
          {photo && <img className="player-photo preview" src={photo.url} alt="New photo" />}
          <div className="actions">
            <button className="primary" onClick={save} disabled={busy || !changed || !name.trim()}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button onClick={() => setOpen(false)}>Cancel</button>
          </div>
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </section>
  );
}
