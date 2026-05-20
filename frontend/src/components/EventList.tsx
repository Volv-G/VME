import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { EventDto, RosterDto } from "../types/api";
import { EMPTY_STATE } from "./Controls/state";
import { analyzeCuts } from "./cutAnalysis";
import { analyzeFocus } from "./focusAnalysis";
import { summarizeEvent } from "./eventSummary";
import { eventIcon } from "./eventStyle";

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
  // Local-only search state - fresh per match-editor mount, not persisted.
  // Filters nothing; it only highlights so the user keeps scroll context.
  const [query, setQuery] = useState("");
  const trimmed = query.trim();
  const lowerQuery = trimmed.toLowerCase();

  const ctx = { homeRoster, opponentRoster, homeName, opponentName, fps };
  // Two independent orphan-detection passes - merged here so the same
  // red badge / `.orphan` class flags both unpaired cuts AND unannotated
  // focus spans. Membership in either set is enough to highlight.
  const { orphanIds: cutOrphans } = analyzeCuts(events);
  const { orphanIds: focusOrphans } = analyzeFocus(events);
  const orphanIds = useMemo(
    () => new Set([...cutOrphans, ...focusOrphans]),
    [cutOrphans, focusOrphans]
  );

  // Pre-compute summary strings so the empty-list check below still has a
  // search bar and the match count is consistent with what's rendered.
  const summaries = useMemo(
    () =>
      events.map((ev, i) => {
        const prev = i === 0 ? EMPTY_STATE : (events[i - 1].state ?? EMPTY_STATE);
        return summarizeEvent(ev, prev, ctx);
      }),
    // ctx is a fresh object each render; depend on its primitive parts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [events, homeRoster, opponentRoster, homeName, opponentName, fps]
  );

  const matchCount = lowerQuery
    ? summaries.filter((s) => s.toLowerCase().includes(lowerQuery)).length
    : 0;

  // Auto-scroll the selected row into view whenever the selection
  // changes. Used when an event is added or clicked elsewhere (e.g. the
  // timeline) - the user shouldn't have to hunt for it in a long list.
  // `block: 'nearest'` avoids gratuitous scrolling when the row is
  // already visible, and uses smooth scrolling so the motion is
  // legible rather than a jarring jump.
  const selectedRowRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (selectedId == null) return;
    const el = selectedRowRef.current;
    if (el)
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId]);

  return (
    <div>
      <div className="event-search">
        <input
          type="search"
          placeholder="Search events…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {trimmed && (
          <span className="event-search-count">
            {matchCount} match{matchCount === 1 ? "" : "es"}
          </span>
        )}
      </div>

      {events.length === 0 && (
        <div className="muted" style={{ padding: 12 }}>No events yet.</div>
      )}

      {events.map((ev, i) => {
        const summary = summaries[i];
        const isOrphan = orphanIds.has(ev.id);
        const isMatch =
          lowerQuery !== "" && summary.toLowerCase().includes(lowerQuery);
        const orphanReason =
          ev.type === "cut_start"
            ? "Cut Start without a matching Cut End"
            : ev.type === "cut_end"
            ? "Cut End without a preceding Cut Start"
            : ev.type === "focus_in"
            ? "FocusIn with no matching player event in the rally - this focus will be skipped at render time"
            : "Orphan event";
        const title = isOrphan ? `${summary} - ${orphanReason}` : summary;
        return (
          <div
            key={ev.id}
            ref={ev.id === selectedId ? selectedRowRef : undefined}
            className={
              "event-row" +
              (ev.id === selectedId ? " selected" : "") +
              (isOrphan ? " orphan" : "") +
              (isMatch ? " match" : "")
            }
            onClick={() => {
              onSelect(ev.id);
              if (ev.global_frame !== null) onSeek(ev.global_frame);
            }}
          >
            {/* Two-line content column: summary on top, frame/time below.
                Giving the summary the full row width avoids the previous
                mid-name truncation when the timestamp shared the line. */}
            <div className="event-row-body">
              <div className="event-row-text" title={title}>
                {isOrphan && <span className="orphan-badge" title={orphanReason}>!</span>}
                <span className="event-row-icon" aria-hidden="true">
                  {eventIcon(ev.type)}
                </span>
                {renderWithHighlight(summary, lowerQuery)}
              </div>
              <div className="ev-frame">{frameLabel(ev.global_frame, fps)}</div>
            </div>
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
        );
      })}
    </div>
  );
}

/**
 * Split `text` on every case-insensitive occurrence of `lowerNeedle` and wrap
 * each match in a `<mark>` element. Returns a single string when `lowerNeedle`
 * is empty so non-search renders pay no array-construction cost.
 */
function renderWithHighlight(text: string, lowerNeedle: string): ReactNode {
  if (!lowerNeedle) return text;
  const lower = text.toLowerCase();
  const out: ReactNode[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    const idx = lower.indexOf(lowerNeedle, cursor);
    if (idx === -1) {
      out.push(text.slice(cursor));
      break;
    }
    if (idx > cursor) out.push(text.slice(cursor, idx));
    const end = idx + lowerNeedle.length;
    out.push(
      <mark key={idx} className="event-row-hl">
        {text.slice(idx, end)}
      </mark>
    );
    cursor = end;
  }
  return out;
}

function frameLabel(globalFrame: number | null, fps: number): string {
  if (globalFrame === null) return "—";
  const seconds = globalFrame / fps;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")} (#${globalFrame})`;
}
