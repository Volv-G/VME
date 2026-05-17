import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { TeamSummary } from "../types/api";
import { displayName } from "../util/names";

export function TeamSelectPage() {
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  async function load() {
    try {
      setTeams(await api.listTeams());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => { void load(); }, []);

  async function createTeam() {
    if (!newName.trim()) return;
    try {
      await api.createTeam(newName.trim());
      setNewName("");
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="page">
      <div className="card">
        <div className="card-header">
          <h2>Select a team</h2>
        </div>
        {error && <div className="error">{error}</div>}
        {teams.length === 0 && !error && (
          <p className="muted">No teams yet. Create one below.</p>
        )}
        <div className="list">
          {teams.map((t) => (
            <Link key={t.name} to={`/teams/${encodeURIComponent(t.name)}`} className="list-row">
              <div>
                <div>{displayName(t.name)}</div>
                <div className="row-meta">
                  {t.tournament_count} tournament{t.tournament_count === 1 ? "" : "s"}
                  {t.has_roster ? "" : " - no roster yet"}
                </div>
              </div>
              <span className="muted">→</span>
            </Link>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><h2>Create team</h2></div>
        <div className="toolbar">
          <input
            value={newName}
            placeholder="e.g. Northwood Jrs 15 Grey"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createTeam()}
            style={{ flex: 1, minWidth: 280 }}
          />
          <button className="primary" onClick={createTeam}>Create</button>
        </div>
      </div>
    </div>
  );
}
