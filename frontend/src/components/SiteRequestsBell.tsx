import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { SiteRequestDto } from "../types/api";
import { Modal } from "./Modal";

const POLL_MS = 60_000;

function who(r: SiteRequestDto) {
  return r.role === "coach" ? "Coach" : r.player_name;
}

/**
 * People asking on the public site to become a team's coach or one of
 * its players. The backend fetches them once a minute (site_sync.py);
 * approving here answers on the site within seconds. Hidden when the
 * site isn't configured or nobody is waiting.
 */
export function SiteRequestsBell() {
  const [configured, setConfigured] = useState<string | null>(null);
  const [requests, setRequests] = useState<SiteRequestDto[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    void api
      .siteRequests()
      .then((r) => setRequests(r.requests))
      .catch(() => {});
  }, []);

  useEffect(() => {
    void api
      .siteStatus()
      .then((s) => setConfigured(s.configured ? s.url : null))
      .catch(() => setConfigured(null));
  }, []);

  useEffect(() => {
    if (!configured) return;
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, [configured, load]);

  if (!configured || (requests.length === 0 && !open)) return null;

  async function decide(r: SiteRequestDto, action: "approve" | "decline") {
    setBusy(r.id);
    setError(null);
    try {
      const out = await api.decideSiteRequest(r.id, action);
      setRequests(out.requests);
      if (out.requests.length === 0) setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <button className="site-bell" onClick={() => setOpen(true)} title="Access requests on the public site">
        🔔 <span className="site-bell-count">{requests.length}</span>
      </button>
      <Modal open={open} title="Access requests" onClose={() => setOpen(false)}>
        {requests.length === 0 ? (
          <p className="muted">Nothing waiting.</p>
        ) : (
          <div className="site-requests">
            {requests.map((r) => (
              <div key={r.id} className="site-request">
                <div className="site-request-main">
                  <div>
                    <strong>{who(r)}</strong> · {r.team_name}
                  </div>
                  <div className="muted site-small">
                    {r.email}
                    {r.note ? ` · "${r.note}"` : ""} · {new Date(r.requested_at).toLocaleString()}
                  </div>
                </div>
                <button className="primary" disabled={busy !== null} onClick={() => void decide(r, "approve")}>
                  Approve
                </button>
                <button disabled={busy !== null} onClick={() => void decide(r, "decline")}>
                  Decline
                </button>
              </div>
            ))}
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <p className="muted site-small">
          Approved people see the team's videos and stats on{" "}
          <a href={configured} target="_blank" rel="noreferrer">
            the site
          </a>
          ; a parent also sees their player's reels and can edit the player's name and photo. Manage everyone on the
          site's{" "}
          <a href={`${configured}/admin`} target="_blank" rel="noreferrer">
            Admin page
          </a>
          .
        </p>
      </Modal>
    </>
  );
}
