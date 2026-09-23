/**
 * Which event types open and close a cut.
 *
 * A timeout is, for now, exactly a cut: the backend models
 * `timeout_start` / `timeout_end` as subclasses of the cut events, so
 * the render drops the span between them. Everything in the editor that
 * reasons about cuts - pairing, orphan warnings, serve-gap markers -
 * asks these instead of comparing against "cut_start" / "cut_end".
 * Otherwise the render would cut a timeout while the editor drew it as
 * something else, and flagged the dead time the cut already removed.
 */
export function isCutStart(type: string): boolean {
  return type === "cut_start" || type === "timeout_start";
}

export function isCutEnd(type: string): boolean {
  return type === "cut_end" || type === "timeout_end";
}

/** "Timeout" or "Cut", for messages that name the marker. */
export function cutNoun(type: string): string {
  return type.startsWith("timeout_") ? "Timeout" : "Cut";
}
