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
 * Breathing room left at each end of a one-click cut.
 *
 * The flagged span is dead time bracketed by two real moments, and both
 * of them are worth keeping: the previous event is usually a score, whose
 * celebration is the end of the rally, and the serve needs its run-up to
 * make sense. Cutting hard against either lands mid-motion.
 *
 * 3s by request. Applied symmetrically because the padding is protecting
 * the two neighbouring events equally - unlike reel or rally windows,
 * where the asymmetry comes from events being tagged when they finish.
 */
export const GAP_CUT_PAD_SECONDS = 3;

export interface ServeGap {
  /** Cut-adjusted distance from the previous event, in seconds. */
  seconds: number;
  /** Global frame of the preceding non-marker event. */
  prevFrame: number;
  /** Global frame of the serve itself. */
  frame: number;
  /**
   * Span a one-click cut would remove, or null when one can't be placed
   * safely (see `proposeCut`). Pre-computed here rather than at click
   * time so the badge can render as a dead label instead of offering an
   * action that would fail.
   */
  cut: { start: number; end: number } | null;
}

/**
 * Gap info, keyed by event id, for every `ball_served` whose distance
 * from the PRECEDING event exceeds the threshold.
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
): Map<number, ServeGap> {
  const out = new Map<number, ServeGap>();
  if (!Number.isFinite(fps) || fps <= 0) return out;
  const { regions } = analyzeCuts(events);
  // Every cut marker's frame, paired or not. `regions` only covers matched
  // pairs, and an unpaired cut_start inside a proposed span would still
  // scramble the pairing, so both are needed to judge safety.
  const markerFrames: number[] = [];
  for (const ev of events) {
    const f = ev.global_frame;
    if (f === null || f === undefined) continue;
    if (ev.type === "cut_start" || ev.type === "cut_end") markerFrames.push(f);
  }
  let prevFrame: number | null = null;
  for (const ev of events) {
    const frame = ev.global_frame;
    if (frame === null || frame === undefined) continue;
    if (ev.type === "ball_served" && prevFrame !== null) {
      const cut = cutFramesBetween(regions, prevFrame, frame);
      const seconds = (frame - prevFrame - cut) / fps;
      if (seconds > SERVE_GAP_WARN_SECONDS) {
        out.set(ev.id, {
          seconds,
          prevFrame,
          frame,
          cut: proposeCut(prevFrame, frame, fps, regions, markerFrames),
        });
      }
    }
    if (!MARKER_TYPES.has(ev.type)) prevFrame = frame;
  }
  return out;
}

/**
 * Span to remove between two events, padded by `GAP_CUT_PAD_SECONDS`, or
 * null when inserting one would make a mess.
 *
 * Refused in two cases:
 *
 * - The span would touch an existing cut marker or overlap an existing
 *   region. Cuts pair sequentially by frame order, so a new pair placed
 *   around an existing one re-pairs into `[new start, old start]` and
 *   `[old end, new end]` - which leaves the middle, the part that was
 *   already being cut, in the render. Silently inverting the user's
 *   earlier edit is far worse than declining.
 * - Nothing is left after padding. The threshold makes this unreachable
 *   today (a >12s gap keeps >6s), but the constants are independent and
 *   a zero-length cut is not worth creating.
 */
function proposeCut(
  prevFrame: number,
  frame: number,
  fps: number,
  regions: { start: number; end: number }[],
  markerFrames: number[]
): { start: number; end: number } | null {
  const pad = Math.round(GAP_CUT_PAD_SECONDS * fps);
  const start = prevFrame + pad;
  const end = frame - pad;
  if (end <= start) return null;
  if (markerFrames.some((f) => f >= start && f <= end)) return null;
  if (regions.some((r) => r.end > start && r.start < end)) return null;
  return { start, end };
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
