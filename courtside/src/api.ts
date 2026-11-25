import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { Me } from "../shared/api";

export const imageUrl = (key: string) => `/api/image/${key.split("/").map(encodeURIComponent).join("/")}`;
const docPath = (key: string) => `/api/doc/${key.split("/").map(encodeURIComponent).join("/")}`;

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; status: number; message: string }
  | { state: "ok"; doc: T };

/** Any GET under /api, reloadable. `path` null = don't load (yet). */
export function useApi<T>(path: string | null): Loaded<T> & { reload: () => void } {
  const [res, setRes] = useState<Loaded<T>>({ state: "loading" });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (path === null) return;
    let live = true;
    fetch(path, { credentials: "same-origin" })
      .then(async (r) => {
        if (!live) return;
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          setRes({ state: "error", status: r.status, message: body.error || r.statusText });
          return;
        }
        setRes({ state: "ok", doc: (await r.json()) as T });
      })
      .catch((e) => live && setRes({ state: "error", status: 0, message: String(e) }));
    return () => {
      live = false;
    };
  }, [path, tick]);
  return { ...res, reload };
}

/** One published document, by key (see shared/docs.ts). */
export function useDoc<T>(key: string): Loaded<T> {
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

export function formatWhen(ms: number): string {
  return new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
