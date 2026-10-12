import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { RosterDto } from "../types/api";
import {
  GAP_CUT_KEEP_AFTER_SECONDS,
  GAP_CUT_KEEP_BEFORE_SECONDS,
  SERVE_GAP_WARN_SECONDS,
} from "./serveGaps";

/**
 * Team-level editing defaults: what counts as dead space, and how much
 * footage a dead-space cut keeps.
 *
 * Per team rather than per match because the gap it measures is mostly
 * tagging latency - how long after the rally the score gets logged -
 * which is a habit of whoever tags this team's matches, not a property
 * of any one match.
 */
interface Props {
  team: string;
  roster: RosterDto | null;
  onSaved: (next: RosterDto) => void;
}

export function TeamEditingSettings({ team, roster, onSaved }: Props) {
  const [threshold, setThreshold] = useState("");
  const [keepAfter, setKeepAfter] = useState("");
  const [keepBefore, setKeepBefore] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const saved = {
    threshold: roster?.editing?.dead_space_seconds ?? SERVE_GAP_WARN_SECONDS,
    keepAfter:
      roster?.editing?.dead_space_keep_after_seconds ?? GAP_CUT_KEEP_AFTER_SECONDS,
    keepBefore:
      roster?.editing?.dead_space_keep_before_seconds ?? GAP_CUT_KEEP_BEFORE_SECONDS,
  };

  useEffect(() => {
    setThreshold(String(saved.threshold));
    setKeepAfter(String(saved.keepAfter));
    setKeepBefore(String(saved.keepBefore));
    // Re-hydrate only when a new roster arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roster]);

  const t = Number(threshold);
  const a = Number(keepAfter);
  const b = Number(keepBefore);
  // What is kept has to leave something to cut, or every gap is kept
  // whole and the button quietly does nothing.
  const problem =
    !Number.isFinite(t) || t < 2
      ? "Dead space must be at least 2 seconds."
      : !Number.isFinite(a) || a < 0 || !Number.isFinite(b) || b < 0
        ? "Footage kept can't be negative."
        : a + b >= t
          ? `Keeping ${a}s + ${b}s leaves nothing of a ${t}s gap to cut.`
          : null;
  const dirty =
    t !== saved.threshold || a !== saved.keepAfter || b !== saved.keepBefore;

  async function save() {
    if (!roster || problem) return;
    setErr(null);
    setInfo(null);
    setSaving(true);
    try {
      const next: RosterDto = {
        ...roster,
        editing: {
          dead_space_seconds: t,
          dead_space_keep_after_seconds: a,
          dead_space_keep_before_seconds: b,
        },
      };
      onSaved(await api.putRoster(team, next));
      setInfo("Saved.");
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2>Dead space</h2>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        The stretch between a rally ending and the next serve. The editor
        flags it on the event list, shades it on the timeline, and the ✂
        button above the event list cuts all of it at once.
      </p>

      <label style={{ marginTop: 8 }}>
        A serve counts as late after (seconds)
        <input
          type="number"
          min={2}
          step={0.5}
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
        />
      </label>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Measured from the event before the serve. Default{" "}
        {SERVE_GAP_WARN_SECONDS}s.
      </p>

      <label style={{ marginTop: 12 }}>
        Footage kept after the rally (seconds)
        <input
          type="number"
          min={0}
          step={0.5}
          value={keepAfter}
          onChange={(e) => setKeepAfter(e.target.value)}
        />
      </label>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        After the event before the gap - usually the score - so the
        celebration survives. Default {GAP_CUT_KEEP_AFTER_SECONDS}s.
      </p>

      <label style={{ marginTop: 12 }}>
        Footage kept before the serve (seconds)
        <input
          type="number"
          min={0}
          step={0.5}
          value={keepBefore}
          onChange={(e) => setKeepBefore(e.target.value)}
        />
      </label>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        The server's run-up. Default {GAP_CUT_KEEP_BEFORE_SECONDS}s.
      </p>

      {problem && <div className="error" style={{ marginTop: 8 }}>{problem}</div>}
      {err && <div className="error" style={{ marginTop: 8 }}>{err}</div>}
      {info && <div className="muted" style={{ marginTop: 8 }}>{info}</div>}

      <div className="toolbar" style={{ marginTop: 12, justifyContent: "flex-end" }}>
        <button
          className="primary"
          onClick={() => void save()}
          disabled={!dirty || saving || !!problem}
          title={dirty ? "Save editing defaults" : "No changes"}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
