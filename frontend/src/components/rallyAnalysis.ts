import type { EventDto } from "../types/api";

/**
 * Rally detection for the timeline, mirroring the renderer's own
 * boundary rules (see `backend/app/render/batch.py`): a rally opens at a
 * `ball_served` and closes at the first event that ends the point.
 *
 * Only three things end a point: our Kill, our Ace, or a `score` (which
 * covers every point the OPPONENT wins - there's no Kill of ours to
 * find). Set and game ends are bookkeeping that follow the last point's
 * score, not rally enders.
 */
const RALLY_END_TYPES = new Set(["kill", "ace", "score"]);

export interface RallyRegion {
  /** Global frame of the serve that opened the rally. */
  start: number;
  /**
   * Global frame where the point was decided, or null when the rally
   * never closed and nothing follows it - the drawer extends those to
   * the end of the footage.
   */
  end: number | null;
  startId: number;
  /** Event that closed it; null when unresolved. */
  endId: number | null;
  /**
   * False when no point-ending event was found before the next serve.
   * Usually a point nobody logged, so the band is drawn as a warning
   * rather than a clean rally.
   */
  resolved: boolean;
}

/**
 * Pair serves with point-enders. Input needn't be sorted; events with no
 * `global_frame` are skipped.
 *
 * A serve while a rally is still open closes the previous one as
 * unresolved at that serve - two serves with nothing between them means
 * the point in between went unrecorded, and the alternative (silently
 * dropping the first serve) would hide exactly the mistake worth seeing.
 */
export function analyzeRallies(events: EventDto[]): RallyRegion[] {
  const sorted = [...events]
    .filter((e) => e.global_frame !== null && e.global_frame !== undefined)
    .sort((a, b) => (a.global_frame ?? 0) - (b.global_frame ?? 0));

  const regions: RallyRegion[] = [];
  let open: { id: number; frame: number } | null = null;

  for (const ev of sorted) {
    const frame = ev.global_frame as number;
    if (ev.type === "ball_served") {
      if (open !== null) {
        regions.push({
          start: open.frame,
          end: frame,
          startId: open.id,
          endId: null,
          resolved: false,
        });
      }
      open = { id: ev.id, frame };
    } else if (open !== null && RALLY_END_TYPES.has(ev.type)) {
      regions.push({
        start: open.frame,
        end: frame,
        startId: open.id,
        endId: ev.id,
        resolved: true,
      });
      open = null;
    }
  }
  if (open !== null) {
    regions.push({
      start: open.frame,
      end: null,
      startId: open.id,
      endId: null,
      resolved: false,
    });
  }
  return regions;
}
