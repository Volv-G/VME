import { useEffect } from "react";
import type { GameStateDto, RosterDto } from "../../types/api";

interface Props {
  roster: RosterDto;
  /** Current lineup; used to dim already-on-court players. */
  lineup: GameStateDto["home_positions"];
  /** Position number being filled. */
  position: number;
  onPick: (jersey: number) => void;
  onClose: () => void;
}

/** Inline picker that replaces the lineup grid while choosing a player to substitute in. */
export function RosterPicker({ roster, lineup, position, onPick, onClose }: Props) {
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

  const onCourt = new Set(Object.values(lineup).filter((n): n is number => n != null));

  if (roster.players.length === 0) {
    return (
      <div className="roster-picker">
        <div className="roster-picker-header">
          <strong>Position {position}</strong>
          <button onClick={onClose}>Cancel</button>
        </div>
        <p className="muted">Roster is empty. Add players in the team roster page first.</p>
      </div>
    );
  }

  const sorted = [...roster.players].sort((a, b) => a.number - b.number);

  return (
    <div className="roster-picker">
      <div className="roster-picker-header">
        <strong>Position {position} - choose player</strong>
        <button onClick={onClose}>Cancel</button>
      </div>
      <div className="roster-picker-grid">
        {sorted.map((p) => (
          <button
            key={p.number}
            type="button"
            className={`roster-chip${onCourt.has(p.number) ? " on-court" : ""}`}
            onClick={() => onPick(p.number)}
            title={onCourt.has(p.number) ? `${p.name} (currently on court)` : p.name}
          >
            <span className="chip-num">#{p.number}</span>
            <span className="chip-name">{p.short_name || p.name.split(" ")[0]}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
