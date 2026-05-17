import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { MatchSummary, RosterDto } from "../types/api";
import { RosterEditor } from "../components/RosterEditor";

export function TeamDashboardPage() {
  const { team = "" } = useParams();
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [roster, setRoster] = useState<RosterDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newMatch, setNewMatch] = useState({ name: "", opponent: "", date: "" });

  const load = useCallback(async () => {
    try {
      setError(null);
      const [m, r] = await Promise.all([api.listMatches(team), api.getRoster(team)]);
      setMatches(m);
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

  async function createMatch() {
    if (!newMatch.name.trim()) return;
    try {
      await api.createMatch(team, {
        name: newMatch.name.trim(),
        opponent: newMatch.opponent.trim(),
        date: newMatch.date.trim(),
      });
      setNewMatch({ name: "", opponent: "", date: "" });
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
          <h2>{team}</h2>
          <Link to="/" className="muted">← All teams</Link>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      {roster && <RosterEditor value={roster} onSave={saveRoster} defaultName={team} />}

      <div className="card">
        <div className="card-header">
          <h2>Matches</h2>
          <button className="primary" onClick={() => setCreating(true)}>+ New match</button>
        </div>

        {matches.length === 0 && (
          <p className="muted">No matches yet.</p>
        )}

        <div className="list">
          {matches.map((m) => (
            <Link
              key={m.name}
              to={`/teams/${encodeURIComponent(team)}/matches/${encodeURIComponent(m.name)}`}
              className="list-row"
            >
              <div>
                <div>{m.name}</div>
                <div className="row-meta">
                  {m.date || "no date"} - vs {m.opponent || "TBD"} -{" "}
                  {m.has_video ? `${m.clip_count} clip${m.clip_count === 1 ? "" : "s"}` : "no video uploaded"}
                </div>
              </div>
              <span className="muted">→</span>
            </Link>
          ))}
        </div>
      </div>

      {creating && (
        <div className="modal-backdrop" onClick={() => setCreating(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>New match</h3>
            <div className="field-row">
              <label>
                Folder name
                <input value={newMatch.name} onChange={(e) => setNewMatch({ ...newMatch, name: e.target.value })} />
              </label>
            </div>
            <div className="field-row">
              <label>
                Opponent
                <input value={newMatch.opponent} onChange={(e) => setNewMatch({ ...newMatch, opponent: e.target.value })} />
              </label>
              <label>
                Date
                <input type="date" value={newMatch.date} onChange={(e) => setNewMatch({ ...newMatch, date: e.target.value })} />
              </label>
            </div>
            <div className="toolbar" style={{ marginTop: 16, justifyContent: "flex-end" }}>
              <button onClick={() => setCreating(false)}>Cancel</button>
              <button className="primary" onClick={createMatch}>Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
