import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { NamingConfigDto, RosterDto } from "../types/api";

/**
 * Team-level render-output naming templates editor.
 *
 * Four templates, each producing a path relative to the match's
 * `renders/` directory. Forward slashes create subfolders. Variable
 * values are filesystem-sanitized server-side, so the user can put
 * unsafe characters in opponent / team names without breaking
 * anything.
 *
 * Fields are PRE-FILLED with the backend default so the user edits a
 * working template instead of starting from an empty box. A value left
 * at (or emptied back to) the default is persisted as null, which keeps
 * roster.json free of redundant copies and lets a future change to the
 * default reach teams that never customized.
 */
interface Props {
  team: string;
  roster: RosterDto | null;
  onSaved: (next: RosterDto) => void;
}

// Mirror `domain/roster.py` defaults so the placeholder hints accurately
// describe the fallback. Keep the constants here narrow and labeled -
// they're documentation as much as configuration.
const DEFAULT_FULL = "{label}_{timestamp}.mp4";
const DEFAULT_HIGHLIGHT =
  "highlights/{team}/{player}/{action}/{date}_vs_{opponent}_{match_timestamp}_{action}.mp4";
const DEFAULT_FOCUSED =
  "focused/{team}/{player}/{action}/{date}_vs_{opponent}_{start_timestamp}-{end_timestamp}.mp4";
const DEFAULT_REEL =
  "reels/{team}/{player}/{date}_vs_{opponent}_{player}_reel.mp4";

// Variable-help blocks shown under each field. Grouped by which fields
// each variable applies to so the user doesn't have to guess.
const COMMON_VARS = [
  "{date}",
  "{opponent}",
  "{team}",
  "{tournament}",
  "{tournament_abbr}",
  "{tournament_full}",
  "{match_index}",
];
const FULL_ONLY_VARS = ["{label}", "{timestamp}"];
const PLAYER_VARS = ["{player}", "{player_number}", "{player_name}"];
const HIGHLIGHT_ONLY_VARS = ["{action}", "{match_timestamp}"];
// `{action}` is available to focused templates too: it's the first
// player-event action in the same rally (falls back to "focus" when
// the rally has no player-tagged action). See backend
// app/render/batch.py::_first_action_for_player_in_rally.
const FOCUSED_ONLY_VARS = ["{action}", "{start_timestamp}", "{end_timestamp}"];
// A reel spans many actions and many timestamps, so neither `{action}`
// nor `{match_timestamp}` identifies the file; `{clip_count}` (number of
// plays in the reel) is offered instead.
const REEL_ONLY_VARS = ["{clip_count}"];

function VarHints({ tokens }: { tokens: string[] }) {
  // Render as inline-wrappable code chips so a long list flows nicely
  // on narrow cards. Tiny font matches the rest of the muted hint copy.
  return (
    <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
      Variables:{" "}
      {tokens.map((t, i) => (
        <span key={t}>
          <code>{t}</code>
          {i < tokens.length - 1 ? " " : ""}
        </span>
      ))}
    </p>
  );
}

export function TeamNamingSettings({ team, roster, onSaved }: Props) {
  const [fullTpl, setFullTpl] = useState("");
  const [highlightTpl, setHighlightTpl] = useState("");
  const [focusedTpl, setFocusedTpl] = useState("");
  const [reelTpl, setReelTpl] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // Hydrate whenever the parent fetches a new roster, falling back to
  // the default template text so every box starts editable-but-valid.
  useEffect(() => {
    const n = roster?.naming ?? null;
    setFullTpl(n?.full_render_template || DEFAULT_FULL);
    setHighlightTpl(n?.highlight_template || DEFAULT_HIGHLIGHT);
    setFocusedTpl(n?.focused_template || DEFAULT_FOCUSED);
    setReelTpl(n?.reel_template || DEFAULT_REEL);
  }, [roster]);

  /** Default (or blank) -> null, so the server keeps applying its own
   *  default rather than a frozen copy of today's value. */
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
        naming: {
          full_render_template: normalize(fullTpl, DEFAULT_FULL),
          highlight_template: normalize(highlightTpl, DEFAULT_HIGHLIGHT),
          focused_template: normalize(focusedTpl, DEFAULT_FOCUSED),
          reel_template: normalize(reelTpl, DEFAULT_REEL),
        } satisfies NamingConfigDto,
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

  // Normalized comparison: a pre-filled default is not a change.
  const dirty =
    roster &&
    (normalize(fullTpl, DEFAULT_FULL) !==
      (roster.naming?.full_render_template ?? null) ||
      normalize(highlightTpl, DEFAULT_HIGHLIGHT) !==
        (roster.naming?.highlight_template ?? null) ||
      normalize(focusedTpl, DEFAULT_FOCUSED) !==
        (roster.naming?.focused_template ?? null) ||
      normalize(reelTpl, DEFAULT_REEL) !==
        (roster.naming?.reel_template ?? null));

  return (
    <div className="card">
      <div className="card-header">
        <h2>Render output naming</h2>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        Paths are relative to each match's <code>renders/</code> folder.
        Slashes create subfolders. Fields start at the default - edit
        them, or clear one to fall back to the default.
      </p>

      <label style={{ marginTop: 8 }}>
        Full render
        <input
          value={fullTpl}
          onChange={(e) => setFullTpl(e.target.value)}
          placeholder={DEFAULT_FULL}
          spellCheck={false}
          style={{ fontFamily: "ui-monospace, monospace" }}
        />
      </label>
      <VarHints tokens={[...COMMON_VARS, ...FULL_ONLY_VARS]} />

      <label style={{ marginTop: 12 }}>
        Highlight clip
        <input
          value={highlightTpl}
          onChange={(e) => setHighlightTpl(e.target.value)}
          placeholder={DEFAULT_HIGHLIGHT}
          spellCheck={false}
          style={{ fontFamily: "ui-monospace, monospace" }}
        />
      </label>
      <VarHints
        tokens={[...COMMON_VARS, ...PLAYER_VARS, ...HIGHLIGHT_ONLY_VARS]}
      />

      <label style={{ marginTop: 12 }}>
        Focused clip
        <input
          value={focusedTpl}
          onChange={(e) => setFocusedTpl(e.target.value)}
          placeholder={DEFAULT_FOCUSED}
          spellCheck={false}
          style={{ fontFamily: "ui-monospace, monospace" }}
        />
      </label>
      <VarHints
        tokens={[...COMMON_VARS, ...PLAYER_VARS, ...FOCUSED_ONLY_VARS]}
      />

      <label style={{ marginTop: 12 }}>
        Player reel
        <input
          value={reelTpl}
          onChange={(e) => setReelTpl(e.target.value)}
          placeholder={DEFAULT_REEL}
          spellCheck={false}
          style={{ fontFamily: "ui-monospace, monospace" }}
        />
      </label>
      <VarHints
        tokens={[...COMMON_VARS, ...PLAYER_VARS, ...REEL_ONLY_VARS]}
      />

      {err && <div className="error" style={{ marginTop: 8 }}>{err}</div>}
      {info && <div className="muted" style={{ marginTop: 8 }}>{info}</div>}

      <div className="toolbar" style={{ marginTop: 12, justifyContent: "flex-end" }}>
        <button
          className="primary"
          onClick={save}
          disabled={!dirty || saving}
          title={dirty ? "Save naming templates" : "No changes"}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
