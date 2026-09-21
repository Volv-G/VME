import { Link, Navigate, useParams } from "react-router-dom";
import { UploadConnections } from "../components/UploadConnections";

/**
 * Settings that belong to this VME server rather than to a team.
 *
 * Upload connections were briefly on the team settings page, next to
 * the rest of the upload configuration. That was wrong and the panel
 * said so out loud — one Google token authorises one channel and one
 * Microsoft token one drive, for everything this server does. A
 * component that has to explain why it is where it is belongs
 * somewhere else.
 *
 * Structured with sections from the start, even though there is one:
 * the next server-wide thing (storage paths, the render queue's
 * limits, auth) has an obvious home rather than being wedged into a
 * team.
 */
const SECTIONS = [{ id: "connections", label: "Connections" }] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

export function ServerSettingsPage() {
  const params = useParams();
  const section = (params.section ?? "connections") as SectionId;

  if (!SECTIONS.some((s) => s.id === section)) {
    return <Navigate to="/settings" replace />;
  }

  return (
    <div>
      <div className="page-head">
        <h1>Server settings</h1>
        <Link to="/">← All teams</Link>
      </div>

      {SECTIONS.length > 1 && (
        <nav className="settings-tabs">
          {SECTIONS.map((s) => (
            <Link
              key={s.id}
              to={s.id === "connections" ? "/settings" : `/settings/${s.id}`}
              className={`settings-tab${s.id === section ? " active" : ""}`}
            >
              {s.label}
            </Link>
          ))}
        </nav>
      )}

      <div className="settings-body">
        {section === "connections" && <UploadConnections />}
      </div>
    </div>
  );
}
