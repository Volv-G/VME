import { useState } from "react";
import type { ClubsDoc, LiveStream, ScheduleChange, TeamDoc, UpcomingMatch } from "../shared/docs";
import { formatDate, formatTime, imageUrl, post, useApi } from "./api";
import { Crest } from "./common";

/**
 * What's coming up, and what's on now.
 *
 * Upcoming matches are VME's; a coach or the admin can add one here, or
 * move or cancel one, and the phone at the gym can too. Those changes
 * wait on the site until VME picks them up (within a minute while its PC
 * is on), so until then they're listed as "waiting for VME".
 *
 * Adding one, a club set up in VME can be picked: it fills in the
 * opponent's name (which can then be made particular, "NW Jrs. Grey")
 * and the colour, and VME gives the match the club's badge.
 */

type Club = ClubsDoc["clubs"][number];

const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const when = (date: string | null, time: string | null) =>
  [date ? formatDate(date) : "", formatTime(time)].filter(Boolean).join(" · ");

/** The big "on now" notice at the top of a team (or match) page. */
export function LiveBanner({ streams, teamName }: { streams: LiveStream[]; teamName: string }) {
  if (!streams.length) return null;
  return (
    <div className="live-banners">
      {streams.map((s) => (
        <a key={s.id} className="live-banner" href={s.url} target="_blank" rel="noreferrer">
          <span className="live-dot" aria-hidden />
          <span className="live-text">
            <strong>Live now</strong>
            <span>
              {teamName}
              {s.opponent ? ` vs ${s.opponent}` : ""}
            </span>
          </span>
          <span className="live-watch">Watch ▶</span>
        </a>
      ))}
    </div>
  );
}

export function LiveChip({ stream }: { stream: LiveStream }) {
  return (
    <a className="live-chip" href={stream.url} target="_blank" rel="noreferrer" title="Watch the live stream">
      ● LIVE
    </a>
  );
}

export function UpcomingSection({ t, onChanged }: { t: TeamDoc; onChanged: () => void }) {
  const [editing, setEditing] = useState<UpcomingMatch | "new" | null>(null);
  const clubsDoc = useApi<ClubsDoc>(t.can_manage ? "/api/doc/clubs" : null);
  const clubs = clubsDoc.state === "ok" ? clubsDoc.doc.clubs : [];
  const upcoming = (t.upcoming ?? []).filter((m) => m.date >= today());
  const pending = t.pending_schedule ?? [];
  const live = t.live ?? [];
  const pendingFor = (key: string) => pending.filter((p) => p.key === key);
  const added = pending.filter((p) => p.action === "add");
  if (!upcoming.length && !added.length && !t.can_manage) return null;
  return (
    <section>
      <h2>
        Upcoming
        {t.can_manage && editing !== "new" && (
          <button type="button" className="small-action" onClick={() => setEditing("new")}>
            ＋ Add match
          </button>
        )}
      </h2>
      {editing === "new" && (
        <ScheduleForm t={t} clubs={clubs} onDone={(changed) => (setEditing(null), changed && onChanged())} />
      )}
      {upcoming.length === 0 && added.length === 0 && editing !== "new" && (
        <p className="muted small">Nothing scheduled.</p>
      )}
      {(upcoming.length > 0 || added.length > 0) && (
        <div className="list">
          {upcoming.map((m) => {
            const waiting = pendingFor(m.key);
            const stream = live.find((s) => s.match_key === m.key);
            if (editing !== "new" && editing?.key === m.key) {
              return <ScheduleForm key={m.key} t={t} clubs={clubs} match={m} onDone={(changed) => (setEditing(null), changed && onChanged())} />;
            }
            return (
              <div key={m.key} className="row upcoming-row">
                {(m.opponent_logo || m.opponent_color) && (
                  <Crest src={m.opponent_logo ?? null} color={m.opponent_color ?? undefined} label={m.opponent} size={32} />
                )}
                <span className="row-main">
                  <span>vs {m.opponent}</span>
                  <span className="muted small">
                    {when(m.date, m.time)} · {m.tournament_name}
                  </span>
                  {waiting.map((p) => (
                    <span key={p.id} className="chip chip-pending">
                      {describe(p)} - waiting for VME
                    </span>
                  ))}
                </span>
                {stream && <LiveChip stream={stream} />}
                {t.can_manage && !stream && (
                  <button type="button" className="small-action" onClick={() => setEditing(m)}>
                    Edit
                  </button>
                )}
              </div>
            );
          })}
          {added.map((p) => (
            <div key={p.id} className="row upcoming-row">
              <span className="row-main">
                <span>vs {p.opponent || clubs.find((c) => c.id === p.club)?.name || p.club}</span>
                <span className="muted small">
                  {when(p.date, p.time)} · {p.tournament}
                </span>
              </span>
              <span className="chip chip-pending">waiting for VME</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function describe(p: ScheduleChange): string {
  if (p.action === "delete") return "Cancelled";
  const parts = [p.date && formatDate(p.date), p.time && formatTime(p.time), p.opponent && `vs ${p.opponent}`].filter(Boolean);
  return `Changing to ${parts.join(", ") || "-"}`;
}

/** Add a match, or change / cancel `match`. */
function ScheduleForm({ t, clubs, match, onDone }: { t: TeamDoc; clubs: Club[]; match?: UpcomingMatch; onDone: (changed: boolean) => void }) {
  const names = t.tournament_names ?? t.tournaments.map((x) => ({ slug: x.slug, name: x.name }));
  const [tournament, setTournament] = useState(match?.tournament_name ?? t.tournaments[0]?.name ?? names[0]?.name ?? "");
  const [date, setDate] = useState(match?.date ?? today());
  const [time, setTime] = useState(match?.time ?? "");
  const [opponent, setOpponent] = useState(match?.opponent ?? "");
  const [club, setClub] = useState<Club | null>(null);
  const [color, setColor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /** The club's name goes in unless another was typed, and its main colour. */
  function pickClub(id: string) {
    const picked = clubs.find((c) => c.id === id) ?? null;
    const typed = opponent.trim().toLowerCase();
    const replaceable = !typed || (club && typed === club.name.toLowerCase()) || (match && typed === match.opponent.toLowerCase());
    if (picked && replaceable) setOpponent(picked.name);
    setClub(picked);
    setColor(picked?.colors[0] ?? null);
  }
  const [error, setError] = useState<string | null>(null);

  async function send(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/schedule/${encodeURIComponent(t.slug)}`, body);
      onDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  function save(e: React.FormEvent) {
    e.preventDefault();
    const dress = club ? { club: club.id, color } : {};
    if (match) {
      void send({ action: "update", key: match.key, date, time: time || null, opponent: opponent.trim(), ...dress });
    } else {
      // The folder name when the typed name is one VME already has.
      const known = names.find((n) => n.name.toLowerCase() === tournament.trim().toLowerCase());
      void send({ action: "add", tournament: known?.slug ?? tournament.trim(), date, time: time || null, opponent: opponent.trim(), ...dress });
    }
  }

  return (
    <form className="form schedule-form" onSubmit={save}>
      {!match && (
        <label>
          Tournament
          <input list="tournament-names" value={tournament} onChange={(e) => setTournament(e.target.value)} required />
          <datalist id="tournament-names">
            {names.map((n) => (
              <option key={n.slug} value={n.name} />
            ))}
          </datalist>
        </label>
      )}
      {clubs.length > 0 && (
        <label>
          <span>Club <span className="muted small">(fills in the name, colour and badge)</span></span>
          <select className="select" value={club?.id ?? ""} onChange={(e) => pickClub(e.target.value)}>
            <option value="">{match ? "Unchanged" : "None"}</option>
            {clubs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {club && (
        <div className="club-colors">
          {club.logo && <img className="crest" src={imageUrl(club.logo)} alt="" width={28} height={28} />}
          <span className="muted small">Colour</span>
          {club.colors.map((c) => (
            <button
              key={c}
              type="button"
              className={`swatch${color === c ? " picked" : ""}`}
              style={{ background: c }}
              title={c}
              aria-label={`Colour ${c}`}
              onClick={() => setColor(c)}
            />
          ))}
          <input type="color" value={color ?? "#888888"} onChange={(e) => setColor(e.target.value)} title="Another colour" />
        </div>
      )}
      <label>
        Opponent
        <input value={opponent} onChange={(e) => setOpponent(e.target.value)} required={!club} maxLength={120} placeholder={club?.name} />
      </label>
      <div className="form-row">
        <label>
          Date
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
        </label>
        <label>
          <span>Time <span className="muted small">(optional)</span></span>
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>
          {match ? "Save" : "Add match"}
        </button>
        <button type="button" onClick={() => onDone(false)} disabled={busy}>
          Cancel
        </button>
        {match && (
          <button
            type="button"
            className="danger"
            disabled={busy}
            onClick={() => confirm(`Take ${match.opponent} on ${formatDate(match.date)} off the schedule?`) && void send({ action: "delete", key: match.key })}
          >
            Cancel match
          </button>
        )}
      </div>
      <p className="muted small">VME applies this within a minute while its PC is on.</p>
    </form>
  );
}
