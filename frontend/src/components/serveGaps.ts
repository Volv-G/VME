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
export const SERVE_GAP_WARN_SECONDS = 10;

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
