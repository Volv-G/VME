import type { EventDto, RosterDto } from "../types/api";
import { EMPTY_STATE } from "./Controls/state";
import { analyzeCuts } from "./cutAnalysis";
import { summarizeEvent } from "./eventSummary";

interface Props {
  events: EventDto[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
  onSeek: (globalFrame: number) => void;
  fps: number;
  homeRoster: RosterDto;
  opponentRoster: RosterDto;
  homeName: string;
  opponentName: string;
}

export function EventList({
  events,
  selectedId,
  onSelect,
  onDelete,
  onSeek,
  fps,
  homeRoster,
  opponentRoster,
  homeName,
  opponentName,
}: Props) {
  if (events.length === 0) {
    return <div className="muted" style={{ padding: 12 }}>No events yet.</div>;
  }

  const ctx = { homeRoster, opponentRoster, homeName, opponentName };
  const { orphanIds } = analyzeCuts(events);

  return (
    <div>
      {events.map((ev, i) => {
        // State BEFORE this event = state of the previous one (or EMPTY for the first).
        const prev = i === 0 ? EMPTY_STATE : (events[i - 1].state ?? EMPTY_STATE);
        const summary = summarizeEvent(ev, prev, ctx);
        const isOrphan = orphanIds.has(ev.id);
        const orphanReason =
          ev.type === "cut_start"
            ? "Cut Start without a matching Cut End"
            : "Cut End without a preceding Cut Start";
        const title = isOrphan ? `${summary} - ${orphanReason}` : summary;
        return (
          <div
            key={ev.id}
            className={`event-row${ev.id === selectedId ? " selected" : ""}${isOrphan ? " orphan" : ""}`}
            onClick={() => {
              onSelect(ev.id);
              if (ev.global_frame !== null) onSeek(ev.global_frame);
            }}
          >
            <div className="event-row-text" title={title}>
              {isOrphan && <span className="orphan-badge" title={orphanReason}>!</span>}
              {summary}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className="ev-frame">{frameLabel(ev.global_frame, fps)}</span>
              <button
                className="danger"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(ev.id);
                }}
                title="Delete event"
              >
                ×
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function frameLabel(globalFrame: number | null, fps: number): string {
  if (globalFrame === null) return "—";
  const seconds = globalFrame / fps;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")} (#${globalFrame})`;
}
