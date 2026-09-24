import { useEffect, useState } from "react";
import type { RosterDto } from "../../types/api";

interface Props {
  roster: RosterDto;
  /** Jerseys currently designated for this match. */
  value: readonly number[];
  onSave: (liberos: number[]) => Promise<void>;
  onClose: () => void;
}

/**
 * Inline multi-select that replaces the lineup grid while choosing the
 * liberos for this match. Same shape as `RosterPicker` so the card does
 * not jump, but it toggles rather than picks: a match has one or two,
 * set once before first serve and then left alone.
 */
export function LiberoPicker({ roster, value, onSave, onClose }: Props) {
  const [draft, setDraft] = useState<number[]>([...value]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function toggle(jersey: number) {
    setDraft((d) =>
      d.includes(jersey) ? d.filter((n) => n !== jersey) : [...d, jersey]
    );
  }

  async function save() {
    setSaving(true);
    try {
      await onSave([...draft].sort((a, b) => a - b));
    } finally {
      setSaving(false);
    }
  }

  const sorted = [...roster.players].sort((a, b) => a.number - b.number);

  return (
    <div className="roster-picker">
      <div className="roster-picker-header">
        <strong>Liberos for this match</strong>
        <span>
          <button onClick={onClose} disabled={saving}>
            Cancel
          </button>{" "}
          <button className="primary" onClick={save} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </button>
        </span>
      </div>
      {sorted.length === 0 ? (
        <p className="muted">
          Roster is empty. Add players in the team roster page first.
        </p>
      ) : (
        <div className="roster-picker-grid">
          {sorted.map((p) => {
            const on = draft.includes(p.number);
            return (
              <button
                key={p.number}
                type="button"
                className={`roster-chip${on ? " selected" : ""}`}
                onClick={() => toggle(p.number)}
                aria-pressed={on}
                title={p.name}
              >
                {on && (
                  <span className="libero-badge" title="Libero">L</span>
                )}
                <span className="chip-num">#{p.number}</span>
                <span className="chip-name">
                  {p.short_name || p.name.split(" ")[0]}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
