import { createElement, type ReactNode } from "react";
import blockPng from "../assets/block.png";
import blockPointPng from "../assets/block_point.png";

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
 *   Cuts (red)              ~5    cut_start, cut_end, timeout_start/_end
 *   Offense (orange/gold)   ~30   kill, positive_attack, ace, assist
 *   Set boundary (gold)     ~45   set_end
 *   Lifecycle (green)       ~130  game_start, game_end
 *   Substitution (teal)     ~170  substitution
 *   Serve flow (cyan)       ~185  first_serve, ball_served, replay
 *   Defense (blue)          ~215  block, perfect_pass, dig, dive
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
  // Timeouts ARE cuts for now, so they stay in the family - a shade
  // deeper, to tell a called timeout from dead time trimmed by hand.
  timeout_start: "#da3633",
  timeout_end: "#e5534b",

  // --- Player offense (orange -> gold) ---
  kill: "#f78166",
  positive_attack: "#f0a07a",
  ace: "#ffa657",
  assist: "#ffd33d",

  // --- Set boundary (dark gold, sits between offense and lifecycle) ---
  set_end: "#bf8700",

  // --- Game lifecycle (green) ---
  game_start: "#3fb950",
  game_end: "#2ea043",

  // --- Substitution (teal - between lifecycle green and serve cyan) ---
  substitution: "#19b8a6",
  libero_rotation: "#19b8a6",
  liberos: "#19b8a6",
  rotation: "#19b8a6",

  // --- Serve flow (cyan family) ---
  first_serve: "#39c5cf",
  ball_served: "#56d4dd",
  replay: "#22a3a8",

  // --- Player defense (blue) ---
  block: "#1f6feb",
  block_point: "#1f6feb",
  perfect_pass: "#3b8eea",
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
  positive_attack: "⚡",
  ace: "🎯",
  assist: "🤝",

  // Player defense
  block: "🧱",
  block_point: "✋",
  perfect_pass: "👌",
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
  libero_rotation: "🔃",
  liberos: "🦺",
  rotation: "↻",

  // Lifecycle / cuts
  game_start: "▶️",
  game_end: "🏁",
  set_end: "📍",
  cut_start: "✂️",
  cut_end: "🎬",
  timeout_start: "⏸️",
  timeout_end: "⏯️",

  // Annotations / structural
  message: "💬",
  clip_transition: "↔️",

  default: "•",
};

/** Color lookup with fallback. */
export function eventColor(type: string): string {
  return EVENT_COLORS[type] ?? EVENT_COLORS.default;
}

/**
 * Drawn icons for the types no emoji says well: a volleyball block (two
 * hands over the net), and the block that won the point (the same, with
 * an impact burst). Made with the image model on this PC; see
 * assets/block*.png.
 */
const EVENT_IMAGES: Record<string, string> = {
  block: blockPng,
  block_point: blockPointPng,
};

/** Icon lookup with fallback: an emoji, or for the drawn ones an image
 *  sized to the text around it. */
export function eventIcon(type: string): ReactNode {
  const src = EVENT_IMAGES[type];
  if (src) return createElement("img", { src, alt: "", className: "event-icon-img", draggable: false });
  return EVENT_ICONS[type] ?? EVENT_ICONS.default;
}
