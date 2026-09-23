import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { RosterDto } from "../types/api";
import { RosterEditor } from "../components/RosterEditor";
import { TeamYouTubeSettings } from "../components/TeamYouTubeSettings";
import { TeamOneDriveSettings } from "../components/TeamOneDriveSettings";
import { TeamNamingSettings } from "../components/TeamNamingSettings";
import { TeamMediaServerSettings } from "../components/TeamMediaServerSettings";

/**
 * Team settings, one subject per subpage.
 *
 * These used to be a single scrolling stack on the dashboard, which was
 * fine while every upload was a YouTube upload. It stopped being fine
 * once there were two engines: a playlist and a privacy status are
 * YouTube's vocabulary, a folder path and a share link are OneDrive's,
 * and showing both at once asks the reader to work out which half
 * applies to them.
 *
 * The section lives in the URL so a link can point at one.
 *
 * Upload *connections* are deliberately not here: one token authorises
 * one account for the whole server, so they live under Server
 * settings. What is here is what genuinely differs per team — which
 * engine, which playlist, which folder.
 */
const SECTIONS = [
  { id: "uploads", label: "Uploads" },
  { id: "youtube", label: "YouTube" },
  { id: "onedrive", label: "OneDrive" },
  { id: "naming", label: "File naming" },
  { id: "media-server", label: "Media server" },
  { id: "roster", label: "Roster" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

function displayName(slug: string): string {
  return slug.replace(/_/g, " ");
}

export function TeamSettingsPage() {
  const params = useParams();
  const team = params.team ?? "";
  const section = (params.section ?? "uploads") as SectionId;

  const [roster, setRoster] = useState<RosterDto | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRoster(await api.getRoster(team));
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [team]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveRoster(next: RosterDto) {
    setRoster(await api.putRoster(team, next));
  }

  if (!SECTIONS.some((s) => s.id === section)) {
    return <Navigate to={`/teams/${encodeURIComponent(team)}/settings`} replace />;
  }

  const base = `/teams/${encodeURIComponent(team)}/settings`;

  return (
    <div className="page page-narrow">
      <div className="page-head">
        <h1>{displayName(team)} — settings</h1>
        <Link to={`/teams/${encodeURIComponent(team)}`}>← Back to team</Link>
      </div>

      {err && <div className="error">{err}</div>}

      <nav className="settings-tabs">
        {SECTIONS.map((s) => (
          <Link
            key={s.id}
            to={s.id === "uploads" ? base : `${base}/${s.id}`}
            className={`settings-tab${s.id === section ? " active" : ""}`}
          >
            {s.label}
          </Link>
        ))}
      </nav>

      <div className="settings-body">
        {section === "uploads" && (
          <TeamOneDriveSettings
            team={team}
            roster={roster}
            onSaved={setRoster}
            show="destinations"
          />
        )}
        {section === "youtube" && (
          <TeamYouTubeSettings team={team} roster={roster} onSaved={setRoster} />
        )}
        {section === "onedrive" && (
          <TeamOneDriveSettings
            team={team}
            roster={roster}
            onSaved={setRoster}
            show="onedrive"
          />
        )}
        {section === "naming" && (
          <TeamNamingSettings team={team} roster={roster} onSaved={setRoster} />
        )}
        {section === "media-server" && (
          <TeamMediaServerSettings team={team} roster={roster} onSaved={setRoster} />
        )}
        {section === "roster" && roster && (
          <RosterEditor
            value={roster}
            onSave={saveRoster}
            defaultName={displayName(team)}
            team={team}
          />
        )}
      </div>
    </div>
  );
}
