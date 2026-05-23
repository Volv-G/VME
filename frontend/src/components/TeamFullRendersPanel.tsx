import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { FullRenderDto, YouTubeStatusDto } from "../types/api";
import { displayName } from "../util/names";

/**
 * Team-wide "Full renders" list, with per-row YouTube upload action.
 *
 * Renders are produced by the per-match Render panel; this view
 * aggregates them across the whole team so the user has one place to
 * push completed matches up to YouTube. A green link replaces the
 * upload button after a successful upload (the backend records this
 * via a per-render `.youtube.json` sidecar).
 *
 * The list polls every 5s so newly-finished renders and uploads
 * (driven by the global render queue) appear without a manual refresh.
 */
interface Props {
  team: string;
}

const POLL_MS = 5000;

function formatBytes(n: number): string {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatAge(unixSeconds: number): string {
  const diffSec = Date.now() / 1000 - unixSeconds;
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return new Date(unixSeconds * 1000).toLocaleString();
}

export function TeamFullRendersPanel({ team }: Props) {
  const [renders, setRenders] = useState<FullRenderDto[]>([]);
  const [status, setStatus] = useState<YouTubeStatusDto | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setRenders(await api.listTeamFullRenders(team));
    } catch (e) {
      setErr(String(e));
    }
  }, [team]);

  useEffect(() => {
    void reload();
    void api.youtubeStatus().then(setStatus).catch(() => setStatus(null));
    const t = window.setInterval(reload, POLL_MS);
    return () => clearInterval(t);
  }, [reload]);

  async function upload(r: FullRenderDto) {
    if (!status?.configured) return;
    const id = `${r.tournament}/${r.date}/${r.match}/${r.filename}`;
    setErr(null);
    setBusyId(id);
    try {
      await api.enqueueYouTubeUpload(team, r.tournament, r.date, r.match, {
        filename: r.filename,
      });
      // Poll a moment later; the upload runs through the queue so it
      // doesn't move `renders` immediately - but a fresh fetch will at
      // least surface the job on the queue widget.
      window.setTimeout(reload, 500);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusyId(null);
    }
  }

  // Build a per-row download URL the same way the per-match panel does:
  // segment-encoded so nested filenames survive FastAPI's path param.
  function downloadUrl(r: FullRenderDto): string {
    const base =
      `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api/teams/` +
      `${encodeURIComponent(team)}/tournaments/${encodeURIComponent(r.tournament)}` +
      `/dates/${encodeURIComponent(r.date)}/matches/${encodeURIComponent(r.match)}/renders/`;
    return base + encodeURIComponent(r.filename);
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2>Full renders ({renders.length})</h2>
        {status && (
          <span
            className="row-meta"
            title={status.reason}
            style={{
              color: status.configured ? "#3aa55d" : "var(--text-dim)",
            }}
          >
            YouTube: {status.configured ? "ready" : "not configured"}
          </span>
        )}
      </div>

      {err && <div className="error" style={{ marginBottom: 8 }}>{err}</div>}

      {renders.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          No full-match renders yet. Run a render from a match page.
        </p>
      ) : (
        <div className="list">
          {renders.map((r) => {
            const id = `${r.tournament}/${r.date}/${r.match}/${r.filename}`;
            const uploaded = !!r.youtube_video_id;
            const ytUrl = uploaded
              ? `https://youtu.be/${r.youtube_video_id}`
              : null;
            // Title line: "<date>  M<n>. <opponent>  (<tournament>)".
            // Uses displayName so underscored slugs render with spaces.
            const matchLabel = r.match_index
              ? `M${r.match_index}. ${displayName(r.opponent || r.match)}`
              : displayName(r.opponent || r.match);
            return (
              <div
                key={id}
                className="list-row"
                style={{ alignItems: "center" }}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      alignItems: "baseline",
                      flexWrap: "wrap",
                    }}
                  >
                    <strong style={{ fontSize: 13 }}>{r.date}</strong>
                    <span>{matchLabel}</span>
                    <span className="muted" style={{ fontSize: 11 }}>
                      {displayName(r.tournament)}
                    </span>
                  </div>
                  <div className="row-meta" style={{ marginTop: 2 }}>
                    <a href={downloadUrl(r)} download>
                      {r.filename}
                    </a>{" "}
                    · {formatBytes(r.size_bytes)} · {formatAge(r.created_at)}
                  </div>
                </div>
                {uploaded ? (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "flex-end",
                      gap: 2,
                      whiteSpace: "nowrap",
                    }}
                  >
                    <a
                      href={ytUrl!}
                      target="_blank"
                      rel="noreferrer"
                      title={
                        r.youtube_uploaded_at
                          ? `Uploaded ${formatAge(r.youtube_uploaded_at)}`
                          : "Uploaded"
                      }
                      style={{ color: "#3aa55d" }}
                    >
                      ▶ on YouTube
                    </a>
                    {/* Show the privacy status as a small badge. When
                        YouTube downgraded the upload (requested !=
                        actual) the badge turns warning-colored and the
                        tooltip explains the OAuth-Testing-mode cause -
                        without this users discover hours later that
                        their "public" upload is actually private. */}
                    {r.youtube_privacy_status && (() => {
                      const downgraded =
                        !!r.youtube_requested_privacy_status &&
                        r.youtube_requested_privacy_status !==
                          r.youtube_privacy_status;
                      return (
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 6px",
                            borderRadius: 3,
                            border: "1px solid",
                            borderColor: downgraded
                              ? "var(--warn, #d29922)"
                              : "var(--border)",
                            color: downgraded
                              ? "var(--warn, #d29922)"
                              : "var(--text-dim)",
                            background: downgraded
                              ? "rgba(210, 153, 34, 0.10)"
                              : "transparent",
                          }}
                          title={
                            downgraded
                              ? `YouTube forced privacy=${r.youtube_privacy_status} ` +
                                `(you requested ${r.youtube_requested_privacy_status}). ` +
                                `Usually means the OAuth client is in Google Cloud Console's ` +
                                `Testing mode - switch to Production to allow public/unlisted uploads.`
                              : `Privacy: ${r.youtube_privacy_status}`
                          }
                        >
                          {downgraded ? "⚠ " : ""}
                          {r.youtube_privacy_status}
                        </span>
                      );
                    })()}
                  </div>
                ) : (
                  <button
                    onClick={() => upload(r)}
                    disabled={!status?.configured || busyId === id}
                    title={
                      status?.configured
                        ? "Enqueue an upload of this render to YouTube"
                        : status?.reason ||
                          "YouTube uploads are not configured for this server"
                    }
                  >
                    {busyId === id ? "Enqueueing…" : "Upload to YouTube"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
