import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { RosterDto, TournamentSummary } from "../types/api";
import { RosterEditor } from "../components/RosterEditor";
import { Modal } from "../components/Modal";
import { displayName } from "../util/names";

export function TeamDashboardPage() {
  const { team = "" } = useParams();
  const [tournaments, setTournaments] = useState<TournamentSummary[]>([]);
  const [roster, setRoster] = useState<RosterDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  const load = useCallback(async () => {
    try {
      setError(null);
      const [t, r] = await Promise.all([api.listTournaments(team), api.getRoster(team)]);
      setTournaments(t);
      setRoster(r);
    } catch (e) {
      setError(String(e));
    }
  }, [team]);

  useEffect(() => { void load(); }, [load]);

  async function saveRoster(next: RosterDto) {
    try {
      const saved = await api.putRoster(team, next);
      setRoster(saved);
    } catch (e) {
      setError(String(e));
    }
  }

  async function createTournament() {
    if (!newName.trim()) return;
    try {
      await api.createTournament(team, newName.trim());
      setNewName("");
      setCreating(false);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="page">
      <div className="card">
        <div className="card-header">
          <h2>{displayName(team)}</h2>
          <Link to="/" className="muted">← All teams</Link>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Tournaments</h2>
          <button className="primary" onClick={() => setCreating(true)}>+ New tournament</button>
        </div>

        {tournaments.length === 0 && (
          <p className="muted">No tournaments yet.</p>
        )}

        <div className="list">
          {tournaments.map((t) => (
            <Link
              key={t.name}
              to={`/teams/${encodeURIComponent(team)}/tournaments/${encodeURIComponent(t.name)}`}
              className="list-row"
            >
              <div>
                <div>{displayName(t.name)}</div>
                <div className="row-meta">
                  {t.match_count} match{t.match_count === 1 ? "" : "es"}
                </div>
              </div>
              <span className="muted">→</span>
            </Link>
          ))}
        </div>
      </div>

      {roster && <RosterEditor value={roster} onSave={saveRoster} defaultName={displayName(team)} />}

      <Modal
        open={creating}
        title="New tournament"
        onClose={() => setCreating(false)}
        width="min(480px, 92vw)"
      >
        <div className="field-row">
          <label>
            Tournament name
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && createTournament()}
              autoFocus
              placeholder="e.g. PSR 2026"
            />
          </label>
        </div>
        <div className="toolbar" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button onClick={() => setCreating(false)}>Cancel</button>
          <button className="primary" onClick={createTournament}>Create</button>
        </div>
      </Modal>
    </div>
  );
}
