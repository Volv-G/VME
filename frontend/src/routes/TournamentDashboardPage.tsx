import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { MatchSummary } from "../types/api";
import { Modal } from "../components/Modal";
import { displayName } from "../util/names";

export function TournamentDashboardPage() {
  const { team = "", tournament = "" } = useParams();
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // `match_index` is a string in form state so the user can clear the field
  // while editing; we coerce to number on submit.
  const [newMatch, setNewMatch] = useState({
    opponent: "",
    date: "",
    match_index: "" as string,
  });
  // True after the user has manually edited the match-# field; we stop
  // auto-suggesting once they take over.
  const [matchIndexTouched, setMatchIndexTouched] = useState(false);

  /** Next available match number for a given date, based on already-loaded matches. */
  const nextIndexFor = useCallback(
    (date: string): number => {
      const used = matches
        .filter((m) => m.date === date && m.match_index != null)
        .map((m) => m.match_index as number);
      return used.length === 0 ? 1 : Math.max(...used) + 1;
    },
    [matches]
  );

  // Auto-suggest match # when the date changes (unless the user has typed
  // their own value).
  const suggestedIndex = useMemo(
    () => (newMatch.date ? nextIndexFor(newMatch.date) : 1),
    [newMatch.date, nextIndexFor]
  );
  useEffect(() => {
    if (!creating || matchIndexTouched) return;
    setNewMatch((prev) => ({ ...prev, match_index: String(suggestedIndex) }));
  }, [creating, matchIndexTouched, suggestedIndex]);

  const load = useCallback(async () => {
    try {
      setError(null);
      setMatches(await api.listMatches(team, tournament));
    } catch (e) {
      setError(String(e));
    }
  }, [team, tournament]);

  useEffect(() => { void load(); }, [load]);

  function openCreate() {
    setNewMatch({ opponent: "", date: "", match_index: "" });
    setMatchIndexTouched(false);
    setCreating(true);
  }

  async function createMatch() {
    if (!newMatch.opponent.trim() || !newMatch.date.trim()) return;
    const parsedIdx = parseInt(newMatch.match_index, 10);
    const idx = Number.isFinite(parsedIdx) && parsedIdx > 0 ? parsedIdx : null;
    try {
      await api.createMatch(team, tournament, {
        opponent: newMatch.opponent.trim(),
        date: newMatch.date.trim(),
        match_index: idx,
      });
      setNewMatch({ opponent: "", date: "", match_index: "" });
      setMatchIndexTouched(false);
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
          <div>
            <h2 style={{ margin: 0 }}>{displayName(tournament)}</h2>
            <div className="row-meta">{displayName(team)}</div>
          </div>
          <Link to={`/teams/${encodeURIComponent(team)}`} className="muted">
            ← {displayName(team)}
          </Link>
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Matches</h2>
          <button className="primary" onClick={openCreate}>+ New match</button>
        </div>

        {matches.length === 0 && (
          <p className="muted">No matches yet.</p>
        )}

        <div className="list">
          {matches.map((m) => (
            <Link
              key={`${m.date}/${m.name}`}
              to={
                `/teams/${encodeURIComponent(team)}` +
                `/tournaments/${encodeURIComponent(tournament)}` +
                `/dates/${encodeURIComponent(m.date)}` +
                `/matches/${encodeURIComponent(m.name)}`
              }
              className="list-row"
            >
              <div>
                <div>
                  {m.match_index != null ? `Match ${m.match_index} – ` : ""}
                  vs {m.opponent}
                </div>
                <div className="row-meta">
                  {m.date}
                  {" – "}
                  {m.has_video
                    ? `${m.clip_count} clip${m.clip_count === 1 ? "" : "s"}`
                    : "no video uploaded"}
                </div>
              </div>
              <span className="muted">→</span>
            </Link>
          ))}
        </div>
      </div>

      <Modal
        open={creating}
        title="New match"
        onClose={() => setCreating(false)}
        width="min(520px, 92vw)"
      >
        <div className="field-row">
          <label style={{ flex: 1 }}>
            Opponent
            <input
              value={newMatch.opponent}
              onChange={(e) => setNewMatch({ ...newMatch, opponent: e.target.value })}
              autoFocus
            />
          </label>
        </div>
        <div className="field-row">
          <label style={{ flex: 1 }}>
            Date
            <input
              type="date"
              value={newMatch.date}
              onChange={(e) => setNewMatch({ ...newMatch, date: e.target.value })}
            />
          </label>
          <label style={{ flex: "0 0 110px" }}>
            Match #
            <input
              type="number"
              min={1}
              value={newMatch.match_index}
              onChange={(e) => {
                setMatchIndexTouched(true);
                setNewMatch({ ...newMatch, match_index: e.target.value });
              }}
              title="Order of this match on the chosen date"
            />
          </label>
        </div>
        <div className="toolbar" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button onClick={() => setCreating(false)}>Cancel</button>
          <button className="primary" onClick={createMatch}>Create</button>
        </div>
      </Modal>
    </div>
  );
}
