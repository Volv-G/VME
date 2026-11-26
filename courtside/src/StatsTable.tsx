import { Fragment, useState } from "react";
import type { Stats, StatsLine } from "../shared/docs";

const LABELS: Record<string, [string, string]> = {
  kill: ["💥", "Kill"],
  positive_attack: ["⚡", "Positive attack"],
  ace: ["🎯", "Ace"],
  block_point: ["✋", "Block point"],
  block: ["🧱", "Block"],
  assist: ["🤝", "Assist"],
  perfect_pass: ["👌", "Perfect pass"],
  dig: ["🛡️", "Dig"],
  dive: ["🤿", "Dive"],
  highlight: ["⭐", "Highlight"],
};

type SortKey = "number" | "name" | "plays" | "serves" | "serves_won" | "pct" | `ev:${string}`;

function value(l: StatsLine, k: SortKey): number | string {
  switch (k) {
    case "number": return l.number ?? 0;
    case "name": return l.name.toLocaleLowerCase();
    case "plays": return l.plays;
    case "serves": return l.serves;
    case "serves_won": return l.serves_won;
    case "pct": return l.serves ? l.serves_won / l.serves : -1;
    default: return l.events[k.slice(3)] ?? 0;
  }
}

/**
 * Per-player numbers, sortable by any column, team total last (when the
 * viewer gets it: coaches and the admin do, families see only their own
 * player). A column nobody in the table has anything in is left out.
 */
export function StatsTable({ stats, total = true }: { stats: Stats; total?: boolean }) {
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "number", desc: false });
  if (!stats.players.length) return <p className="muted">No stats yet.</p>;
  const totalLine = total ? stats.total : null;
  const shown = totalLine ? [...stats.players, totalLine] : stats.players;
  const columns = stats.columns.filter((c) => shown.some((l) => (l.events[c] ?? 0) > 0));
  const plays = shown.some((l) => l.plays > 0);
  const serves = shown.some((l) => l.serves > 0);
  const by = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: key !== "number" && key !== "name" }));
  const rows = [...stats.players].sort((a, b) => {
    const va = value(a, sort.key);
    const vb = value(b, sort.key);
    const c = va < vb ? -1 : va > vb ? 1 : (a.number ?? 0) - (b.number ?? 0);
    return sort.desc && va !== vb ? -c : c;
  });
  const th = (key: SortKey, label: React.ReactNode, title?: string, cls?: string) => (
    <th
      className={`sortable${cls ? ` ${cls}` : ""}`}
      onClick={() => by(key)}
      title={title}
      aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : "none"}
    >
      {label}
      {sort.key === key && <span className="arrow">{sort.desc ? " ▼" : " ▲"}</span>}
    </th>
  );
  const row = (l: StatsLine, key: string, total = false) => (
    <tr key={key} className={total ? "total" : undefined}>
      <td className="num">{l.number ?? ""}</td>
      <td className="name">{l.name}</td>
      {columns.map((c) => <td key={c}>{l.events[c] || ""}</td>)}
      {plays && <td className="plays">{l.plays || ""}</td>}
      {serves && (
        <>
          <td>{l.serves || ""}</td>
          <td>{l.serves_won || ""}</td>
          <td className="muted">{l.serves ? `${Math.round((100 * l.serves_won) / l.serves)}%` : ""}</td>
        </>
      )}
    </tr>
  );
  return (
    <div className="table-scroll">
      <table className="stats">
        <thead>
          <tr>
            {th("number", "#")}
            {th("name", "Player", undefined, "name")}
            {columns.map((c) => (
              <Fragment key={c}>{th(`ev:${c}`, LABELS[c]?.[0] ?? c, LABELS[c]?.[1] ?? c)}</Fragment>
            ))}
            {plays && th("plays", "Plays", "Highlighted plays: events close together count once", "plays")}
            {serves && (
              <>
                {th("serves", "Serves", "Rallies served that ended in a point")}
                {th("serves_won", "Won", "Serves that won the point")}
                {th("pct", "Serves won %", "Share of serves that won the point", "pct")}
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => row(p, String(p.number)))}
          {totalLine && row(totalLine, "total", true)}
        </tbody>
      </table>
    </div>
  );
}
