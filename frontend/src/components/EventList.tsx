import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { EventDto, RosterDto } from "../types/api";
import { EMPTY_STATE } from "./Controls/state";
import { analyzeCuts } from "./cutAnalysis";
import { analyzeFocus } from "./focusAnalysis";
import { analyzeServeGaps } from "./serveGaps";
import { summarizeEvent } from "./eventSummary";
import { eventIcon } from "./eventStyle";

interface Props {
  events: EventDto[];
  selectedId: number | null;
  /** Playhead position, so the list can follow playback. */
  currentFrame: number;
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
  currentFrame,
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
  // red badge / `.orphan` class flags unpaired cuts AND unpaired focus
  // spans (both are dropped at render time). Membership in either set is
  // enough to highlight.
  const { orphanIds: cutOrphans } = analyzeCuts(events);
  const { orphanIds: focusOrphans } = analyzeFocus(events);
  const orphanIds = useMemo(
    () => new Set([...cutOrphans, ...focusOrphans]),
    [cutOrphans, focusOrphans]
  );
  // Serves that arrive suspiciously long after the previous event -
  // usually something wasn't logged. Warning, not error: real breaks
  // exist, so we show the gap and let the user judge.
  const serveGaps = useMemo(() => analyzeServeGaps(events, fps), [events, fps]);

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

  // Indices (into `events`) of every row matching the query, in list
  // order. Drives both the counter and the prev/next navigation.
  const matchIndices = useMemo(() => {
    if (!lowerQuery) return [];
    const out: number[] = [];
    summaries.forEach((s, i) => {
      if (s.toLowerCase().includes(lowerQuery)) out.push(i);
    });
    return out;
  }, [summaries, lowerQuery]);
  const matchCount = matchIndices.length;

  // Which match the prev/next buttons are parked on. -1 = "not started",
  // so the first Next lands on match 1 rather than skipping to 2.
  const [cursor, setCursor] = useState(-1);
  useEffect(() => { setCursor(-1); }, [lowerQuery]);

  // Rows are looked up through the container by `data-event-id` rather
  // than per-row ref callbacks: navigation must be able to scroll to a
  // match that is ALREADY the selected row (selection wouldn't change, so
  // the selection effect below wouldn't fire), and a query is cheaper
  // than re-attaching a ref on every row on every render.
  const listRef = useRef<HTMLDivElement | null>(null);
  // Id that navigation already scrolled to, so the selection effect below
  // doesn't immediately re-scroll it with a different alignment.
  const navScrolledIdRef = useRef<number | null>(null);

  const goToMatch = useCallback(
    (delta: number) => {
      if (matchCount === 0) return;
      // From the "not started" state, Next opens on the first hit and
      // Prev on the last one; afterwards both wrap around.
      const next =
        cursor < 0
          ? delta > 0
            ? 0
            : matchCount - 1
          : (cursor + delta + matchCount) % matchCount;
      setCursor(next);
      const ev = events[matchIndices[next]];
      if (!ev) return;
      navScrolledIdRef.current = ev.id;
      onSelect(ev.id);
      if (ev.global_frame !== null) onSeek(ev.global_frame);
      // Center the row: unlike the `nearest` scroll used for selection,
      // stepping through hits reads better when the target lands mid-list.
      listRef.current
        ?.querySelector(`[data-event-id="${ev.id}"]`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    },
    [cursor, events, matchCount, matchIndices, onSeek, onSelect]
  );

  // The event the playhead is currently "inside": the last one at or
  // before it. Before the first event we point at that first event
  // instead of nothing - the list should always show where you are, and
  // "just before the first event" is still, in every useful sense, at
  // the first event.
  //
  // Events at the same frame resolve to the LAST one in list order (>=
  // below), which is the most recent action - e.g. a Kill logged at the
  // same frame as the Score it caused.
  const playheadId = useMemo(() => {
    let bestId: number | null = null;
    let bestFrame = -1;
    let firstId: number | null = null;
    for (const ev of events) {
      if (ev.global_frame === null) continue;
      if (firstId === null) firstId = ev.id;
      if (ev.global_frame <= currentFrame && ev.global_frame >= bestFrame) {
        bestFrame = ev.global_frame;
        bestId = ev.id;
      }
    }
    return bestId ?? firstId;
  }, [events, currentFrame]);

  // Keep the playhead row on screen while the video plays. Only fires
  // when the row actually changes, so scrubbing within one event (or
  // scrolling the list while paused) doesn't yank the view.
  useEffect(() => {
    if (playheadId == null) return;
    if (navScrolledIdRef.current === playheadId) return;
    listRef.current
      ?.querySelector(`[data-event-id="${playheadId}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [playheadId]);

  // Auto-scroll the selected row into view whenever the selection
  // changes. Used when an event is added or clicked elsewhere (e.g. the
  // timeline) - the user shouldn't have to hunt for it in a long list.
  // `block: 'nearest'` avoids gratuitous scrolling when the row is
  // already visible, and uses smooth scrolling so the motion is
  // legible rather than a jarring jump.
  const selectedRowRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (selectedId == null) return;
    if (navScrolledIdRef.current === selectedId) {
      // Search navigation already centered this row - don't fight it.
      navScrolledIdRef.current = null;
      return;
    }
    navScrolledIdRef.current = null;
    const el = selectedRowRef.current;
    if (el)
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId]);

  return (
    <div ref={listRef}>
      <div className="event-search">
        <input
          type="search"
          placeholder="Search events…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            // Enter / Shift+Enter step through hits (browser-find muscle
            // memory); Escape clears without leaving the field.
            if (e.key === "Enter") {
              e.preventDefault();
              goToMatch(e.shiftKey ? -1 : 1);
            } else if (e.key === "Escape") {
              e.preventDefault();
              setQuery("");
            }
          }}
        />
        {trimmed && (
          <>
            <span className="event-search-count">
              {matchCount === 0
                ? "no matches"
                : cursor < 0
                  ? `${matchCount} match${matchCount === 1 ? "" : "es"}`
                  : `${cursor + 1} / ${matchCount}`}
            </span>
            <div className="event-search-nav">
              <button
                type="button"
                onClick={() => goToMatch(-1)}
                disabled={matchCount === 0}
                title="Previous match (Shift+Enter)"
                aria-label="Previous match"
              >
                ↑
              </button>
              <button
                type="button"
                onClick={() => goToMatch(1)}
                disabled={matchCount === 0}
                title="Next match (Enter)"
                aria-label="Next match"
              >
                ↓
              </button>
            </div>
          </>
        )}
      </div>

      {events.length === 0 && (
        <div className="muted" style={{ padding: 12 }}>No events yet.</div>
      )}

      {events.map((ev, i) => {
        const summary = summaries[i];
        const isOrphan = orphanIds.has(ev.id);
        const gap = serveGaps.get(ev.id);
        const gapLabel = gap === undefined ? null : `${gap.toFixed(1)}s`;
        const gapReason =
          gap === undefined
            ? ""
            : `${gap.toFixed(1)}s since the previous event - check for a missing event before this serve`;
        const isMatch =
          lowerQuery !== "" && summary.toLowerCase().includes(lowerQuery);
        const orphanReason =
          ev.type === "cut_start"
            ? "Cut Start without a matching Cut End"
            : ev.type === "cut_end"
            ? "Cut End without a preceding Cut Start"
            : ev.type === "focus_in"
            ? "Focus In without a matching Focus Out - this focus will be skipped at render time"
            : "Orphan event";
        const title = isOrphan
          ? `${summary} - ${orphanReason}`
          : gapReason
            ? `${summary} - ${gapReason}`
            : summary;
        return (
          <div
            key={ev.id}
            data-event-id={ev.id}
            ref={ev.id === selectedId ? selectedRowRef : undefined}
            className={
              "event-row" +
              (ev.id === playheadId ? " current" : "") +
              (ev.id === selectedId ? " selected" : "") +
              (gapLabel && !isOrphan ? " warn" : "") +
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
                {gapLabel && !isOrphan && (
                  <span className="gap-badge" title={gapReason}>
                    ⚠ {gapLabel}
                  </span>
                )}
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
