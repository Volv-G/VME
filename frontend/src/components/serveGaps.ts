import type { EventDto } from "../types/api";

/**
 * A serve that lands a long time after whatever was logged before it is
 * a sign of missing events: rallies don't take that long to restart, so
 * the likely explanations are an unlogged point, a substitution nobody
 * entered, or a serve marked in the wrong place.
 *
 * It's a warning, not an error - long breaks are real (timeouts, set
 * changes, an injury) - so the list flags it and says how long, leaving
 * the judgement to the user.
 */
// Measured over 286 serves across three matches (Liberty 2026-09-03,
// Mercer Island 2026-09-09, Bellevue 2026-09-10). Median gap from the
// previous event to the next serve is 8-11s: a score is logged when
// the rally ends, and the celebration, rotation and walk to the line
// genuinely take that long.
//
//   threshold   flagged
//      10s      126 (44%)
//      12s       73 (26%)
//      15s       18 (6%)
//      20s        1 (0%)
//
// 12s by request: it catches gaps 15s misses, at the cost of more
// noise. Note the spread between matches - 12s flags 39% of Liberty
// but 14% of Mercer Island, because Liberty's scores were logged
// several seconds later in the rally. The threshold is really
// measuring tagging latency, so expect it to feel different per match.
export const SERVE_GAP_WARN_SECONDS = 12;

/**
 * Gap in seconds, keyed by event id, for every `ball_served` whose
 * distance from the PRECEDING event exceeds the threshold.
 *
 * Compared against the immediately preceding event with a known frame,
 * not the preceding serve: what matters is how long the timeline has
 * been silent, whatever the last thing on it was.
 *
 * A serve that opens the list has nothing to be late relative to, so it
 * is never flagged.
 */
export function analyzeServeGaps(
  events: EventDto[],
  fps: number
): Map<number, number> {
  const out = new Map<number, number>();
  if (!Number.isFinite(fps) || fps <= 0) return out;
  let prevFrame: number | null = null;
  for (const ev of events) {
    const frame = ev.global_frame;
    if (frame === null) continue;
    if (ev.type === "ball_served" && prevFrame !== null) {
      const seconds = (frame - prevFrame) / fps;
      if (seconds > SERVE_GAP_WARN_SECONDS) out.set(ev.id, seconds);
    }
    prevFrame = frame;
  }
  return out;
}
