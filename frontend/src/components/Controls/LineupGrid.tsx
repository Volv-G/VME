import type { GameStateDto, RosterDto } from "../../types/api";
import { positionLabel } from "./state";

interface Props {
  positions: GameStateDto["home_positions"];
  roster: RosterDto;
  /** When set, the grid is in "pick a position" mode for an armed action. */
  armedActionLabel: string | null;
  /** Click handler. In normal mode receives any position; in armed mode only fires for filled positions. */
  onPositionClick: (position: number) => void;
}

/**
 * Volleyball court layout (from the home team's perspective):
 *
 *   Front: 4 - 3 - 2
 *   Back:  5 - 6 - 1   <- server is at 1
 *
 * Both rows render left-to-right.
 */
const LAYOUT: number[][] = [
  [4, 3, 2],
  [5, 6, 1],
];

export function LineupGrid({ positions, roster, armedActionLabel, onPositionClick }: Props) {
  return (
    <div className={`lineup-grid${armedActionLabel ? " armed" : ""}`}>
      {LAYOUT.flat().map((pos) => {
        const jersey = positions[pos] ?? null;
        const empty = jersey == null;
        const disabled = armedActionLabel !== null && empty;
        return (
          <button
            key={pos}
            type="button"
            className={`lineup-cell${empty ? " empty" : ""}`}
            onClick={() => onPositionClick(pos)}
            disabled={disabled}
            title={
              armedActionLabel
                ? empty
                  ? "Empty position"
                  : `${armedActionLabel} - position ${pos}`
                : `Position ${pos} - click to substitute`
            }
          >
            <span className="lineup-pos">P{pos}</span>
            <span className="lineup-name">{positionLabel(roster, jersey)}</span>
          </button>
        );
      })}
    </div>
  );
}
