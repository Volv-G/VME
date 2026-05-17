/** Cut-event analysis shared by the Timeline and the EventList.
 *
 * Cut events are paired globally (not per-clip) by walking events in
 * timeline order: a `cut_start` opens a region; the next `cut_end` closes
 * it. Anything that doesn't pair up is reported as an orphan so the UI can
 * flag it in red.
 */

import type { EventDto } from "../types/api";

export interface CutRegion {
  /** Global frame where the cut begins (the cut_start event). */
  start: number;
  /** Global frame where the cut ends (the cut_end event). */
  end: number;
  /** Event ids for the start and end (useful for highlighting). */
  startId: number;
  endId: number;
}

export interface CutAnalysis {
  /** Matched cut_start <-> cut_end pairs in timeline order. */
  regions: CutRegion[];
  /** Event ids of cut_start / cut_end events without a partner. */
  orphanIds: Set<number>;
}

/** Compute paired regions and orphan ids from a list of events.
 *
 * The list does NOT need to be pre-sorted; we sort by `global_frame`
 * internally. Events missing `global_frame` are skipped.
 */
export function analyzeCuts(events: EventDto[]): CutAnalysis {
  const sorted = [...events]
    .filter((e) => e.global_frame !== null && e.global_frame !== undefined)
    .sort((a, b) => (a.global_frame ?? 0) - (b.global_frame ?? 0));

  const regions: CutRegion[] = [];
  const orphans = new Set<number>();
  let open: { id: number; frame: number } | null = null;

  for (const ev of sorted) {
    if (ev.type === "cut_start") {
      // Nested cut_start (no end before it) → previous one is an orphan.
      if (open !== null) orphans.add(open.id);
      open = { id: ev.id, frame: ev.global_frame as number };
    } else if (ev.type === "cut_end") {
      if (open !== null) {
        regions.push({
          start: open.frame,
          end: ev.global_frame as number,
          startId: open.id,
          endId: ev.id,
        });
        open = null;
      } else {
        // cut_end with nothing open before it.
        orphans.add(ev.id);
      }
    }
  }
  if (open !== null) orphans.add(open.id);

  return { regions, orphanIds: orphans };
}
