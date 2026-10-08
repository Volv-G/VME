import { useState } from "react";
import type { GameStateDto, RosterDto } from "../../types/api";

interface Props {
  state: GameStateDto;
  homeName: string;
  opponentName: string;
  homeColor?: string | null;
  opponentColor?: string | null;
  /**
   * Jump the playhead to the previous / next point.
   *
   * On the scoreboard because that is what they navigate: the score is
   * the thing that changes at each one, and reviewing a match is mostly
   * hopping point to point. Optional - without them the arrows are not
   * rendered rather than rendered dead.
   */
  onStepPoint?: (dir: 1 | -1) => void;
  /**
   * The match's "sides swapped" setting: the away team on the left, as
   * the rendered scoreboard draws it. The toggle lives here, on the
   * scoreboard, because that is what it changes - and this display
   * flips with it, so it shows the bar the way the video will.
   */
  swapped?: boolean;
  onSwapSides?: () => Promise<void>;
}

export function ScoreDisplay({
  state,
  homeName,
  opponentName,
  homeColor,
  opponentColor,
  onStepPoint,
  swapped = false,
  onSwapSides,
}: Props) {
  const serving = state.serving_team;
  const [swapping, setSwapping] = useState(false);
  const home = (
    <div className="score-side">
      <div className="score-label" style={homeColor ? { color: homeColor } : undefined}>
        {homeName}
        {serving === "home" && <span className="serve-dot" title="Serving">●</span>}
      </div>
      <div className="score-points">{state.home_score}</div>
      {/* Always rendered, zero included. Hiding it on the side with no
          sets yet left the two halves different heights, and the card
          changed shape the moment the first set was won. */}
      <div className="score-sets">sets {state.home_sets}</div>
    </div>
  );
  const away = (
    <div className="score-side">
      <div className="score-label" style={opponentColor ? { color: opponentColor } : undefined}>
        {opponentName}
        {serving === "away" && <span className="serve-dot" title="Serving">●</span>}
      </div>
      <div className="score-points">{state.away_score}</div>
      <div className="score-sets">sets {state.away_sets}</div>
    </div>
  );
  return (
    <div className="score-display">
      {onStepPoint && (
        <button
          type="button"
          className="score-step"
          onClick={() => onStepPoint(-1)}
          title="Previous point (score, kill or ace)"
          aria-label="Previous point"
        >
          ◀
        </button>
      )}
      {swapped ? away : home}
      <div className="score-sep">
        <span>-</span>
        {onSwapSides && (
          <button
            type="button"
            className={`score-swap${swapped ? " on" : ""}`}
            disabled={swapping}
            onClick={() => {
              setSwapping(true);
              void onSwapSides().finally(() => setSwapping(false));
            }}
            title={
              swapped
                ? "Sides swapped: the scoreboard shows the away team on the left. Click to put home back on the left."
                : "Swap sides: show the away team on the left of the scoreboard"
            }
            aria-pressed={swapped}
          >
            ⇄
          </button>
        )}
      </div>
      {swapped ? home : away}
      {onStepPoint && (
        <button
          type="button"
          className="score-step"
          onClick={() => onStepPoint(1)}
          title="Next point (score, kill or ace)"
          aria-label="Next point"
        >
          ▶
        </button>
      )}
    </div>
  );
}
