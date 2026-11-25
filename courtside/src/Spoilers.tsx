import { useState, type ReactNode } from "react";

/**
 * No spoilers: scores and stats stay hidden until the viewer asks, so
 * the videos can be watched first. What someone revealed is remembered
 * in their browser only (it's a convenience, not a secret: the numbers
 * are in the page data either way).
 */
const KEY = "revealed";
const KEEP = 500;

function load(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

export function isRevealed(id: string): boolean {
  return load().includes(id);
}

function remember(id: string) {
  try {
    const all = load().filter((x) => x !== id);
    all.push(id);
    localStorage.setItem(KEY, JSON.stringify(all.slice(-KEEP)));
  } catch {
    /* private mode: revealed for this visit only */
  }
}

export function useRevealed(id: string): [boolean, () => void] {
  // The id, not a flag: the same page component can move on to another match.
  const [revealed, setRevealed] = useState<string | null>(null);
  return [
    revealed === id || isRevealed(id),
    () => {
      remember(id);
      setRevealed(id);
    },
  ];
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
