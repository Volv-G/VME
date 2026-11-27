import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { Me } from "../shared/api";

export const imageUrl = (key: string) => `/api/image/${key.split("/").map(encodeURIComponent).join("/")}`;
const docPath = (key: string) => `/api/doc/${key.split("/").map(encodeURIComponent).join("/")}`;

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; status: number; message: string }
  | { state: "ok"; doc: T };

/**
 * Open pages keep themselves current: every view loaded with useApi or
 * useDoc fetches again each minute while the tab is visible, and at once
 * when the tab comes back to the front or the network returns. New
 * matches, videos, scores, streams and join requests show up without a
 * reload.
 */
const REFRESH_MS = 60_000;
/** Coming back sooner than this after the last round doesn't refetch. */
const WAKE_GAP_MS = 10_000;
const refreshers = new Set<() => void>();
let lastRefresh = Date.now();

function refreshAll() {
  lastRefresh = Date.now();
  refreshers.forEach((f) => f());
}

/** Something else to redo on each round (App: the spoiler reveals). */
export function onRefresh(f: () => void): () => void {
  refreshers.add(f);
  return () => {
    refreshers.delete(f);
  };
}

/** Started once, by App. */
export function startAutoRefresh(): () => void {
  const visible = () => document.visibilityState === "visible";
  const timer = setInterval(() => visible() && refreshAll(), REFRESH_MS);
  const wake = () => {
    if (visible() && Date.now() - lastRefresh > WAKE_GAP_MS) refreshAll();
  };
  document.addEventListener("visibilitychange", wake);
  window.addEventListener("online", wake);
  window.addEventListener("focus", wake);
  return () => {
    clearInterval(timer);
    document.removeEventListener("visibilitychange", wake);
    window.removeEventListener("online", wake);
    window.removeEventListener("focus", wake);
  };
}

/**
 * Any GET under /api, reloadable. `path` null = don't load (yet).
 *
 * An automatic refresh is quiet: when it fails (a blip, the Access
 * session lapsing) the page keeps what it shows rather than turning into
 * an error, and when nothing changed nothing re-renders.
 */
export function useApi<T>(path: string | null): Loaded<T> & { reload: () => void } {
  const [res, setRes] = useState<Loaded<T>>({ state: "loading" });
  const [tick, setTick] = useState({ n: 0, quiet: false });
  const reload = useCallback(() => setTick((t) => ({ n: t.n + 1, quiet: false })), []);
  useEffect(() => onRefresh(() => setTick((t) => ({ n: t.n + 1, quiet: true }))), []);
  // The body last shown, and for which path.
  const shown = useRef<{ path: string; text: string } | null>(null);
  useEffect(() => {
    if (path === null) return;
    let live = true;
    const quiet = tick.quiet && shown.current?.path === path;
    const fail = (status: number, message: string) => {
      if (!live || quiet) return;
      shown.current = null;
      setRes({ state: "error", status, message });
    };
    fetch(path, { credentials: "same-origin" })
      .then(async (r) => {
        if (!live) return;
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          fail(r.status, body.error || r.statusText);
          return;
        }
        const text = await r.text();
        if (!live || (shown.current?.path === path && shown.current.text === text)) return;
        const doc = JSON.parse(text) as T;
        shown.current = { path, text };
        setRes({ state: "ok", doc });
      })
      .catch((e) => fail(0, String(e)));
    return () => {
      live = false;
    };
  }, [path, tick]);
  return { ...res, reload };
}

/** One published document, by key (see shared/docs.ts). Refreshed
 *  like everything else (see REFRESH_MS). */
export function useDoc<T>(key: string): Loaded<T> & { reload: () => void } {
  return useApi<T>(docPath(key));
}

export async function post<T = unknown>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data as T;
}

/** Who is signed in, loaded once for the whole app. */
export const MeContext = createContext<{ me: Me | null; reload: () => void }>({ me: null, reload: () => {} });
export const useMe = () => useContext(MeContext);

/** Invite codes opened on this device, by team, so a member can ask for
 *  another player later without digging out the link. */
export const savedInvites = {
  all(): Record<string, string> {
    try {
      return JSON.parse(localStorage.getItem("invites") || "{}") as Record<string, string>;
    } catch {
      return {};
    }
  },
  get(team: string): string | null {
    return this.all()[team] ?? null;
  },
  set(team: string, code: string) {
    try {
      localStorage.setItem("invites", JSON.stringify({ ...this.all(), [team]: code }));
    } catch {
      /* private mode: the link still works, it just isn't remembered */
    }
  },
};

export function formatDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  return isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

/** "18:30" -> "6:30 PM" (or however this device writes times). */
export function formatTime(hhmm: string | null | undefined): string {
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date(2000, 0, 1, h, m);
  return isNaN(d.getTime()) ? hhmm : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function formatWhen(ms: number): string {
  return new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
