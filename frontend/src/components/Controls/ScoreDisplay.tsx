import type { GameStateDto, RosterDto } from "../../types/api";

interface Props {
  state: GameStateDto;
  homeName: string;
  opponentName: string;
  homeColor?: string | null;
  opponentColor?: string | null;
}

export function ScoreDisplay({ state, homeName, opponentName, homeColor, opponentColor }: Props) {
  const serving = state.serving_team;
  return (
    <div className="score-display">
      <div className="score-side">
        <div className="score-label" style={homeColor ? { color: homeColor } : undefined}>
          {homeName}
          {serving === "home" && <span className="serve-dot" title="Serving">●</span>}
        </div>
        <div className="score-points">{state.home_score}</div>
        {state.home_sets > 0 && <div className="score-sets">sets {state.home_sets}</div>}
      </div>
      <div className="score-sep">-</div>
      <div className="score-side">
        <div className="score-label" style={opponentColor ? { color: opponentColor } : undefined}>
          {opponentName}
          {serving === "away" && <span className="serve-dot" title="Serving">●</span>}
        </div>
        <div className="score-points">{state.away_score}</div>
        {state.away_sets > 0 && <div className="score-sets">sets {state.away_sets}</div>}
      </div>
    </div>
  );
}
