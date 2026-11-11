import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { SeasonReelFileDto, SeasonReelsDto } from "../types/api";
import { formatBytes } from "../util/bytes";
import { RenderPlayerModal } from "./RenderPlayerModal";

/**
 * Season reels: one per player, every match of theirs in order, each
 * introduced by a title card. Rendering and uploading to OneDrive are
 * separate actions, as for a match's reels - render, check it, then send.
 *
 * Polled quickly only while something is queued or being written, since
 * each listing re-reads every match of the season.
 */
const POLL_BUSY_MS = 5000;
const POLL_IDLE_MS = 30000;

function formatAge(unixSeconds: number): string {
  const diffSec = Date.now() / 1000 - unixSeconds;
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return new Date(unixSeconds * 1000).toLocaleString();
}

export function TeamSeasonReelsPanel({ team }: { team: string }) {
  const [data, setData] = useState<SeasonReelsDto | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [playing, setPlaying] = useState<SeasonReelFileDto | null>(null);

  const reload = useCallback(async () => {
    try {
      setData(await api.listSeasonReels(team));
    } catch (e) {
      setErr(String(e));
    }
  }, [team]);

  const live =
    !!data &&
    (data.players.some((p) => p.job_state) ||
      data.files.some((f) => f.rendering || f.upload_state));

  useEffect(() => {
    void reload();
    const t = window.setInterval(() => void reload(), live ? POLL_BUSY_MS : POLL_IDLE_MS);
    return () => clearInterval(t);
  }, [reload, live]);

  async function act(key: string, fn: () => Promise<unknown>, done: string) {
    setErr(null);
    setNote(null);
    setBusy(key);
    try {
      await fn();
      setNote(done);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
      await reload();
    }
  }

  function remove(f: SeasonReelFileDto) {
    const up = f.onedrive_url
      ? "\n\nIt is in OneDrive; that copy is not touched."
      : "";
    if (!confirm(`Delete ${f.label}'s season reel (${formatBytes(f.size_bytes)})?${up}`)) return;
    void act(f.filename, () => api.deleteSeasonReel(team, f.filename), `Deleted ${f.label}'s season reel.`);
  }

  const players = (data?.players ?? []).filter((p) => p.plays > 0 || p.job_state);

  return (
    <div className="card">
      <div className="card-header">
        <h2>Season reels</h2>
        <span className="row-meta">
          Every match of a player's, each opened by a title card. Rendering
          and uploading to OneDrive are separate steps.
        </span>
      </div>

      {err && <div className="error" style={{ marginBottom: 8 }}>{err}</div>}
      {note && (
        <div className="info" style={{ marginBottom: 8, display: "flex", gap: 8 }}>
          <span style={{ flex: 1 }}>{note}</span>
          <button onClick={() => setNote(null)} style={{ padding: "0 6px", fontSize: 11 }} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}
      {!!data?.no_footage.length && (
        <p className="muted" style={{ marginTop: 0 }}>
          Left out, footage deleted: {data.no_footage.join(", ")}.
        </p>
      )}

      {!data ? (
        !err && <p className="muted" style={{ margin: 0 }}>Counting plays…</p>
      ) : players.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>No player has any tagged plays yet.</p>
      ) : (
        <div className="stats-scroll storage-scroll">
          <table className="stats-table">
            <thead>
              <tr>
                <th className="stats-name">Player</th>
                <th>Plays</th>
                <th>Matches</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {players.map((p) => {
                const key = `render:${p.number}`;
                return (
                  <tr key={p.number}>
                    <td className="stats-name">{p.label}</td>
                    <td>{p.plays || ""}</td>
                    <td>{p.matches || ""}</td>
                    <td className="storage-actions">
                      {p.job_state ? (
                        <span className="rendering-badge">
                          {p.job_state === "rendering" ? "● rendering" : "queued"}
                        </span>
                      ) : (
                        <button
                          onClick={() =>
                            act(
                              key,
                              () => api.renderSeasonReel(team, p.number),
                              `${p.label}: season reel queued. It renders when the Render lane runs.`
                            )
                          }
                          disabled={busy !== null || p.plays === 0}
                          title={`Render ${p.label}'s ${p.plays} plays from ${p.matches} matches into one reel`}
                        >
                          {busy === key ? "…" : "🎬 Render season reel"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!!data?.files.length && (
        <div className="list render-list" style={{ marginTop: 12 }}>
          {data.files.map((f) => {
            const leaf = f.filename.split("/").pop() || f.filename;
            const disabled = busy !== null || f.rendering;
            return (
              <div
                key={f.filename}
                className={`list-row render-row${f.rendering ? " is-rendering" : ""}`}
                style={{ alignItems: "center" }}
              >
                {f.has_thumbnail && (
                  <img
                    src={api.seasonReelThumbnailUrl(team, f.filename)}
                    alt=""
                    width={64}
                    height={36}
                    style={{ borderRadius: 3, border: "1px solid var(--border)", objectFit: "cover", flex: "0 0 auto" }}
                  />
                )}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 13 }}>{f.label}</strong>
                    {f.matches > 0 && <span className="row-meta">{f.matches} matches</span>}
                    {f.rendering && <span className="rendering-badge">● rendering</span>}
                    {f.upload_state && (
                      <span className="rendering-badge">↑ {f.upload_state}</span>
                    )}
                  </div>
                  <div className="row-meta" style={{ marginTop: 2 }}>
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault();
                        if (!f.rendering) setPlaying(f);
                      }}
                      title={`Play ${leaf}`}
                    >
                      {leaf}
                    </a>{" "}
                    · {formatBytes(f.size_bytes)} · {formatAge(f.created_at)}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}>
                  {f.onedrive_url && (
                    <a href={f.onedrive_url} target="_blank" rel="noreferrer" style={{ color: "#3aa55d" }}>
                      ▶ in OneDrive
                    </a>
                  )}
                  <a href={api.seasonReelUrl(team, f.filename)} download title="Download">
                    <button style={{ padding: "2px 8px" }} disabled={f.rendering}>⬇</button>
                  </a>
                  <button
                    onClick={() => remove(f)}
                    disabled={disabled}
                    title="Delete this season reel from disk"
                    style={{ padding: "2px 8px" }}
                  >
                    {busy === f.filename ? "…" : "🗑"}
                  </button>
                  <button
                    onClick={() =>
                      act(
                        `up:${f.filename}`,
                        () => api.uploadSeasonReel(team, f.filename),
                        `${f.label}: upload queued. It runs when the OneDrive lane runs.`
                      )
                    }
                    disabled={disabled || !!f.upload_state}
                    title={
                      f.onedrive_url
                        ? "Upload to OneDrive again (replaces the copy there)"
                        : "Upload this season reel to OneDrive"
                    }
                    style={{ padding: "2px 8px" }}
                  >
                    {busy === `up:${f.filename}` ? "…" : "📤"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {playing && (
        <RenderPlayerModal
          open
          onClose={() => setPlaying(null)}
          title={`${playing.label}: season reel`}
          subtitle={`${playing.matches} matches · ${formatBytes(playing.size_bytes)}`}
          src={api.seasonReelUrl(team, playing.filename, true)}
          downloadUrl={api.seasonReelUrl(team, playing.filename)}
        />
      )}
    </div>
  );
}
