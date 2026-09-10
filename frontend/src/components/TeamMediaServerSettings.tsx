import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MediaServerConfigDto, RosterDto } from "../types/api";

/**
 * Where finished renders get copied for a media server to play.
 *
 * The renders tree is organised for editing
 * (`<tournament>/<date>/<NN_Opponent>/renders/full_<timestamp>.mp4`),
 * which tells Jellyfin nothing. Publishing copies the video out under
 * one flat, self-describing name, together with the sidecar images the
 * render already produced (`-thumb.jpg`, `-poster.jpg`).
 *
 * The path is resolved BY THE SERVER, not the browser: the copy is a
 * server-side file operation, so a local folder or a UNC share the
 * service account can reach both work, and a multi-gigabyte match never
 * travels through the UI.
 */
interface Props {
  team: string;
  roster: RosterDto | null;
  onSaved: (next: RosterDto) => void;
}

// Mirrors DEFAULT_MEDIA_SERVER_TEMPLATE in domain/roster.py.
const DEFAULT_TEMPLATE = "{date}. {tournament_abbr}. M{match_index}. {opponent}";

const VARS = [
  "{date}",
  "{date_iso}",
  "{opponent}",
  "{team}",
  "{tournament_abbr}",
  "{tournament_full}",
  "{match_index}",
];

export function TeamMediaServerSettings({ team, roster, onSaved }: Props) {
  const [path, setPath] = useState("");
  const [template, setTemplate] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    const ms = roster?.media_server ?? null;
    setPath(ms?.path || "");
    setTemplate(ms?.filename_template || DEFAULT_TEMPLATE);
  }, [roster]);

  /** Default (or blank) -> null, so a later change to the default
   *  reaches teams that never customized it. */
  function normalize(value: string, fallback: string): string | null {
    const v = value.trim();
    return !v || v === fallback ? null : v;
  }

  async function save() {
    if (!roster) return;
    setErr(null);
    setInfo(null);
    setSaving(true);
    try {
      const next: RosterDto = {
        ...roster,
        media_server: {
          path: path.trim() || null,
          filename_template: normalize(template, DEFAULT_TEMPLATE),
        } satisfies MediaServerConfigDto,
      };
      const saved = await api.putRoster(team, next);
      onSaved(saved);
      setInfo("Saved.");
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  const dirty =
    roster &&
    ((path.trim() || null) !== (roster.media_server?.path ?? null) ||
      normalize(template, DEFAULT_TEMPLATE) !==
        (roster.media_server?.filename_template ?? null));

  return (
    <div className="card">
      <div className="card-header">
        <h2>Media server</h2>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        A folder <em>on the server</em> that Jellyfin (or Plex, or a TV)
        plays from. Set one and each render gets a 📺 button that copies
        the video plus its <code>-thumb.jpg</code> / <code>-poster.jpg</code>{" "}
        images there, renamed for a library. Leave empty to hide the
        button.
      </p>

      <label style={{ marginTop: 8 }}>
        Destination folder
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="D:\Media\Volleyball  or  \\nas\media\Volleyball"
          spellCheck={false}
          style={{ fontFamily: "ui-monospace, monospace" }}
        />
      </label>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Absolute path. Created if it doesn't exist. A network share must
        be reachable by the account the backend service runs as - a
        mapped drive letter from your own session will not work.
      </p>

      <label style={{ marginTop: 12 }}>
        File name
        <input
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          placeholder={DEFAULT_TEMPLATE}
          spellCheck={false}
          style={{ fontFamily: "ui-monospace, monospace" }}
        />
      </label>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        No extension - it's taken from the render. Player reels get the
        player appended, so a match and its reels sort together.
        Variables:{" "}
        {VARS.map((t, i) => (
          <span key={t}>
            <code>{t}</code>
            {i < VARS.length - 1 ? " " : ""}
          </span>
        ))}
        . An unnumbered match (match&nbsp;0) drops <code>M{"{match_index}"}</code>{" "}
        entirely.
      </p>

      {err && (
        <div className="error" style={{ marginTop: 8 }}>
          {err}
        </div>
      )}
      {info && (
        <div className="info" style={{ marginTop: 8 }}>
          {info}
        </div>
      )}

      <div
        className="toolbar"
        style={{ marginTop: 12, justifyContent: "flex-end" }}
      >
        <button
          className="primary"
          onClick={save}
          disabled={!roster || !dirty || saving}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
