import { useEffect, useState } from "react";
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

function StatsTable({ stats }: { stats: StatsDto }) {
  if (stats.matches === 0) return <p className="muted">No matches yet.</p>;
  const row = (line: StatsLineDto, key: string, total = false) => (
    <tr key={key} className={total ? "stats-total" : undefined}>
      <td className="stats-num">{line.number ?? ""}</td>
      <td className="stats-name">{line.name || (line.number != null ? `#${line.number}` : "")}</td>
      {stats.columns.map((c) => (
        <td key={c}>{line.events[c] || ""}</td>
      ))}
      <td>{line.serves || ""}</td>
      <td>{line.serves_won || ""}</td>
      <td className="muted">{pct(line.serves_won, line.serves)}</td>
    </tr>
  );
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        {stats.matches} {stats.matches === 1 ? "match" : "matches"}. A serve is a
        rally that ended in a point while we served, credited to the player in
        position 1.
      </p>
      <div className="stats-scroll">
        <table className="stats-table">
          <thead>
            <tr>
              <th>#</th>
              <th className="stats-name">Player</th>
              {stats.columns.map((c) => (
                <th key={c} title={LABELS[c] ?? c}>
                  {eventIcon(c)} {LABELS[c] ?? c}
                </th>
              ))}
              <th>Serves</th>
              <th>Won</th>
              <th>Won %</th>
            </tr>
          </thead>
          <tbody>
            {stats.players.map((p) => row(p, String(p.number)))}
            {row(stats.total, "total", true)}
          </tbody>
        </table>
      </div>
    </>
  );
}
