import { Link, Route, Routes, useLocation } from "react-router-dom";
import { TeamSelectPage } from "./routes/TeamSelectPage";
import { TeamDashboardPage } from "./routes/TeamDashboardPage";
import { TeamSettingsPage } from "./routes/TeamSettingsPage";
import { ServerSettingsPage } from "./routes/ServerSettingsPage";
import { TournamentDashboardPage } from "./routes/TournamentDashboardPage";
import { MatchEditorPage } from "./routes/MatchEditorPage";
import { displayName } from "./util/names";

export function App() {
  // Anywhere inside a team - its tournaments, a match, its settings - the
  // team itself is one click away, not a detour through the team list.
  const { pathname } = useLocation();
  const teamSlug = /^\/teams\/([^/]+)/.exec(pathname)?.[1];
  const team = teamSlug ? decodeURIComponent(teamSlug) : null;
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Match Editor</h1>
        <nav>
          <Link to="/">Teams</Link>
          {team && <Link to={`/teams/${teamSlug}`}>{displayName(team)}</Link>}
          <Link to="/settings">Settings</Link>
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<TeamSelectPage />} />
          <Route path="/settings" element={<ServerSettingsPage />} />
          <Route path="/settings/:section" element={<ServerSettingsPage />} />
          <Route path="/teams/:team" element={<TeamDashboardPage />} />
          <Route path="/teams/:team/settings" element={<TeamSettingsPage />} />
          <Route
            path="/teams/:team/settings/:section"
            element={<TeamSettingsPage />}
          />
          <Route
            path="/teams/:team/tournaments/:tournament"
            element={<TournamentDashboardPage />}
          />
          <Route
            path="/teams/:team/tournaments/:tournament/dates/:date/matches/:match"
            element={<MatchEditorPage />}
          />
        </Routes>
      </main>
    </div>
  );
}
