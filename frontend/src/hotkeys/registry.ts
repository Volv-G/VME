import { DEFAULT_BINDINGS } from "./catalog";
import { comboFromEvent, isTypingTarget, normalizeCombo } from "./combos";
import type { ActionHandler, ActionId, Bindings, KeyCombo } from "./types";

const STORAGE_KEY = "vme.hotkeys.bindings.v1";

// --- Internal mutable state ---------------------------------------------

const handlers = new Map<ActionId, ActionHandler>();
let bindings: Bindings = loadBindings();
let comboIndex: Map<KeyCombo, ActionId> = buildComboIndex(bindings);
const subscribers = new Set<() => void>();

// --- Persistence --------------------------------------------------------

function loadBindings(): Bindings {
  try {
    const raw = typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY);
    if (!raw) return cloneDefaults();
    const parsed = JSON.parse(raw) as Bindings;
    // Merge with defaults so newly-added actions get their default combos
    // even if the user has saved an older bindings snapshot.
    return { ...cloneDefaults(), ...parsed };
  } catch {
    return cloneDefaults();
  }
}

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(bindings));
  } catch {
    // Storage may be unavailable (private mode, quota); ignore.
  }
}

function cloneDefaults(): Bindings {
  // Deep-ish clone so callers can't mutate the shared default arrays.
  const out: Bindings = {};
  for (const [id, combos] of Object.entries(DEFAULT_BINDINGS)) out[id] = [...combos];
  return out;
}

// --- Index --------------------------------------------------------------

function buildComboIndex(b: Bindings): Map<KeyCombo, ActionId> {
  const m = new Map<KeyCombo, ActionId>();
  for (const [actionId, combos] of Object.entries(b)) {
    for (const raw of combos) {
      const c = normalizeCombo(raw);
      if (!c) continue;
      // Last writer wins; bindings are user-controlled and unique by
      // intent. The configurator should warn on collisions.
      m.set(c, actionId);
    }
  }
  return m;
}

function notify(): void {
  for (const cb of subscribers) cb();
}

// --- Public API ---------------------------------------------------------

export function registerActionHandler(id: ActionId, handler: ActionHandler): () => void {
  handlers.set(id, handler);
  return () => {
    // Only remove if this handler is still the current one (guards against
    // a stale unmount cleanup wiping a freshly registered handler from a
    // remount in StrictMode / fast refresh).
    if (handlers.get(id) === handler) handlers.delete(id);
  };
}

export function getBindings(): Bindings {
  return bindings;
}

export function setBindings(next: Bindings): void {
  bindings = { ...next };
  comboIndex = buildComboIndex(bindings);
  persist();
  notify();
}

export function setBindingForAction(id: ActionId, combos: KeyCombo[]): void {
  setBindings({ ...bindings, [id]: combos.map(normalizeCombo).filter(Boolean) });
}

export function resetBindings(): void {
  setBindings(cloneDefaults());
}

/** Subscribe to bindings changes (for the configurator UI). */
export function subscribe(cb: () => void): () => void {
  subscribers.add(cb);
  return () => subscribers.delete(cb);
}

// --- Dispatcher ---------------------------------------------------------

let installed = false;

export function installHotkeys(): () => void {
  if (installed) return () => { /* no-op */ };
  installed = true;
  window.addEventListener("keydown", onKeyDown, { capture: false });
  return () => {
    window.removeEventListener("keydown", onKeyDown, { capture: false });
    installed = false;
  };
}

function onKeyDown(e: KeyboardEvent): void {
  // Always let the user type into form fields without hijacking keys.
  if (isTypingTarget(e.target)) return;
  // Suppress key auto-repeat: actions are toggles, not held inputs.
  if (e.repeat) return;

  const combo = comboFromEvent(e);
  if (!combo) return;
  const actionId = comboIndex.get(combo);
  if (!actionId) return;
  const handler = handlers.get(actionId);
  if (!handler) return;

  e.preventDefault();
  handler();
}
