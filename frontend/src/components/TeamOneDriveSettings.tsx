import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { RosterDto, UploadConfigDto } from "../types/api";

/**
 * Where this team's videos go, and the OneDrive specifics when that is
 * the answer.
 *
 * Two views out of one component because they edit the same `upload`
 * block: `destinations` is the engine choice, `onedrive` is the
 * folder and sharing. Splitting them into two components would mean
 * two copies of the save path writing the same object, which is how
 * one of them quietly starts clobbering the other's fields.
 */
interface Props {
  team: string;
  roster: RosterDto | null;
  onSaved: (next: RosterDto) => void;
  show: "destinations" | "onedrive";
}

const DEFAULT_FOLDER = "VME/{team}/{date} {opponent}";

const ENGINES = [
  {
    id: "youtube",
    label: "YouTube",
    note: "Free bandwidth, discovery and notifications. 6 uploads/day.",
  },
  {
    id: "onedrive",
    label: "OneDrive",
    note: "No daily cap. Share links instead of a channel.",
  },
] as const;

export function TeamOneDriveSettings({ team, roster, onSaved, show }: Props) {
  const [cfg, setCfg] = useState<UploadConfigDto | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    setCfg(
      roster?.upload ?? {
        match_destination: "youtube",
        reel_destination: "youtube",
        onedrive_folder: DEFAULT_FOLDER,
        onedrive_share_links: true,
      }
    );
  }, [roster]);

  async function save() {
    if (!roster || !cfg) return;
    setSaving(true);
    setErr(null);
    try {
      onSaved(await api.putRoster(team, { ...roster, upload: cfg }));
      setInfo("Saved.");
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  function set<K extends keyof UploadConfigDto>(key: K, value: UploadConfigDto[K]) {
    setCfg((c) => (c ? { ...c, [key]: value } : c));
  }

  if (!cfg) return <div className="card muted">Loading…</div>;

  return (
    <div className="card">
      <div className="card-header">
        <h2>{show === "destinations" ? "Upload destinations" : "OneDrive"}</h2>
        <button className="primary" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      {err && <div className="error">{err}</div>}
      {info && <div className="info">{info}</div>}

      {show === "destinations" ? (
        <>
          <p className="muted">
            Matches and reels can go to different places, and usually should.
            A match is large and watched by everyone, which is what YouTube
            serves free. A dozen reels are small, watched by one family each,
            and are the entire reason the daily quota runs out.
          </p>

          {(
            [
              ["match_destination", "Full matches and condensed renders"],
              ["reel_destination", "Player highlight reels"],
            ] as const
          ).map(([key, label]) => (
            <div key={key} className="field-block">
              <label>{label}</label>
              <div className="engine-choice">
                {ENGINES.map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    className={`engine-btn${cfg[key] === e.id ? " active" : ""}`}
                    onClick={() => set(key, e.id)}
                  >
                    <strong>{e.label}</strong>
                    <span className="muted">{e.note}</span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </>
      ) : (
        <>
          <p className="muted">
            Only used for whatever is set to OneDrive under{" "}
            <strong>Uploads</strong>. The account itself is connected once
            for the whole server, under{" "}
            <Link to="/settings">Server settings → Connections</Link>.
          </p>

          <label>
            Folder
            <input
              value={cfg.onedrive_folder ?? ""}
              placeholder={DEFAULT_FOLDER}
              onChange={(e) => set("onedrive_folder", e.target.value)}
            />
          </label>
          <p className="muted" style={{ fontSize: 12 }}>
            Relative to the drive root. Takes <code>{"{team}"}</code>,{" "}
            <code>{"{tournament}"}</code>, <code>{"{date}"}</code> and{" "}
            <code>{"{opponent}"}</code>. The filename comes from the render,
            so reels keep the per-player names they already have.
          </p>

          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={cfg.onedrive_share_links}
              onChange={(e) => set("onedrive_share_links", e.target.checked)}
            />
            Create a shareable link for each upload
          </label>
          <p className="muted" style={{ fontSize: 12 }}>
            Anonymous view links, so a parent needs no Microsoft account.
            Business and SharePoint drives often forbid these by policy — the
            upload still succeeds, it just has no public URL.
          </p>
        </>
      )}
    </div>
  );
}
