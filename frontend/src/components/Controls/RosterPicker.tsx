import { useEffect } from "react";
import type { GameStateDto, RosterDto } from "../../types/api";
import { jerseyLabel } from "./state";

interface Props {
  roster: RosterDto;
  /** Current lineup; used to dim already-on-court players. */
  lineup: GameStateDto["home_positions"];
  /** Position number being filled. */
  position: number;
  /** Libero jerseys for this match - for the `(L)` tag and the filter below. */
  liberos: readonly number[];
  /** Hide liberos - for a front-row slot they are not allowed to fill. */
  excludeLiberos?: boolean;
  /** Shown above the grid when the picker was opened by a rule rather
   *  than a click, so the user knows why they are being asked. */
  reason?: string | null;
  onPick: (jersey: number) => void;
  onClose: () => void;
}

/** Inline picker that replaces the lineup grid while choosing a player to substitute in. */
export function RosterPicker({
  roster,
  lineup,
  position,
  liberos,
  excludeLiberos,
  reason,
  onPick,
  onClose,
}: Props) {
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

  const sorted = [...roster.players]
    .filter((p) => !(excludeLiberos && liberos.includes(p.number)))
    .sort((a, b) => a.number - b.number);

  return (
    <div className="roster-picker">
      <div className="roster-picker-header">
        <strong>Position {position} - choose player</strong>
        <button onClick={onClose}>Cancel</button>
      </div>
      {reason && <p className="muted">{reason}</p>}
      <div className="roster-picker-grid">
        {sorted.map((p) => (
          <button
            key={p.number}
            type="button"
            className={`roster-chip${onCourt.has(p.number) ? " on-court" : ""}`}
            onClick={() => onPick(p.number)}
            title={onCourt.has(p.number) ? `${p.name} (currently on court)` : p.name}
          >
            {liberos.includes(p.number) && (
              <span className="libero-badge" title="Libero">L</span>
            )}
            <span className="chip-num">{jerseyLabel(p.number, liberos)}</span>
            <span className="chip-name">{p.short_name || p.name.split(" ")[0]}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
