/**
 * Visual styling for match events: timeline marker colors and UI icons.
 *
 * Single source of truth so the Timeline, EventList, and ControlsPanel
 * stay in sync. To rebrand an event family edit only this file.
 *
 * ---
 *
 * Color palette rationale
 * -----------------------
 * Hues are spread around the wheel so unrelated event families are
 * maximally distinguishable on a dense timeline; *within* a family the
 * hues are kept close so e.g. a Kill and its Assist read as obviously
 * related at a glance.
 *
 *   Family                  Hue   Members
 *   ----------------------- ----- ----------------------------------------
 *   Cuts (red)              ~5    cut_start, cut_end
 *   Offense (orange/gold)   ~30   kill, ace, assist
 *   Set boundary (gold)     ~45   set_end
 *   Lifecycle (green)       ~130  game_start, game_end
 *   Substitution (teal)     ~170  substitution
 *   Serve flow (cyan)       ~185  first_serve, ball_served, replay
 *   Defense (blue)          ~215  block, dig, dive
 *   Score (indigo)          ~255  score, score_correction
 *   Focus (purple)          ~275  focus_in, focus_out
 *   Highlight (pink)        ~330  highlight
 *   Neutral / structural    n/a   message, clip_transition
 *
 * Edit colors freely - the key constraint is keeping intra-family hues
 * within ~20 deg of each other so the relatedness still reads.
 */

export const EVENT_COLORS: Record<string, string> = {
  // --- Cuts (red - reads as "warning / edit zone") ---
  cut_start: "#f85149",
  cut_end: "#ff7b72",

  // --- Player offense (orange -> gold) ---
  kill: "#f78166",
  ace: "#ffa657",
  assist: "#ffd33d",

  // --- Set boundary (dark gold, sits between offense and lifecycle) ---
  set_end: "#bf8700",

  // --- Game lifecycle (green) ---
  game_start: "#3fb950",
  game_end: "#2ea043",

  // --- Substitution (teal - between lifecycle green and serve cyan) ---
  substitution: "#19b8a6",

  // --- Serve flow (cyan family) ---
  first_serve: "#39c5cf",
  ball_served: "#56d4dd",
  replay: "#22a3a8",

  // --- Player defense (blue) ---
  block: "#1f6feb",
  dig: "#58a6ff",
  dive: "#79c0ff",

  // --- Score events (indigo - separates from defense blue) ---
  score: "#7c5cff",
  score_correction: "#6c4bd9",

  // --- Focus spans (purple) ---
  focus_in: "#a371f7",
  focus_out: "#8957e5",

  // --- Highlight (pink) ---
  highlight: "#ff7eb6",

  // --- Neutral / structural ---
  message: "#8b949e",
  clip_transition: "#d29922",

  // Fallback for unknown / new types - same blue used pre-refactor.
  default: "#6ea1ff",
};

/**
 * Emoji icon per event type. Used in action buttons (ControlsPanel) and
 * as a leading glyph in EventList rows for fast visual scanning. Keep
 * to single-codepoint or short ZWJ sequences so layout stays tight.
 *
 * Icons are decoration only - every visible reference is paired with a
 * text label or tooltip, so screen readers don't depend on them.
 */
export const EVENT_ICONS: Record<string, string> = {
  // Player offense
  kill: "💥",
  ace: "🎯",
  assist: "🤝",

  // Player defense
  block: "🧱",
  dig: "🛡️",
  dive: "🤿",

  // Misc player attribution
  highlight: "⭐",

  // Focus spans
  focus_in: "🔍",
  focus_out: "🔭",

  // Serve flow
  first_serve: "🏐",
  ball_served: "🏐",
  replay: "🔁",

  // Score
  score: "➕",
  score_correction: "✏️",

  // Substitution
  substitution: "🔄",

  // Lifecycle / cuts
  game_start: "▶️",
  game_end: "🏁",
  set_end: "📍",
  cut_start: "✂️",
  cut_end: "🎬",

  // Annotations / structural
  message: "💬",
  clip_transition: "↔️",

  default: "•",
};

/** Color lookup with fallback. */
export function eventColor(type: string): string {
  return EVENT_COLORS[type] ?? EVENT_COLORS.default;
}

/** Icon lookup with fallback. */
export function eventIcon(type: string): string {
  return EVENT_ICONS[type] ?? EVENT_ICONS.default;
}
