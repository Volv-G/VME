import type { ActionDef, Bindings } from "./types";

/**
 * Source of truth for all known actions. The future configurator UI iterates
 * this list to render rebind controls. Components register live handlers by
 * id via `useHotkeyAction`.
 *
 * Conventions:
 *  - id uses "category.verb" dot-notation
 *  - category groups related actions in the configurator
 */
export const ACTION_CATALOG: readonly ActionDef[] = [
  {
    id: "playback.toggle",
    label: "Play / pause",
    category: "Playback",
    description: "Toggle playback at the most recently selected speed.",
  },
  {
    id: "playback.play1x",
    label: "Play at 1×",
    category: "Playback",
  },
  {
    id: "playback.play2x",
    label: "Play at 2×",
    category: "Playback",
  },
  {
    id: "playback.play4x",
    label: "Play at 4×",
    category: "Playback",
  },
  {
    id: "playback.back1s",
    label: "Step back 1 second",
    category: "Playback",
  },
  {
    id: "playback.fwd1s",
    label: "Step forward 1 second",
    category: "Playback",
  },
] as const;

/** Default key bindings shipped with the app. */
export const DEFAULT_BINDINGS: Bindings = {
  "playback.toggle": ["Space"],
  "playback.play1x": ["S"],
  "playback.play2x": ["W"],
  "playback.play4x": [],
  "playback.back1s": ["A"],
  "playback.fwd1s": ["D"],
};

/** Look up an action def by id (handy for the configurator). */
export function findAction(id: string): ActionDef | undefined {
  return ACTION_CATALOG.find((a) => a.id === id);
}
