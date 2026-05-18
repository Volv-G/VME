import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { RosterDto, TournamentSummary } from "../types/api";
import { RosterEditor } from "../components/RosterEditor";
import { Modal } from "../components/Modal";
import { TeamRenderQueue } from "../components/TeamRenderQueue";
import { TeamYouTubeSettings } from "../components/TeamYouTubeSettings";
import { TeamNamingSettings } from "../components/TeamNamingSettings";
import { TeamFullRendersPanel } from "../components/TeamFullRendersPanel";
import { displayName } from "../util/names";

export function TeamDashboardPage() {
  const { team = "" } = useParams();
  const [tournaments, setTournaments] = useState<TournamentSummary[]>([]);
  const [roster, setRoster] = useState<RosterDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // New-tournament form. `name` is the folder slug (URL identifier);
  // `abbreviation` and `full_name` are optional display strings written
  // to the tournament.json sidecar - they back the {tournament_abbr}
  // and {tournament_full} template variables used by YouTube uploads.
  const [newName, setNewName] = useState("");
  const [newAbbrev, setNewAbbrev] = useState("");
  const [newFullName, setNewFullName] = useState("");

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
    const name = newName.trim();
    if (!name) return;
    try {
      const created = await api.createTournament(team, name);
      // Persist the display-name sidecar if either optional field was
      // filled in. We hit this endpoint AFTER createTournament so the
      // folder is guaranteed to exist (putTournamentInfo 404s otherwise).
      // Empty strings -> null so the backend stores "unset" rather than
      // an empty string masquerading as a real value.
      const abbr = newAbbrev.trim();
      const full = newFullName.trim();
      if (abbr || full) {
        try {
          await api.putTournamentInfo(team, created.name, {
            abbreviation: abbr || null,
            full_name: full || null,
          });
        } catch (e) {
          // Sidecar failure is non-fatal; the tournament folder is
          // created and the user can fill the fields on its dashboard.
          setError(
            `Tournament created, but failed to save display names: ${e}`
          );
        }
      }
      setNewName("");
      setNewAbbrev("");
      setNewFullName("");
      setCreating(false);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  function closeCreate() {
    // Reset on close so a cancelled create doesn't pre-fill the next
    // attempt with leftover text.
    setNewName("");
    setNewAbbrev("");
    setNewFullName("");
    setCreating(false);
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

      <TeamRenderQueue team={team} />

      <TeamFullRendersPanel team={team} />

      <TeamYouTubeSettings team={team} roster={roster} onSaved={setRoster} />

      <TeamNamingSettings team={team} roster={roster} onSaved={setRoster} />

      {roster && <RosterEditor value={roster} onSave={saveRoster} defaultName={displayName(team)} />}

      <Modal
        open={creating}
        title="New tournament"
        onClose={closeCreate}
        width="min(520px, 92vw)"
      >
        {/* Name = folder slug (URL identifier). The other two fields
            are optional display strings (sidecar in tournament.json)
            used by the YouTube upload templates. Pressing Enter in any
            field submits the form. */}
        <label>
          Tournament name
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createTournament()}
            autoFocus
            placeholder="e.g. PSR_2026"
          />
        </label>
        <label style={{ marginTop: 8 }}>
          Abbreviation <span className="muted">(optional)</span>
          <input
            value={newAbbrev}
            onChange={(e) => setNewAbbrev(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createTournament()}
            placeholder={newName ? newName.replace(/_/g, " ") : "e.g. PSR 26"}
            title="Short name used in YouTube titles (and {tournament_abbr} template variable). Falls back to the folder name with underscores replaced by spaces."
          />
        </label>
        <label style={{ marginTop: 8 }}>
          Full name <span className="muted">(optional)</span>
          <input
            value={newFullName}
            onChange={(e) => setNewFullName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && createTournament()}
            placeholder={newAbbrev || (newName ? newName.replace(/_/g, " ") : "e.g. PSR Spring 2026")}
            title="Long name used in YouTube descriptions (and {tournament_full} template variable). Falls back to the abbreviation when empty."
          />
        </label>
        <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
          Both display names can be edited later from the tournament
          page; leave blank to fall back to the folder name.
        </p>
        <div className="toolbar" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button onClick={closeCreate}>Cancel</button>
          <button className="primary" onClick={createTournament}>Create</button>
        </div>
      </Modal>
    </div>
  );
}
