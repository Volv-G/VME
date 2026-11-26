import { useSyncExternalStore, type ReactNode } from "react";
import { post } from "./api";
import type { TeamDoc } from "../shared/docs";

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
/** A reel this person has started watching, kept the same way as reveals. */
export const seenId = (team: string, matchKey: string, number: number | null | undefined) =>
  `seen:${team}/${matchKey}/${number ?? "team"}`;
/**
 * Season totals move with every match, so any change to the list - a new
 * match, one the phone scored, VME taking over a phone score with its own -
 * hides them again: the id is a fingerprint of the list.
 */
export function seasonSpoiler(t: TeamDoc): string {
  const parts = t.tournaments.flatMap((x) => x.matches.map((m) => `${m.key}:${m.scored ? "v" : m.score_source ?? "-"}`));
  return `season:${t.slug}:${hash(parts.sort().join("|"))}`;
}

/** Short, stable, not secret (djb2, base 36). */
export function hash(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

/**
 * Which of a match's videos this person has already come across (opened
 * the match, or played it anywhere), so one added later can be flagged
 * "New" on a match they have seen. One id per video.
 */
export const knownId = (team: string, matchKey: string, video: string) => `known:${team}/${matchKey}/${hash(video)}`;

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
