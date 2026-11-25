import { useSyncExternalStore, type ReactNode } from "react";
import { post } from "./api";

/**
 * No spoilers: scores and stats stay hidden until the viewer asks, so
 * the videos can be watched first. What someone revealed is kept on the
 * site per person (server/reveals.ts), so every device agrees; the
 * browser keeps a copy to draw the page at once, and anything revealed
 * before reveals were kept on the site is handed over on the first load.
 * (A convenience, not a secret: the numbers are in the page data.)
 */
const KEY = "revealed";

function loadLocal(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

const revealedIds = new Set<string>(loadLocal());
let version = 0;
const listeners = new Set<() => void>();

function changed() {
  version++;
  try {
    localStorage.setItem(KEY, JSON.stringify([...revealedIds]));
  } catch {
    /* private mode: the site still remembers */
  }
  listeners.forEach((l) => l());
}

function send(ids: string[]) {
  for (let i = 0; i < ids.length; i += 500) {
    post("/api/reveals", { ids: ids.slice(i, i + 500) }).catch(() => {
      /* offline: kept in the browser, handed over on the next load */
    });
  }
}

/** Once per visit: merge the site's reveals with this browser's. */
export async function loadReveals(): Promise<void> {
  try {
    const r = await fetch("/api/reveals", { credentials: "same-origin" });
    if (!r.ok) return;
    const { ids } = (await r.json()) as { ids: string[] };
    const server = new Set(ids);
    const onlyHere = [...revealedIds].filter((id) => !server.has(id));
    ids.forEach((id) => revealedIds.add(id));
    changed();
    if (onlyHere.length) send(onlyHere);
  } catch {
    /* the browser's copy will do */
  }
}

export function isRevealed(id: string): boolean {
  return revealedIds.has(id);
}

export function reveal(...ids: string[]) {
  const fresh = ids.filter((id) => !revealedIds.has(id));
  if (!fresh.length) return;
  fresh.forEach((id) => revealedIds.add(id));
  changed();
  send(fresh);
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** Re-render when anything is revealed (here, or loaded from the site). */
export function useReveals(): { revealed: (id: string) => boolean; reveal: (...ids: string[]) => void } {
  useSyncExternalStore(subscribe, () => version);
  return { revealed: isRevealed, reveal };
}

export function useRevealed(id: string): [boolean, () => void] {
  const { revealed } = useReveals();
  return [revealed(id), () => reveal(id)];
}

export const matchSpoiler = (team: string, key: string) => `match:${team}/${key}`;
/** Season totals move with every match, so a new match hides them again. */
export const seasonSpoiler = (team: string, matches: number) => `season:${team}:${matches}`;

/** `children` once revealed; until then a button that reveals them. */
export function Spoiler({
  revealed,
  onReveal,
  label,
  note,
  children,
}: {
  revealed: boolean;
  onReveal: () => void;
  label: string;
  note?: string;
  children: ReactNode;
}) {
  if (revealed) return <>{children}</>;
  return (
    <div className="spoiler">
      <button type="button" className="reveal" onClick={onReveal}>
        👁 {label}
      </button>
      {note && <span className="muted small">{note}</span>}
    </div>
  );
}
