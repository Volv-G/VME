import { useState } from "react";
import type { ClipDto } from "../types/api";
import { resolveClipFromGlobal } from "./clipFrames";

interface Props {
  clips: ClipDto[];
  currentFrame: number;
  homeName: string;
  opponentName: string;
  homeColor?: string | null;
  opponentColor?: string | null;
  onCreate: (body: {
    type: string;
    clip_id: string;
    local_frame: number;
    payload: Record<string, unknown>;
  }) => Promise<void>;
  /** Step to the event before / after the playhead. Disabled when there
   *  is none in that direction. */
  onPrevEvent: () => void;
  onNextEvent: () => void;
  hasPrevEvent: boolean;
  hasNextEvent: boolean;
}

/**
 * The three things you do over and over while watching footage, next to
 * the video instead of in the controls column.
 *
 * Tagging a rally is Ball Served, then a point to one side, and the eye
 * never leaves the picture while doing it - so on a desktop it saves
 * crossing to the other pane, and on a phone the controls pane is not
 * even on screen. The event stepper belongs here for the same reason:
 * moving between tagged moments is how you review, and it was only
 * reachable by finding the row in the list.
 *
 * Deliberately NOT the whole action set. Everything player-specific
 * needs the lineup grid to say who, which is a panel-sized job; these
 * three need nothing but the frame you are looking at.
 */
export function VideoActionBar({
  clips,
  currentFrame,
  homeName,
  opponentName,
  homeColor,
  opponentColor,
  onCreate,
  onPrevEvent,
  onNextEvent,
  hasPrevEvent,
  hasNextEvent,
}: Props) {
  const [busy, setBusy] = useState(false);

  async function commit(type: string, payload: Record<string, unknown> = {}) {
    const r = resolveClipFromGlobal(clips, currentFrame);
    if (!r) return;
    setBusy(true);
    try {
      await onCreate({ type, clip_id: r.clipId, local_frame: r.localFrame, payload });
    } finally {
      setBusy(false);
    }
  }

  const disabled = busy || clips.length === 0;

  return (
    <div className="video-actions">
      {/* Two labels, one shown at a time (see `.va-full` / `.va-short`).
          At phone width the words cost more than they explain: the bar
          is five buttons and the long ones pushed the rest off the
          pane. */}
      <button
        className="va-step"
        onClick={onPrevEvent}
        disabled={!hasPrevEvent}
        title="Jump to the previous event"
        aria-label="Previous event"
      >
        <span className="va-full">◀ Event</span>
        <span className="va-short">◀</span>
      </button>

      <button
        className="va-serve"
        onClick={() => void commit("ball_served")}
        disabled={disabled}
        title="Log a serve at the current frame"
        aria-label="Ball served"
      >
        <span className="va-full">🏐 Ball Served</span>
        <span className="va-short">🏐</span>
      </button>

      <button
        className="va-step va-next"
        onClick={onNextEvent}
        disabled={!hasNextEvent}
        title="Jump to the next event"
        aria-label="Next event"
      >
        <span className="va-full">Event ▶</span>
        <span className="va-short">▶</span>
      </button>

      {/* Team colour on the point buttons, so the two are told apart by
          the same cue the scoreboard and the overlays use rather than by
          reading the name on a moving picture. */}
      <button
        className="va-point"
        style={homeColor ? { borderColor: homeColor, color: homeColor } : undefined}
        onClick={() => void commit("score", { team: "home" })}
        disabled={disabled}
        title={`Point to ${homeName}`}
      >
        +1 {homeName}
      </button>
      <button
        className="va-point"
        style={
          opponentColor
            ? { borderColor: opponentColor, color: opponentColor }
            : undefined
        }
        onClick={() => void commit("score", { team: "away" })}
        disabled={disabled}
        title={`Point to ${opponentName}`}
      >
        +1 {opponentName}
      </button>

    </div>
  );
}
