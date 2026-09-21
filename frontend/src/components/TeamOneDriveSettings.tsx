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

/** Suggested filename templates, offered as input placeholders. Empty is
 *  the real default and means "keep the render's own name". */
const SUGGEST_MATCH_NAME = "{date} {team} vs {opponent}";
const SUGGEST_REEL_NAME = "{player_name} - {date} vs {opponent}";

/** One representative match, for the preview under each field. A
 *  numbered match on purpose: `{match_index}` renders empty for the
 *  only match of a day, and a preview that silently dropped it would
 *  teach the wrong thing about the placeholder. */
const SAMPLE: Record<string, string> = {
  team: "Eastlake",
  opponent: "Woodinville",
  date: "2026.09.16",
  date_iso: "2026-09-16",
  tournament: "KCC",
  tournament_abbr: "KCC",
  tournament_full: "KingCo Conference",
  match_index: "2",
  player: "#2 Sofia S",
  player_number: "2",
  player_name: "Sofia S",
  clip_count: "7",
};

/** Mirror of the server's lenient rendering: known placeholders are
 *  substituted, unknown ones are left visible so a typo shows up here
 *  rather than in the drive. */
function fill(template: string): string {
  return Object.entries(SAMPLE).reduce(
    (out, [k, v]) => out.split(`{${k}}`).join(v),
    template
  );
}

/** The full path a render would land on, for the preview line. */
function previewPath(folder: string, name: string, isReel: boolean): string {
  const dir = fill(folder || "")
    .split("/")
    .map((s) => s.trim())
    .filter(Boolean)
    .join("/");
  const leaf = fill(name || "").trim();
  const file = leaf
    ? `${leaf}.mp4`
    : isReel
      ? "2026.09.16_vs_Woodinville_02_Sofia_S_reel.mp4"
      : "2026.09.16_vs_Woodinville.mp4";
  return dir ? `${dir}/${file}` : file;
}

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

/** The path a render would land on, under the field that decides it.
 *  Cheaper than a round-trip and it updates as you type, which is the
 *  point: the server renders these templates leniently, so a typo is
 *  meant to be caught here rather than in the drive. */
function Preview({ path }: { path: string }) {
  return (
    <p
      className="muted"
      style={{
        fontSize: 12,
        fontFamily: "var(--mono, monospace)",
        wordBreak: "break-all",
        margin: "2px 0 10px",
      }}
    >
      {path}
    </p>
  );
}

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
        onedrive_match_name: "",
        onedrive_reel_name: "",
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
            Relative to the drive root. <code>/</code> makes a folder level.
          </p>
          <Preview path={previewPath(cfg.onedrive_folder ?? "", "", false)} />

          <label>
            Match filename
            <input
              value={cfg.onedrive_match_name ?? ""}
              placeholder={`${SUGGEST_MATCH_NAME}   (blank = as rendered)`}
              onChange={(e) => set("onedrive_match_name", e.target.value)}
            />
          </label>
          <Preview
            path={previewPath(
              cfg.onedrive_folder ?? "",
              cfg.onedrive_match_name ?? "",
              false
            )}
          />

          <label>
            Reel filename
            <input
              value={cfg.onedrive_reel_name ?? ""}
              placeholder={`${SUGGEST_REEL_NAME}   (blank = as rendered)`}
              onChange={(e) => set("onedrive_reel_name", e.target.value)}
            />
          </label>
          <Preview
            path={previewPath(
              cfg.onedrive_folder ?? "",
              cfg.onedrive_reel_name ?? "",
              true
            )}
          />

          <p className="muted" style={{ fontSize: 12 }}>
            Leave a filename blank to keep the name the render already has.
            The extension is always the render's — a <code>.mp4</code> named
            something else stops playing. Give reels a template that includes
            the player, or a match's twelve reels all land on one name and
            overwrite each other.
          </p>
          <p className="muted" style={{ fontSize: 12 }}>
            Placeholders, the same ones the YouTube title templates use:{" "}
            {[
              "team",
              "opponent",
              "date",
              "date_iso",
              "tournament_abbr",
              "tournament_full",
              "match_index",
              "player",
              "player_name",
              "player_number",
              "clip_count",
            ].map((v, i) => (
              <span key={v}>
                {i > 0 && " "}
                <code>{`{${v}}`}</code>
              </span>
            ))}
            . The player ones are blank outside a reel.{" "}
            <code>{"{match_index}"}</code> is blank for the only match of a
            day, and an <code>M</code> or <code>#</code> in front of it is
            dropped with it.
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
