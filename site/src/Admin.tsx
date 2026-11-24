import { useState } from "react";
import { Link } from "react-router-dom";
import type { AdminState, Decision, MembershipOut } from "../shared/api";
import { formatWhen, post, useApi, useMe } from "./api";
import { Gate } from "./common";

function who(m: MembershipOut) {
  return m.role === "coach" ? "Coach" : m.player_name;
}

/** Requests to approve, who has access, and each team's invite link. */
export function AdminPage() {
  const res = useApi<AdminState>("/api/admin");
  const { reload: reloadMe } = useMe();
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function act(path: string, body: unknown) {
    setError(null);
    try {
      await post(path, body);
      res.reload();
      reloadMe();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  const decide = (m: MembershipOut, action: Decision) => {
    if (action === "remove" && !confirm(`Remove ${m.email} from ${m.team_name}?`)) return;
    void act(`/api/admin/members/${m.id}`, { action });
  };

  return (
    <>
      <header className="masthead">
        <Link to="/" className="muted back">← Home</Link>
        <h1>Admin</h1>
      </header>
      {error && <p className="error">{error}</p>}
      <Gate res={res}>
        {(s) => {
          const byTeam = new Map<string, MembershipOut[]>();
          for (const m of s.members) byTeam.set(m.team_name, [...(byTeam.get(m.team_name) ?? []), m]);
          return (
            <>
              <section>
                <h2>Requests {s.requests.length > 0 && <span className="badge">{s.requests.length}</span>}</h2>
                {s.requests.length === 0 ? (
                  <p className="muted">Nothing waiting.</p>
                ) : (
                  <div className="list">
                    {s.requests.map((m) => (
                      <div key={m.id} className="row">
                        <span className="row-main">
                          <span>
                            <strong>{who(m)}</strong> · {m.team_name}
                          </span>
                          <span className="muted small">
                            {m.email}
                            {m.note ? ` · "${m.note}"` : ""} · {formatWhen(m.requested_at)}
                          </span>
                        </span>
                        <span className="actions">
                          <button className="primary" onClick={() => decide(m, "approve")}>Approve</button>
                          <button onClick={() => decide(m, "decline")}>Decline</button>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section>
                <h2>Invite links</h2>
                <p className="muted small">
                  One per team. Anyone with a team's link can ask to join it; nobody sees anything until you approve them.
                </p>
                {s.invites.map((inv) => {
                  const link = `${location.origin}/join/${inv.code}`;
                  return (
                    <div key={inv.team} className="card invite">
                      <strong className="invite-team">{inv.team_name}</strong>
                      <code>{link}</code>
                      <span className="actions">
                        <button
                          onClick={() => {
                            void navigator.clipboard.writeText(link).then(() => setCopied(inv.team));
                          }}
                        >
                          {copied === inv.team ? "Copied" : "Copy"}
                        </button>
                        <button
                          onClick={() => {
                            if (
                              confirm(
                                `Make a new ${inv.team_name} link? The current one stops working; people already approved keep access.`
                              )
                            ) {
                              setCopied(null);
                              void act("/api/admin/invite", { team: inv.team });
                            }
                          }}
                        >
                          New link
                        </button>
                      </span>
                    </div>
                  );
                })}
              </section>

              <section>
                <h2>Who has access</h2>
                {byTeam.size === 0 && <p className="muted">Nobody yet.</p>}
                {[...byTeam].map(([team, rows]) => (
                  <div key={team} className="tournament">
                    <h3>{team}</h3>
                    <div className="list">
                      {rows.map((m) => (
                        <div key={m.id} className="row">
                          <span className="row-main">
                            <span>
                              <strong>{who(m)}</strong> · {m.email}
                            </span>
                            {m.note && <span className="muted small">{m.note}</span>}
                          </span>
                          <button onClick={() => decide(m, "remove")}>Remove</button>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </section>

              {s.declined.length > 0 && (
                <section>
                  <h2>Declined</h2>
                  <div className="list">
                    {s.declined.map((m) => (
                      <div key={m.id} className="row">
                        <span className="row-main">
                          <span>
                            {who(m)} · {m.team_name}
                          </span>
                          <span className="muted small">
                            {m.email}
                            {m.note ? ` · "${m.note}"` : ""}
                          </span>
                        </span>
                        <span className="actions">
                          <button onClick={() => decide(m, "approve")}>Approve</button>
                          <button onClick={() => decide(m, "remove")}>Forget</button>
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          );
        }}
      </Gate>
    </>
  );
}
