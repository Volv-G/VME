import type { EventDto } from "../types/api";
import { analyzeCuts } from "./cutAnalysis";

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

// Markers, not things that happened in the match. A cut_start dropped
// in front of a serve is the user saying "skip this", not evidence that
// anything was logged there, so it must not reset the clock - otherwise
// marking dead time hides the warning about that very dead time, which
// is backwards.
const MARKER_TYPES = new Set(["cut_start", "cut_end", "clip_transition"]);

/**
 * Gap in seconds, keyed by event id, for every `ball_served` whose
 * distance from the PRECEDING event exceeds the threshold.
 *
 * Compared against the immediately preceding event with a known frame,
 * not the preceding serve: what matters is how long the timeline has
 * been silent, whatever the last thing on it was.
 *
 * Two things are excluded from "the preceding event":
 *
 * - Cut markers and clip transitions, per `MARKER_TYPES`.
 * - Footage inside a COMPLETE cut region, which is subtracted from the
 *   measured gap: that time is removed from the render, so it isn't
 *   dead time in the finished video and there is nothing to warn about.
 *   An unclosed `cut_start` subtracts nothing - a half-marked cut
 *   removes no footage, and the warning has to survive long enough for
 *   the user to finish marking it.
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
  const { regions } = analyzeCuts(events);
  let prevFrame: number | null = null;
  for (const ev of events) {
    const frame = ev.global_frame;
    if (frame === null || frame === undefined) continue;
    if (ev.type === "ball_served" && prevFrame !== null) {
      const cut = cutFramesBetween(regions, prevFrame, frame);
      const seconds = (frame - prevFrame - cut) / fps;
      if (seconds > SERVE_GAP_WARN_SECONDS) out.set(ev.id, seconds);
    }
    if (!MARKER_TYPES.has(ev.type)) prevFrame = frame;
  }
  return out;
}

/** Frames inside complete cut regions that overlap `[lo, hi]`. */
function cutFramesBetween(
  regions: { start: number; end: number }[],
  lo: number,
  hi: number
): number {
  let total = 0;
  for (const r of regions) {
    const from = Math.max(lo, r.start);
    const to = Math.min(hi, r.end);
    if (to > from) total += to - from;
  }
  return total;
}
