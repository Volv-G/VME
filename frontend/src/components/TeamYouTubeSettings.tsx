import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  RosterDto,
  YouTubeConfigDto,
  YouTubeStatusDto,
} from "../types/api";

/**
 * Team-level YouTube upload defaults editor.
 *
 * Reads the current `youtube` block off the team roster, lets the user
 * tweak the four fields, and saves via PUT /roster (the backend
 * round-trips the entire roster - we re-send the existing players /
 * team_name etc. to avoid wiping them).
 *
 * Also shows the backend's YouTube-readiness status so the user knows
 * whether uploads will actually work after saving these defaults.
 */
interface Props {
  team: string;
  roster: RosterDto | null;
  onSaved: (next: RosterDto) => void;
}

// Defaults match `domain/roster.py` so the placeholder shown to the user
// is what they'd get if they leave a template field empty.
const DEFAULT_TITLE = "{date}. {tournament_abbr}. M{match_index}. {opponent}";
const DEFAULT_DESC = "{date}. {tournament_full}. Match {match_index}. {opponent}";
// Player reels get their own pair: one video per player, so the
// match-level title would be identical for all of them.
// Date first, then player: YouTube (and any file listing) sorts
// alphabetically, so leading with the date groups a match's reels
// together and keeps chronological order across matches.
const DEFAULT_REEL_TITLE =
  "{date} - {player} - {tournament_abbr}. M{match_index}. {opponent}";
const DEFAULT_REEL_DESC =
  "{date}. {tournament_full}. Match {match_index}. {opponent}\n\n{chapters}";

export function TeamYouTubeSettings({ team, roster, onSaved }: Props) {
  const [status, setStatus] = useState<YouTubeStatusDto | null>(null);
  const [privacy, setPrivacy] = useState<"private" | "unlisted" | "public">(
    "unlisted"
  );
  const [playlistId, setPlaylistId] = useState<string>("");
  const [titleTpl, setTitleTpl] = useState<string>("");
  const [descTpl, setDescTpl] = useState<string>("");
  const [reelTitleTpl, setReelTitleTpl] = useState<string>("");
  const [reelDescTpl, setReelDescTpl] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    void api.youtubeStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  // Hydrate form whenever the parent fetches a new roster. Template
  // fields are PRE-FILLED with the default when nothing is saved, so
  // the user edits from a working template instead of an empty box.
  useEffect(() => {
    const yt = roster?.youtube ?? null;
    setPrivacy(((yt?.privacy_status as never) ?? "unlisted") || "unlisted");
    setPlaylistId(yt?.playlist_id ?? "");
    setTitleTpl(yt?.title_template || DEFAULT_TITLE);
    setDescTpl(yt?.description_template || DEFAULT_DESC);
    setReelTitleTpl(yt?.reel_title_template || DEFAULT_REEL_TITLE);
    setReelDescTpl(yt?.reel_description_template || DEFAULT_REEL_DESC);
  }, [roster]);

  // A field left at (or emptied back to) the default is persisted as
  // null. That keeps roster.json free of redundant copies AND means a
  // future change to the default propagates to teams that never
  // customized - which pre-filling the box would otherwise prevent.
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
        youtube: {
          privacy_status: privacy,
          // Empty string -> null so the backend can fall back to its
          // default template. Same for playlist.
          playlist_id: playlistId.trim() || null,
          title_template: normalize(titleTpl, DEFAULT_TITLE),
          description_template: normalize(descTpl, DEFAULT_DESC),
          reel_title_template: normalize(reelTitleTpl, DEFAULT_REEL_TITLE),
          reel_description_template: normalize(
            reelDescTpl,
            DEFAULT_REEL_DESC
          ),
        } satisfies YouTubeConfigDto,
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

  // Compare NORMALIZED values so a pre-filled default doesn't read as
  // an unsaved change the moment the card mounts.
  const dirty =
    roster &&
    (privacy !== (roster.youtube?.privacy_status ?? "unlisted") ||
      (playlistId.trim() || null) !== (roster.youtube?.playlist_id ?? null) ||
      normalize(titleTpl, DEFAULT_TITLE) !==
        (roster.youtube?.title_template ?? null) ||
      normalize(descTpl, DEFAULT_DESC) !==
        (roster.youtube?.description_template ?? null) ||
      normalize(reelTitleTpl, DEFAULT_REEL_TITLE) !==
        (roster.youtube?.reel_title_template ?? null) ||
      normalize(reelDescTpl, DEFAULT_REEL_DESC) !==
        (roster.youtube?.reel_description_template ?? null));

  // The status indicator below reports SERVER-SIDE readiness (the
  // google-* libs are installed, an OAuth client_secret file exists,
  // and a refresh token has been minted via `yt_authorize.py`). It is
  // NOT affected by the form fields on this card - saving here only
  // updates the team-level upload DEFAULTS (privacy / playlist /
  // templates). The two are separate concerns: backend setup is a
  // one-time install task, defaults are what get applied to each
  // upload. We make this distinction explicit in the UI to avoid the
  // "I saved and it's still not configured" confusion.
  return (
    <div className="card">
      <div className="card-header">
        <h2>YouTube upload defaults</h2>
      </div>

      {/* --- Backend status block ----------------------------------
          Visually distinct from the form below so it's obvious the
          "not configured" state is about server prerequisites, not
          about the form fields. */}
      <div
        style={{
          marginBottom: 12,
          padding: "8px 10px",
          borderRadius: 6,
          border: "1px solid var(--border)",
          background: "var(--bg-elev-2)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            fontSize: 12,
          }}
        >
          <strong style={{ color: "var(--text)" }}>Backend setup</strong>
          {status && (
            <span
              style={{
                color: status.configured ? "#3aa55d" : "var(--text-dim)",
              }}
            >
              {status.configured
                ? "● ready"
                : "○ not configured (server-side)"}
            </span>
          )}
        </div>
        {status && !status.configured && (
          <p
            className="muted"
            style={{ fontSize: 11, margin: "4px 0 0 0", lineHeight: 1.45 }}
          >
            Saving the form below does <strong>not</strong> fix this.
            Reason: {status.reason}
          </p>
        )}
      </div>

      {/* --- Defaults form ------------------------------------------
          Each field gets its own row so input left edges all line up
          at the same x. Privacy is narrower but starts at the same
          left margin as everything below. */}
      <label>
        Privacy
        <select
          value={privacy}
          onChange={(e) =>
            setPrivacy(e.target.value as "private" | "unlisted" | "public")
          }
          style={{ maxWidth: 200 }}
        >
          <option value="unlisted">unlisted</option>
          <option value="private">private</option>
          <option value="public">public</option>
        </select>
      </label>
      <label style={{ marginTop: 8 }}>
        Playlist ID <span className="muted">(optional)</span>
        <input
          value={playlistId}
          onChange={(e) => setPlaylistId(e.target.value)}
          placeholder="e.g. PLxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        />
      </label>
      <label style={{ marginTop: 8 }}>
        Title template
        <input
          value={titleTpl}
          onChange={(e) => setTitleTpl(e.target.value)}
          placeholder={DEFAULT_TITLE}
        />
      </label>
      <label style={{ marginTop: 8 }}>
        Description template
        <textarea
          value={descTpl}
          onChange={(e) => setDescTpl(e.target.value)}
          rows={3}
          placeholder={DEFAULT_DESC}
        />
      </label>

      <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>
        Variables: <code>{"{date}"}</code> <code>{"{team}"}</code>{" "}
        <code>{"{opponent}"}</code> <code>{"{tournament_abbr}"}</code>{" "}
        <code>{"{tournament_full}"}</code> <code>{"{match_index}"}</code>.
        Leave a template empty to use the default.
      </p>

      {/* Player reels upload one video per player, so they need their
          own title (otherwise all 12 reels from a match share a name)
          and their own description (the place the chapter list goes). */}
      <div
        style={{
          marginTop: 14,
          paddingTop: 10,
          borderTop: "1px solid var(--border)",
        }}
      >
        <strong style={{ fontSize: 12 }}>Player reels</strong>
        <label style={{ marginTop: 8 }}>
          Reel title template
          <input
            value={reelTitleTpl}
            onChange={(e) => setReelTitleTpl(e.target.value)}
            placeholder={DEFAULT_REEL_TITLE}
          />
        </label>
        <label style={{ marginTop: 8 }}>
          Reel description template
          <textarea
            value={reelDescTpl}
            onChange={(e) => setReelDescTpl(e.target.value)}
            rows={4}
            placeholder={DEFAULT_REEL_DESC}
          />
        </label>
        <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>
          Same variables plus <code>{"{player}"}</code> (&quot;#8 Kate
          G&quot;) <code>{"{player_number}"}</code>{" "}
          <code>{"{player_name}"}</code> <code>{"{clip_count}"}</code>{" "}
          <code>{"{chapters}"}</code>. Put <code>{"{chapters}"}</code>{" "}
          where you want the per-play timestamp list; if you omit it, the
          list is appended at the end.
        </p>
      </div>

      {err && <div className="error" style={{ marginTop: 8 }}>{err}</div>}
      {info && <div className="muted" style={{ marginTop: 8 }}>{info}</div>}

      <div className="toolbar" style={{ marginTop: 12, justifyContent: "flex-end" }}>
        <button
          className="primary"
          onClick={save}
          disabled={!dirty || saving}
          title={dirty ? "Save YouTube defaults" : "No changes"}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
