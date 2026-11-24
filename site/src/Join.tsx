import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { JoinTeam, Membership, Role } from "../shared/api";
import { post, savedInvite, useApi, useMe } from "./api";
import { Crest, Gate } from "./common";

const STATUS: Record<Membership["status"], string> = {
  pending: "waiting for approval",
  approved: "approved",
  declined: "declined",
};

/**
 * The invite link: every team, and in each its players to claim or the
 * coach's place. A claim waits for the admin; until then nothing opens.
 */
export function JoinPage() {
  const { code = "" } = useParams();
  const res = useApi<{ teams: JoinTeam[] }>(`/api/join/${encodeURIComponent(code)}`);
  const [team, setTeam] = useState<string | null>(null);

  useEffect(() => {
    if (res.state === "ok") savedInvite.set(code);
  }, [res.state, code]);

  return (
    <>
      <header className="masthead">
        <Link to="/" className="muted back">← Home</Link>
        <h1>Join a team</h1>
        <p className="muted">
          Choose your team, then your player - or the coach's place. The team admin approves each request.
        </p>
      </header>
      <Gate res={res}>
        {({ teams }) => {
          const chosen = teams.find((t) => t.slug === team);
          if (!chosen) {
            return teams.length ? (
              <div className="cards">
                {teams.map((t) => (
                  <button key={t.slug} className="card team-card" style={{ borderTopColor: t.color }} onClick={() => setTeam(t.slug)}>
                    <Crest src={t.logo} color={t.color} label={t.name} size={56} />
                    <strong>{t.name}</strong>
                  </button>
                ))}
              </div>
            ) : (
              <p className="notice">No teams have been published yet.</p>
            );
          }
          return <TeamClaim code={code} team={chosen} onBack={() => setTeam(null)} />;
        }}
      </Gate>
    </>
  );
}

function TeamClaim({ code, team, onBack }: { code: string; team: JoinTeam; onBack: () => void }) {
  const { me, reload } = useMe();
  const [choice, setChoice] = useState<{ role: Role; number: number | null } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mine = (me?.memberships ?? []).filter((m) => m.team === team.slug);
  const existing = (role: Role, number: number | null) =>
    mine.find((m) => m.role === role && (role === "coach" || m.number === number));

  async function send() {
    if (!choice) return;
    setBusy(true);
    setError(null);
    try {
      await post(`/api/join/${encodeURIComponent(code)}`, { team: team.slug, ...choice, note });
      setChoice(null);
      setNote("");
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const option = (role: Role, number: number | null, label: React.ReactNode) => {
    const had = existing(role, number);
    const selected = choice?.role === role && choice.number === number;
    return (
      <button
        key={`${role}-${number}`}
        className={`row claim${selected ? " selected" : ""}`}
        disabled={!!had && had.status !== "declined"}
        onClick={() => setChoice({ role, number })}
      >
        <span className="row-main">{label}</span>
        {had && <span className={`chip chip-${had.status}`}>{STATUS[had.status]}</span>}
      </button>
    );
  };

  return (
    <section>
      <button className="link back" onClick={onBack}>← All teams</button>
      <div className="team-title">
        <Crest src={team.logo} color={team.color} label={team.name} size={48} />
        <h2>{team.name}</h2>
      </div>
      <h3>I'm a parent of / I am</h3>
      <div className="list">
        {team.players.map((p) =>
          option(
            "player",
            p.number,
            <span>
              <strong>#{p.number}</strong> {p.name}
            </span>
          )
        )}
      </div>
      <h3>Or</h3>
      <div className="list">{option("coach", null, <strong>I'm a coach</strong>)}</div>
      {choice && (
        <div className="card form">
          <label>
            Who are you? <span className="muted small">(helps the admin recognise you)</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Kate's mom, Anna" maxLength={200} />
          </label>
          <div className="actions">
            <button className="primary" onClick={send} disabled={busy}>
              {busy ? "Sending…" : "Send request"}
            </button>
            <button onClick={() => setChoice(null)}>Cancel</button>
          </div>
          {error && <p className="error">{error}</p>}
        </div>
      )}
      {mine.some((m) => m.status === "pending") && (
        <p className="notice">Your request is with the admin. You'll see the team on the home page once it's approved.</p>
      )}
    </section>
  );
}
