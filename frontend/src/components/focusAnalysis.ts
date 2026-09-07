/** Focus-event analysis: flag FocusIn events the renderer will skip.
 *
 *  The render-side rule (see `backend/app/render/batch.py`): focus spans
 *  are paired stack-wise, FocusIn to the next FocusOut, and each pair
 *  becomes one focused clip. A FocusIn that never gets a FocusOut is
 *  dropped with a warning - that is the only case the backend skips, so
 *  it is the only case flagged red here.
 *
 *  A span whose player has no tagged action is NOT an error: the backend
 *  looks for the player's first event inside the marked span, then in
 *  the surrounding rally, and falls back to a generic `highlight` label
 *  when neither turns anything up. The user bracketed that moment on
 *  purpose, so the clip still renders.
 *
 *  This module is intentionally a pure function over `EventDto[]` so
 *  it can be reused by Timeline + EventList without sharing state.
 */

import type { EventDto } from "../types/api";

export interface FocusAnalysis {
  /** FocusIn event ids with no matching FocusOut - the backend skips
   *  these, so no clip is produced for them. */
  orphanIds: Set<number>;
  /** Matched spans as `[focusInId, startFrame, endFrame]`, in the order
   *  the backend would render them. */
  spans: Array<{ focusInId: number; start: number; end: number }>;
}

export function analyzeFocus(events: EventDto[]): FocusAnalysis {
  // Sort by global frame once. Events without a resolvable global frame
  // are skipped - they can't be placed on the timeline at all, and the
  // backend's `_indexed_events` filters them the same way.
  const sorted = [...events]
    .filter((e) => e.global_frame !== null && e.global_frame !== undefined)
    .sort((a, b) => (a.global_frame as number) - (b.global_frame as number));

  const openStack: EventDto[] = [];
  const spans: FocusAnalysis["spans"] = [];

  for (const ev of sorted) {
    const g = ev.global_frame as number;
    if (ev.type === "focus_in") {
      openStack.push(ev);
    } else if (ev.type === "focus_out") {
      const inEv = openStack.pop();
      // A FocusOut with no open FocusIn, or one that isn't strictly
      // after it, is ignored by the backend too.
      if (!inEv) continue;
      const inG = inEv.global_frame as number;
      if (g <= inG) continue;
      spans.push({ focusInId: inEv.id, start: inG, end: g });
    }
  }

  return {
    orphanIds: new Set(openStack.map((e) => e.id)),
    spans,
  };
}
