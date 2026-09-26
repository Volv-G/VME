import { cutNoun, isCutEnd, isCutStart } from "./cutTypes";
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
import { GAP_CUT_PAD_SECONDS, analyzeServeGaps } from "./serveGaps";
import { analyzeLiberoSwaps, type LiberoSwap } from "./liberoSwaps";
import { analyzeAceServers } from "./aceServers";
import { summarizeEvent } from "./eventSummary";
import { eventIcon } from "./eventStyle";

interface Props {
  events: EventDto[];
  selectedId: number | null;
  /** Playhead position, so the list can follow playback. */
  currentFrame: number;
  onSelect: (id: number) => void;
  /** Bump to scroll the selected row into view again, for when the list
   *  was hidden and the selection did not change. */
  revealSelected?: number;
  onDelete: (id: number) => void;
  /** Shift an event along the timeline. Optional: without it the
   *  nudge buttons are not rendered at all, rather than rendered dead. */
  onNudge?: (id: number, seconds: number) => Promise<void>;
  /** Drop an event immediately after another (null = the very start);
   *  it lands one frame past its new neighbour. Optional: without it
   *  rows are not draggable at all, rather than draggable and inert. */
  onMove?: (id: number, afterId: number | null) => Promise<void>;
  /** Delete every event on the match. Optional: without it the button
   *  is not rendered at all. The caller owns the confirmation. */
  onClearAll?: () => void;
  /** Open the global shift dialog. Optional, same rule. */
  onShiftAll?: () => void;
  /** The match's libero jerseys, for spotting a libero substitution
   *  whose two halves drifted apart. Without them nothing is flagged. */
  liberos?: readonly number[];
  onSeek: (globalFrame: number) => void;
  /**
   * Insert a cut_start/cut_end pair over a global-frame span. Optional:
   * without it the gap warnings stay informational labels.
   */
  onInsertCut?: (startFrame: number, endFrame: number) => Promise<void>;
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
  revealSelected,
  onDelete,
  onNudge,
  onMove,
  onClearAll,
  onShiftAll,
  liberos,
  onSeek,
  onInsertCut,
  fps,
  homeRoster,
  opponentRoster,
  homeName,
  opponentName,
}: Props) {
  // Which event is mid-nudge, so its buttons can be disabled without
  // freezing the whole list. Null = nothing in flight.
  const [nudgingId, setNudgingId] = useState<number | null>(null);

  // Drag-to-reorder. `draggingId` is the row being carried; `dropAt` is
  // where it would land - an index into the list plus which edge, so the
  // indicator sits in the gap between rows rather than on one of them.
  const [draggingId, setDraggingId] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<{ index: number; below: boolean } | null>(
    null
  );
  const [movingId, setMovingId] = useState<number | null>(null);

  /** The event a drop at `target` would land behind, or null for the top. */
  function anchorFor(target: { index: number; below: boolean }): number | null {
    const before = target.below ? target.index : target.index - 1;
    return before < 0 ? null : events[before]?.id ?? null;
  }

  async function handleDrop(target: { index: number; below: boolean }) {
    const id = draggingId;
    setDraggingId(null);
    setDropAt(null);
    if (id === null || !onMove) return;
    const afterId = anchorFor(target);
    // Dropping a row back where it already is: the anchor is the row
    // itself, or the one it already follows. Either way there is
    // nothing to do, and asking would still cost a frame of movement.
    if (afterId === id) return;
    const from = events.findIndex((e) => e.id === id);
    const landsAfter = afterId === null ? -1 : events.findIndex((e) => e.id === afterId);
    if (from >= 0 && landsAfter === from - 1) return;
    setMovingId(id);
    try {
      await onMove(id, afterId);
    } finally {
      setMovingId(null);
    }
  }

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
  const { orphanIds: cutOrphans, openEndedIds } = analyzeCuts(events);
  const { orphanIds: focusOrphans } = analyzeFocus(events);
  const orphanIds = useMemo(
    () => new Set([...cutOrphans, ...focusOrphans]),
    [cutOrphans, focusOrphans]
  );
  // Serves that arrive suspiciously long after the previous event -
  // usually something wasn't logged. Warning, not error: real breaks
  // exist, so we show the gap and let the user judge.
  const serveGaps = useMemo(() => analyzeServeGaps(events, fps), [events, fps]);

  // Serve id whose cut is being created, so the badge can't be
  // double-clicked into two overlapping cut pairs while the POSTs are in
  // flight (the list only re-analyzes once the match comes back).
  const [cuttingId, setCuttingId] = useState<number | null>(null);
  const insertCut = useCallback(
    async (serveId: number, span: { start: number; end: number }) => {
      if (!onInsertCut || cuttingId !== null) return;
      setCuttingId(serveId);
      try {
        await onInsertCut(span.start, span.end);
      } finally {
        setCuttingId(null);
      }
    },
    [onInsertCut, cuttingId]
  );

  // Libero exit/entry pairs that drifted apart, and the same one-at-a-
  // time guard: the list only re-analyzes once the match comes back, so
  // a second click before then would be aimed at stale positions.
  const [aligningId, setAligningId] = useState<number | null>(null);
  // Aces credited to somebody who was not serving. Pure inspection, so
  // no busy state and no action - the fix is a judgement about which of
  // the two records is wrong.
  const aceMismatches = useMemo(() => analyzeAceServers(events), [events]);
  const liberoSwaps = useMemo(
    () => (onMove ? analyzeLiberoSwaps(events, liberos ?? [], fps) : new Map()),
    [events, liberos, fps, onMove]
  );
  const alignSwap = useCallback(
    async (swap: LiberoSwap) => {
      if (!onMove || aligningId !== null) return;
      setAligningId(swap.id);
      try {
        // Onto the exit, which lands it one frame later - the same dead
        // ball, and it keeps the pair in the order they happened.
        await onMove(swap.id, swap.anchorId);
      } finally {
        setAligningId(null);
      }
    },
    [onMove, aligningId]
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

  // Same scroll, asked for explicitly. `scrollIntoView` on a hidden
  // element does nothing, so the pane becoming visible needs its own
  // nudge - centred, because arriving at a list you could not see is
  // easier to read from the middle than from an edge.
  useEffect(() => {
    if (!revealSelected || selectedId == null) return;
    listRef.current
      ?.querySelector(`[data-event-id="${selectedId}"]`)
      ?.scrollIntoView({ block: "center" });
  }, [revealSelected, selectedId]);

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
        {/* Only offered when the caller can actually do it, and only
            when there is something to clear - a live "Clear all" on an
            empty list is a trap with no upside. */}
        {onShiftAll && events.length > 0 && !trimmed && (
          <button
            type="button"
            className="event-shift-all"
            onClick={onShiftAll}
            title="Move every event earlier or later, together"
          >
            ⇄ Shift
          </button>
        )}
        {onClearAll && events.length > 0 && !trimmed && (
          <button
            type="button"
            className="danger event-clear-all"
            onClick={onClearAll}
            title="Delete every event on this match"
          >
            Clear all
          </button>
        )}
      </div>

      {events.length === 0 && (
        <div className="muted" style={{ padding: 12 }}>No events yet.</div>
      )}

      {events.map((ev, i) => {
        const summary = summaries[i];
        const isOrphan = orphanIds.has(ev.id);
        const gap = serveGaps.get(ev.id);
        const swap = liberoSwaps.get(ev.id) as LiberoSwap | undefined;
        const aceBad = aceMismatches.get(ev.id);
        const gapLabel = gap === undefined ? null : `${gap.seconds.toFixed(1)}s`;
        const canCut = !!gap?.cut && !!onInsertCut;
        const gapReason =
          gap === undefined
            ? ""
            : `${gap.seconds.toFixed(1)}s since the previous event - check for a missing event before this serve` +
              (canCut
                ? `\nClick to cut from ${GAP_CUT_PAD_SECONDS}s after the previous event to ${GAP_CUT_PAD_SECONDS}s before this serve`
                : gap.cut === null
                  ? "\n(can't auto-cut: the gap already contains cut markers)"
                  : "");
        const isMatch =
          lowerQuery !== "" && summary.toLowerCase().includes(lowerQuery);
        const orphanReason = isCutStart(ev.type)
          ? `${cutNoun(ev.type)} Start without a matching end (and no Set End / Game End before the next serve to close it)`
          : isCutEnd(ev.type)
            ? `${cutNoun(ev.type)} End without a preceding start (and no Set End / Game End after the previous serve to open it)`
            : ev.type === "focus_in"
            ? "Focus In without a matching Focus Out - this focus will be skipped at render time"
            : "Orphan event";
        // An open-ended cut has no second marker, so it looks unpaired at a
        // glance. Say where the other edge came from rather than leaving the
        // user to wonder whether it cuts anything.
        const isOpenEnded = openEndedIds.has(ev.id);
        const title = isOrphan
          ? `${summary} - ${orphanReason}`
          : isOpenEnded
            ? `${summary} - open cut, closed by the neighbouring ${
                isCutStart(ev.type) ? "Set End / Game End" : "Set End / Game Start"
              }`
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
              (isMatch ? " match" : "") +
              (ev.id === draggingId ? " dragging" : "") +
              (dropAt?.index === i && !dropAt.below ? " drop-above" : "") +
              (dropAt?.index === i && dropAt.below ? " drop-below" : "")
            }
            draggable={!!onMove && movingId === null}
            onDragStart={(e) => {
              setDraggingId(ev.id);
              e.dataTransfer.effectAllowed = "move";
              // Firefox starts no drag at all without payload.
              e.dataTransfer.setData("text/plain", String(ev.id));
            }}
            onDragEnd={() => {
              setDraggingId(null);
              setDropAt(null);
            }}
            onDragOver={(e) => {
              if (draggingId === null) return;
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              // Which half of the row the pointer is over decides which
              // gap it lands in - without it the first position would be
              // unreachable, since every drop would be "after" something.
              const r = e.currentTarget.getBoundingClientRect();
              const below = e.clientY > r.top + r.height / 2;
              setDropAt((d) =>
                d && d.index === i && d.below === below ? d : { index: i, below }
              );
            }}
            onDrop={(e) => {
              e.preventDefault();
              const r = e.currentTarget.getBoundingClientRect();
              void handleDrop({ index: i, below: e.clientY > r.top + r.height / 2 });
            }}
            onClick={() => {
              onSelect(ev.id);
              if (ev.global_frame !== null) onSeek(ev.global_frame);
            }}
          >
            {onMove && (
              <span
                className="event-grip"
                aria-hidden="true"
                title="Drag to reorder. The event lands one frame after the one you drop it behind."
              >
                ⠿
              </span>
            )}
            {/* Two-line content column: summary on top, frame/time below.
                Giving the summary the full row width avoids the previous
                mid-name truncation when the timestamp shared the line. */}
            <div className="event-row-body">
              <div className="event-row-text" title={title}>
                {isOrphan && <span className="orphan-badge" title={orphanReason}>!</span>}
                {aceBad && (
                  <span
                    className="orphan-badge"
                    title={
                      `Credited to #${aceBad.credited}, but #${aceBad.server} was ` +
                      "serving. An ace is won by the server, so one of the two " +
                      "is wrong - check the rotation around this point."
                    }
                  >
                    !
                  </span>
                )}
                {isOpenEnded && (
                  <span className="open-cut-badge" title={title}>⇥</span>
                )}
                {gapLabel && !isOrphan && (
                  canCut ? (
                    <button
                      type="button"
                      className="gap-badge actionable"
                      title={gapReason}
                      disabled={cuttingId !== null}
                      onClick={(e) => {
                        // The row seeks on click; cutting shouldn't move
                        // the playhead out from under the user.
                        e.stopPropagation();
                        void insertCut(ev.id, gap!.cut!);
                      }}
                    >
                      {cuttingId === ev.id ? "…" : "✂"} {gapLabel}
                    </button>
                  ) : (
                    <span className="gap-badge" title={gapReason}>
                      ⚠ {gapLabel}
                    </span>
                  )
                )}
                {swap && (
                  <button
                    type="button"
                    className="gap-badge actionable"
                    title={
                      `This is the other half of the #${swap.jersey} libero ` +
                      `substitution ${swap.seconds.toFixed(1)}s earlier - one ` +
                      "swap at one dead ball, logged as two moments.\n" +
                      "Click to move it back onto that one."
                    }
                    disabled={aligningId !== null}
                    onClick={(e) => {
                      // The row seeks on click; aligning shouldn't drag
                      // the playhead along with it.
                      e.stopPropagation();
                      void alignSwap(swap);
                    }}
                  >
                    {aligningId === ev.id ? "…" : "⇡"} {swap.seconds.toFixed(1)}s
                  </button>
                )}
                <span className="event-row-icon" aria-hidden="true">
                  {eventIcon(ev.type)}
                </span>
                {renderWithHighlight(summary, lowerQuery)}
              </div>
              <div className="ev-frame">{frameLabel(ev.global_frame, fps)}</div>
            </div>
            {onNudge && ev.global_frame !== null && (
              <span className="event-nudge">
                {[-1, 1].map((sec) => (
                  <button
                    key={sec}
                    type="button"
                    className="nudge-btn"
                    disabled={nudgingId !== null}
                    title={`Move this event ${sec > 0 ? "later" : "earlier"} by 1 second`}
                    onClick={(e) => {
                      // The row seeks on click; nudging shouldn't drag
                      // the playhead along with it.
                      e.stopPropagation();
                      setNudgingId(ev.id);
                      void onNudge(ev.id, sec).finally(() => setNudgingId(null));
                    }}
                  >
                    {sec > 0 ? "+1s" : "-1s"}
                  </button>
                ))}
              </span>
            )}
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
