/** Cut-event analysis shared by the Timeline and the EventList.
 *
 * Cut events are paired globally (not per-clip) by walking events in
 * timeline order: a `cut_start` opens a region; the next `cut_end` closes
 * it. A marker left unpaired gets a second chance against the lifecycle
 * events, so a break bounded by `set_end` / `game_end` / `game_start`
 * only needs ONE marker. Anything still unpaired is reported as an orphan
 * so the UI can flag it in red.
 *
 * This mirrors `backend/app/render/frame_map_builder.py`
 * (`_collect_cut_regions` / `_resolve_open_cuts`). The two have to agree:
 * the timeline draws what this returns, and the renderer drops what that
 * returns.
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
  /**
   * True when one side of the region is a lifecycle event standing in for
   * a missing cut marker rather than a marker the user placed.
   */
  openEnded?: boolean;
}

export interface CutAnalysis {
  /** Matched cut_start <-> cut_end pairs in timeline order. */
  regions: CutRegion[];
  /** Event ids of cut_start / cut_end events without a partner. */
  orphanIds: Set<number>;
  /**
   * Marker ids that were closed by a lifecycle event instead of by the
   * opposite marker. Not orphans - they cut footage - but worth naming
   * so the UI can explain where the other edge came from.
   */
  openEndedIds: Set<number>;
}

const LIFECYCLE = new Set(["game_start", "game_end", "set_end"]);
// A serve between a lone marker and the lifecycle event means a rally is
// inside the proposed region, which is never intended - so the pairing is
// refused and the marker stays red.
const SERVES = new Set(["ball_served", "first_serve"]);

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
  const orphans: { idx: number; ev: EventDto }[] = [];
  const openEndedIds = new Set<number>();
  type OpenCut = { idx: number; id: number; frame: number };
  let open: OpenCut | null = null;

  for (let idx = 0; idx < sorted.length; idx++) {
    const ev = sorted[idx];
    if (ev.type === "cut_start") {
      // Nested cut_start (no end before it) → previous one is unpaired.
      if (open) orphans.push({ idx: open.idx, ev: sorted[open.idx] });
      open = { idx, id: ev.id, frame: ev.global_frame as number };
    } else if (ev.type === "cut_end") {
      if (open) {
        regions.push({
          start: open.frame,
          end: ev.global_frame as number,
          startId: open.id,
          endId: ev.id,
        });
        open = null;
      } else {
        // cut_end with nothing open before it.
        orphans.push({ idx, ev });
      }
    }
  }
  if (open) orphans.push({ idx: open.idx, ev: sorted[open.idx] });

  const orphanIds = new Set<number>();
  for (const { idx, ev } of orphans) {
    const forward = ev.type === "cut_start";
    const step = forward ? 1 : -1;
    let partner: EventDto | null = null;
    for (let j = idx + step; j >= 0 && j < sorted.length; j += step) {
      const c = sorted[j];
      if (SERVES.has(c.type)) break;
      if (LIFECYCLE.has(c.type)) {
        partner = c;
        break;
      }
    }
    if (partner === null) {
      orphanIds.add(ev.id);
      continue;
    }
    // The lifecycle event's own frame survives: the fade it carries has to
    // land on real footage either side of the removed span.
    const evFrame = ev.global_frame as number;
    const pFrame = partner.global_frame as number;
    const start = forward ? evFrame : pFrame + 1;
    const end = forward ? pFrame : evFrame;
    if (start >= end) {
      orphanIds.add(ev.id);
      continue;
    }
    openEndedIds.add(ev.id);
    regions.push({
      start,
      end,
      startId: forward ? ev.id : partner.id,
      endId: forward ? partner.id : ev.id,
      openEnded: true,
    });
  }
  regions.sort((a, b) => a.start - b.start);

  return { regions, orphanIds, openEndedIds };
}
