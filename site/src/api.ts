import { useEffect, useState } from "react";

export const imageUrl = (key: string) => `/api/image/${key.split("/").map(encodeURIComponent).join("/")}`;

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; status: number; message: string }
  | { state: "ok"; doc: T };

/** One published document, by key (see shared/docs.ts). */
export function useDoc<T>(key: string): Loaded<T> {
  const [res, setRes] = useState<Loaded<T>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    setRes({ state: "loading" });
    fetch(`/api/doc/${key.split("/").map(encodeURIComponent).join("/")}`, { credentials: "same-origin" })
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
  }, [key]);
  return res;
}

export function formatDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  return isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
