import { useMemo } from "react";
import type { EventDto, RosterDto } from "../types/api";
import { EMPTY_STATE } from "./Controls/state";
import { eventIcon } from "./eventStyle";
import { summarizeEvent } from "./eventSummary";

interface Props {
  events: EventDto[];
  currentFrame: number;
  fps: number;
  homeRoster: RosterDto;
  opponentRoster: RosterDto;
  homeName: string;
  opponentName: string;
}

/**
 * What the playhead is sitting on, over the top-left of the picture.
 *
 * The event list answers this already, but not when you are looking at
 * the video: on a phone the list is a pane away, and on a desktop it is
 * a column away with the eye on the footage. Scrubbing to "the serve
 * before that kill" meant watching the list instead of the match.
 *
 * The most recent event at or before the playhead, matching how the
 * list decides which row is current - and the last of several at the
 * same frame, which is the one that caused the others (a Kill logged
 * with the Score it produced).
 */
export function CurrentEventBadge({
  events,
  currentFrame,
  fps,
  homeRoster,
  opponentRoster,
  homeName,
  opponentName,
}: Props) {
  const current = useMemo(() => {
    let best: { ev: EventDto; prev: EventDto | null } | null = null;
    let prev: EventDto | null = null;
    for (const ev of events) {
      if (ev.global_frame == null) continue;
      if (ev.global_frame <= currentFrame) {
        best = { ev, prev };
        prev = ev;
      } else break;
    }
    return best;
  }, [events, currentFrame]);

  if (!current) return null;

  const summary = summarizeEvent(
    current.ev,
    current.prev?.state ?? EMPTY_STATE,
    { homeRoster, opponentRoster, homeName, opponentName, fps }
  );

  return (
    <div className="current-event" title={summary}>
      <span aria-hidden="true">{eventIcon(current.ev.type)}</span>
      <span className="current-event-text">{summary}</span>
    </div>
  );
}
