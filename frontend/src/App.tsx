import { Link, Route, Routes } from "react-router-dom";
import { TeamSelectPage } from "./routes/TeamSelectPage";
import { TeamDashboardPage } from "./routes/TeamDashboardPage";
import { MatchEditorPage } from "./routes/MatchEditorPage";

export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Match Editor</h1>
        <nav>
          <Link to="/">Teams</Link>
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<TeamSelectPage />} />
          <Route path="/teams/:team" element={<TeamDashboardPage />} />
          <Route path="/teams/:team/matches/:match" element={<MatchEditorPage />} />
        </Routes>
      </main>
    </div>
  );
}
