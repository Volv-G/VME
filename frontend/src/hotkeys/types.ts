/**
 * Stable, machine-friendly identifier for an action, e.g. "playback.play1x".
 * Use dot-namespacing to group related actions for the configurator UI.
 */
export type ActionId = string;

/**
 * A canonical, human-readable key combination string, e.g. "S", "Space",
 * "Ctrl+Z", "Shift+Alt+ArrowUp". Modifier order is enforced by
 * `normalizeCombo` (Ctrl, Alt, Shift, Meta, then key).
 */
export type KeyCombo = string;

/** Static metadata for an action. Consumed by the configurator. */
export interface ActionDef {
  id: ActionId;
  /** Short human-readable label, e.g. "Play at 1×". */
  label: string;
  /** Group name for the configurator, e.g. "Playback", "Editing". */
  category: string;
  /** Optional longer description / tooltip. */
  description?: string;
}

/** Map: action id → list of key combos bound to it. */
export type Bindings = Record<ActionId, KeyCombo[]>;

/** Live handler bound to an action at runtime. */
export type ActionHandler = () => void;
