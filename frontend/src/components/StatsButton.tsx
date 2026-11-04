import { Fragment, useEffect, useState } from "react";
import { api } from "../api/client";
import type { StatsDto, StatsLineDto, StatsScope } from "../types/api";
import { eventIcon } from "./eventStyle";
import { Modal } from "./Modal";

const LABELS: Record<string, string> = {
  kill: "Kill",
  ace: "Ace",
  block_point: "Block pt",
  block: "Block",
  assist: "Assist",
  dig: "Dig",
  dive: "Dive",
  highlight: "Highlight",
};

/**
 * "Stats" button and the table it opens: every event type credited to
 * each of our players, their serves and serves won, and the team total.
 *
 * Fetched on open rather than with the page: a team's numbers mean
 * reading every match it has, and most visits never ask for them.
 */
export function StatsButton({ scope, title }: { scope: StatsScope; title: string }) {
  const [open, setOpen] = useState(false);
  const [stats, setStats] = useState<StatsDto | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setStats(null);
    setError(null);
    api
      .getStats(scope)
      .then((s) => live && setStats(s))
      .catch((e) => live && setError(String(e?.message ?? e)));
    return () => {
      live = false;
    };
    // The scope's fields, not the object: callers build it inline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, scope.team, scope.tournament, scope.date, scope.match]);

  return (
    <>
      <button onClick={() => setOpen(true)} title="Events, serves and serves won, per player">
        Stats
      </button>
      <Modal open={open} title={title} onClose={() => setOpen(false)} width="min(1000px, 96vw)">
        {error && <div className="error">{error}</div>}
        {!stats && !error && <p className="muted">Counting…</p>}
        {stats && <StatsTable stats={stats} />}
      </Modal>
    </>
  );
}

function pct(won: number, of: number): string {
  return of ? `${Math.round((100 * won) / of)}%` : "";
}

/** Sortable columns: the jersey, the name, an event type, or a serve column. */
type SortKey =
  | "number"
  | "name"
  | "plays"
  | "serves"
  | "serves_won"
  | "pct"
  | `ev:${string}`;

function sortValue(line: StatsLineDto, key: SortKey): number | string {
  switch (key) {
    case "number":
      return line.number ?? 0;
    case "name":
      return (line.name || "").toLocaleLowerCase();
    case "plays":
      return line.plays;
    case "serves":
      return line.serves;
    case "serves_won":
      return line.serves_won;
    case "pct":
      // No serves sorts below 0%: there is nothing to rank.
      return line.serves ? line.serves_won / line.serves : -1;
    default:
      return line.events[key.slice(3)] ?? 0;
  }
}

function StatsTable({ stats }: { stats: StatsDto }) {
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({
    key: "number",
    desc: false,
  });
  if (stats.matches === 0) return <p className="muted">No matches yet.</p>;

  // A first click on a number column puts the most at the top - that is
  // the question being asked - and a second click flips it.
  const sortBy = (key: SortKey) =>
    setSort((s) =>
      s.key === key ? { key, desc: !s.desc } : { key, desc: key !== "number" && key !== "name" }
    );
  const players = [...stats.players].sort((a, b) => {
    const va = sortValue(a, sort.key);
    const vb = sortValue(b, sort.key);
    const c = va < vb ? -1 : va > vb ? 1 : (a.number ?? 0) - (b.number ?? 0);
    return sort.desc && va !== vb ? -c : c;
  });
  const th = (key: SortKey, label: React.ReactNode, className?: string, title?: string) => (
    <th
      className={`stats-sortable${className ? ` ${className}` : ""}`}
      onClick={() => sortBy(key)}
      title={title ? `${title} - click to sort` : "Click to sort"}
      aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : "none"}
    >
      {label}
      <span className="stats-arrow">{sort.key === key ? (sort.desc ? " ▼" : " ▲") : ""}</span>
    </th>
  );
  const row = (line: StatsLineDto, key: string, total = false) => (
    <tr key={key} className={total ? "stats-total" : undefined}>
      <td className="stats-num">{line.number ?? ""}</td>
      <td className="stats-name">{line.name || (line.number != null ? `#${line.number}` : "")}</td>
      {stats.columns.map((c) => (
        <td key={c}>{line.events[c] || ""}</td>
      ))}
      <td className="stats-plays">{line.plays || ""}</td>
      <td>{line.serves || ""}</td>
      <td>{line.serves_won || ""}</td>
      <td className="muted">{pct(line.serves_won, line.serves)}</td>
    </tr>
  );
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        {stats.matches} {stats.matches === 1 ? "match" : "matches"}. Plays are
        what each player's reel shows: their events, with ones close together
        counted once. A serve is a rally that ended in a point while we
        served, credited to the player in position 1.
      </p>
      <div className="stats-scroll">
        <table className="stats-table">
          <thead>
            <tr>
              {th("number", "#")}
              {th("name", "Player", "stats-name")}
              {stats.columns.map((c) => (
                <Fragment key={c}>
                  {th(`ev:${c}`, `${eventIcon(c)} ${LABELS[c] ?? c}`, undefined, LABELS[c] ?? c)}
                </Fragment>
              ))}
              {th(
                "plays",
                "🎬 Plays",
                "stats-plays",
                "Plays in the player's reel: every event above, with those " +
                  "close together (a dig and the kill it set up) counted once"
              )}
              {th("serves", "Serves")}
              {th("serves_won", "Won")}
              {th("pct", "Won %")}
            </tr>
          </thead>
          <tbody>
            {players.map((p) => row(p, String(p.number)))}
            {row(stats.total, "total", true)}
          </tbody>
        </table>
      </div>
    </>
  );
}
