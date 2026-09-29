import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { RosterDto } from "../types/api";
import { GAP_CUT_PAD_SECONDS, SERVE_GAP_WARN_SECONDS } from "./serveGaps";

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
  const [pad, setPad] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const saved = {
    threshold: roster?.editing?.dead_space_seconds ?? SERVE_GAP_WARN_SECONDS,
    pad: roster?.editing?.dead_space_pad_seconds ?? GAP_CUT_PAD_SECONDS,
  };

  useEffect(() => {
    setThreshold(String(saved.threshold));
    setPad(String(saved.pad));
    // Re-hydrate only when a new roster arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roster]);

  const t = Number(threshold);
  const p = Number(pad);
  // Two pads have to leave something to cut, or every gap is kept whole
  // and the button quietly does nothing.
  const problem =
    !Number.isFinite(t) || t < 2
      ? "Dead space must be at least 2 seconds."
      : !Number.isFinite(p) || p < 0
        ? "Padding can't be negative."
        : 2 * p >= t
          ? `Two ${p}s pads leave nothing of a ${t}s gap to cut.`
          : null;
  const dirty = t !== saved.threshold || p !== saved.pad;

  async function save() {
    if (!roster || problem) return;
    setErr(null);
    setInfo(null);
    setSaving(true);
    try {
      const next: RosterDto = {
        ...roster,
        editing: { dead_space_seconds: t, dead_space_pad_seconds: p },
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
        Footage kept either side of a cut (seconds)
        <input
          type="number"
          min={0}
          step={0.5}
          value={pad}
          onChange={(e) => setPad(e.target.value)}
        />
      </label>
      <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Keeps the end of the celebration and the run-up to the serve.
        Default {GAP_CUT_PAD_SECONDS}s.
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
