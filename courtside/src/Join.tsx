import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { JoinTeam, Membership, Role } from "../shared/api";
import { post, savedInvites, useApi, useMe } from "./api";
import { Crest, Gate, teamPath, TeamTheme } from "./common";

const STATUS: Record<Membership["status"], string> = {
  pending: "waiting for approval",
  approved: "approved",
  declined: "declined",
};

/**
 * A team's invite link: its players to claim, or the coach's place. A
 * claim waits for the admin; until then nothing opens.
 */
export function JoinPage() {
  const { code = "" } = useParams();
  const res = useApi<JoinTeam>(`/api/join/${encodeURIComponent(code)}`);

  useEffect(() => {
    if (res.state === "ok") savedInvites.set(res.doc.slug, code);
  }, [res, code]);

  return (
    <Gate res={res}>
      {(team) => <TeamClaim code={code} team={team} />}
    </Gate>
  );
}

function TeamClaim({ code, team }: { code: string; team: JoinTeam }) {
  const { me, reload } = useMe();
  const [choice, setChoice] = useState<{ role: Role; number: number | null } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mine = (me?.memberships ?? []).filter((m) => m.team === team.slug);
  const approved = mine.some((m) => m.status === "approved");
  const existing = (role: Role, number: number | null) =>
    mine.find((m) => m.role === role && (role === "coach" || m.number === number));

  async function send() {
    if (!choice) return;
    setBusy(true);
    setError(null);
    try {
      await post(`/api/join/${encodeURIComponent(code)}`, { ...choice, note });
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
    <>
      <TeamTheme color={team.color} logo={team.logo} />
      <header className="team-head">
        <div className="team-title">
          <Crest src={team.logo} color={team.color} label={team.name} size={64} />
          <div>
            <h1>{team.name}</h1>
            <p className="muted small">
              Choose your player - or the coach's place. The team admin approves each request.
            </p>
          </div>
        </div>
      </header>
      {approved && (
        <p className="notice">
          You're in. <Link to={teamPath(team.slug)}>Go to {team.name}</Link>
        </p>
      )}
      {mine.some((m) => m.status === "pending") && (
        <p className="notice">Your request is with the admin. Once it's approved, this site opens on {team.name}.</p>
      )}
      <section>
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
      </section>
    </>
  );
}
