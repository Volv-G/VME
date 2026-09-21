import { Link, Route, Routes } from "react-router-dom";
import { TeamSelectPage } from "./routes/TeamSelectPage";
import { TeamDashboardPage } from "./routes/TeamDashboardPage";
import { TeamSettingsPage } from "./routes/TeamSettingsPage";
import { ServerSettingsPage } from "./routes/ServerSettingsPage";
import { TournamentDashboardPage } from "./routes/TournamentDashboardPage";
import { MatchEditorPage } from "./routes/MatchEditorPage";

export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Match Editor</h1>
        <nav>
          <Link to="/">Teams</Link>
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
