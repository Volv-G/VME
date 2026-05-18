import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { NamingConfigDto, RosterDto } from "../types/api";

/**
 * Team-level render-output naming templates editor.
 *
 * Three templates, each producing a path relative to the match's
 * `renders/` directory. Forward slashes create subfolders. Variable
 * values are filesystem-sanitized server-side, so the user can put
 * unsafe characters in opponent / team names without breaking
 * anything.
 *
 * Backend defaults match the legacy hard-coded paths exactly - an
 * empty field falls back to the default at upload time. We show the
 * defaults as placeholders so the user knows what they'd get without
 * customizing.
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
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // Hydrate whenever the parent fetches a new roster. We treat an
  // empty string as "use default" in both directions: blank field on
  // load if the saved value equals the default, and serialize as null
  // on save when blank (server then re-applies default).
  useEffect(() => {
    const n = roster?.naming ?? null;
    setFullTpl(n?.full_render_template ?? "");
    setHighlightTpl(n?.highlight_template ?? "");
    setFocusedTpl(n?.focused_template ?? "");
  }, [roster]);

  async function save() {
    if (!roster) return;
    setErr(null);
    setInfo(null);
    setSaving(true);
    try {
      const next: RosterDto = {
        ...roster,
        naming: {
          full_render_template: fullTpl.trim() || null,
          highlight_template: highlightTpl.trim() || null,
          focused_template: focusedTpl.trim() || null,
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

  const dirty =
    roster &&
    ((fullTpl.trim() || null) !==
      (roster.naming?.full_render_template ?? null) ||
      (highlightTpl.trim() || null) !==
        (roster.naming?.highlight_template ?? null) ||
      (focusedTpl.trim() || null) !==
        (roster.naming?.focused_template ?? null));

  return (
    <div className="card">
      <div className="card-header">
        <h2>Render output naming</h2>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        Paths are relative to each match's <code>renders/</code> folder.
        Slashes create subfolders. Leave a field empty to use the default
        shown as placeholder.
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
