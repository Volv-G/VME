import { useEffect, useRef } from "react";
import { registerActionHandler } from "./registry";
import type { ActionHandler, ActionId } from "./types";

/**
 * Register a live handler for an action by id. The handler is captured by
 * ref each render so the registered function always sees the latest closure
 * (no need to memoize or list deps).
 *
 * Example:
 *   useHotkeyAction("playback.play1x", () => playAt(1));
 */
export function useHotkeyAction(id: ActionId, handler: ActionHandler): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    return registerActionHandler(id, () => handlerRef.current());
  }, [id]);
}
