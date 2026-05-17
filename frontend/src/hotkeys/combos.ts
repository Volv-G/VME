import type { KeyCombo } from "./types";

/** Modifier names in canonical order (matches normalizeCombo / comboFromEvent). */
const MOD_ORDER = ["Ctrl", "Alt", "Shift", "Meta"] as const;
type Mod = (typeof MOD_ORDER)[number];

/**
 * Convert a KeyboardEvent.code to a short, layout-independent key name.
 * Returns null for pure modifier keys (so they don't form a combo by themselves).
 *
 * Using `e.code` (not `e.key`) makes bindings layout-independent: "S" means
 * the physical S key regardless of QWERTY/Dvorak/etc.
 */
export function keyNameFromCode(code: string): string | null {
  if (
    code === "ControlLeft" || code === "ControlRight" ||
    code === "AltLeft" || code === "AltRight" ||
    code === "ShiftLeft" || code === "ShiftRight" ||
    code === "MetaLeft" || code === "MetaRight" ||
    code === "OSLeft" || code === "OSRight"
  ) return null;

  if (code.startsWith("Key")) return code.slice(3);          // KeyS  -> S
  if (code.startsWith("Digit")) return code.slice(5);        // Digit1 -> 1
  if (code.startsWith("Numpad")) return "Num" + code.slice(6); // Numpad1 -> Num1
  // Space, Enter, Tab, Escape, Backspace, ArrowUp/Down/Left/Right,
  // F1..F24, Home, End, PageUp, PageDown, Insert, Delete, etc. pass through.
  return code;
}

/** Build a canonical combo string from a KeyboardEvent. Returns "" for modifier-only events. */
export function comboFromEvent(e: KeyboardEvent): KeyCombo {
  const key = keyNameFromCode(e.code);
  if (!key) return "";
  const mods: Mod[] = [];
  if (e.ctrlKey) mods.push("Ctrl");
  if (e.altKey) mods.push("Alt");
  if (e.shiftKey) mods.push("Shift");
  if (e.metaKey) mods.push("Meta");
  return [...mods, key].join("+");
}

/** Normalize a user-provided combo string to canonical form. */
export function normalizeCombo(combo: string): KeyCombo {
  const parts = combo.split("+").map((p) => p.trim()).filter(Boolean);
  if (parts.length === 0) return "";
  const key = parts[parts.length - 1];
  const mods = new Set(parts.slice(0, -1).map(canonicalizeMod));
  const ordered = MOD_ORDER.filter((m) => mods.has(m));
  return [...ordered, key].join("+");
}

function canonicalizeMod(m: string): Mod {
  const lower = m.toLowerCase();
  if (lower === "ctrl" || lower === "control") return "Ctrl";
  if (lower === "alt" || lower === "option") return "Alt";
  if (lower === "shift") return "Shift";
  if (lower === "meta" || lower === "cmd" || lower === "command" || lower === "win") return "Meta";
  // Unknown modifier — preserve as-is by upper-casing first char.
  return (m.charAt(0).toUpperCase() + m.slice(1).toLowerCase()) as Mod;
}

/** True if the event target is a form control or contenteditable region. */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    t.isContentEditable
  );
}
