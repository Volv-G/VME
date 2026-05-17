import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { RenderFileDto, RenderJobDto } from "../types/api";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatAge(unixSeconds: number): string {
  const dt = new Date(unixSeconds * 1000);
  const diffSec = (Date.now() - dt.getTime()) / 1000;
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  // Locale-aware date for anything older than a day.
  return dt.toLocaleString();
}

interface Props {
  team: string;
  tournament: string;
  date: string;
  match: string;
  hasClips: boolean;
  /** Current source/global frame. Used as the preview window center. */
  currentFrame: number;
  /** Match FPS - used to display preview window length in seconds. */
  fps: number;
}

const DEFAULT_PREVIEW_SECONDS = 30;

export function RenderPanel({ team, tournament, date, match, hasClips, currentFrame, fps }: Props) {
  const [job, setJob] = useState<RenderJobDto | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [previewSeconds, setPreviewSeconds] = useState<number>(DEFAULT_PREVIEW_SECONDS);
  const [renders, setRenders] = useState<RenderFileDto[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => () => { esRef.current?.close(); }, []);

  // Load the existing renders list whenever the match changes or after a
  // job finishes successfully. Failures are surfaced to the user.
  const reloadRenders = useCallback(async () => {
    try {
      setRenders(await api.listRenders(team, tournament, date, match));
    } catch (e) {
      setErr(String(e));
    }
  }, [team, tournament, date, match]);

  useEffect(() => {
    void reloadRenders();
  }, [reloadRenders]);

  useEffect(() => {
    if (job?.status === "done") void reloadRenders();
  }, [job?.status, reloadRenders]);

  async function removeRender(filename: string) {
    if (!confirm(`Delete ${filename}?`)) return;
    try {
      await api.deleteRender(team, tournament, date, match, filename);
      await reloadRenders();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function start(label: string, opts: { playheadFrame?: number; secondsAround?: number } = {}) {
    setErr(null);
    try {
      const j = await api.startRender(team, tournament, date, match, { label, ...opts });
      setJob(j);
      esRef.current?.close();
      const es = new EventSource(api.jobEventsUrl(j.id));
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as RenderJobDto;
          setJob(data);
          if (data.status === "done" || data.status === "failed" || data.status === "cancelled") {
            es.close();
          }
        } catch {}
      };
      es.onerror = () => { /* keepalives produce errors that browsers retry */ };
      esRef.current = es;
    } catch (e) {
      setErr(String(e));
    }
  }

  async function cancel() {
    if (!job) return;
    try {
      const updated = await api.cancelRender(job.id);
      setJob(updated);
    } catch (e) {
      setErr(String(e));
    }
  }

  const running = job?.status === "running" || job?.status === "pending";
  const cancelRequested = !!job?.cancel_requested;
  const windowSec = previewSeconds * 2;
  const windowFrames = Math.round(previewSeconds * (fps || 30) * 2);
  const playheadTime = (currentFrame / (fps || 30)).toFixed(1);

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div className="card-header">
        <h2>Render</h2>
      </div>
      {!hasClips && <p className="muted">Upload clips first.</p>}

      <div className="field-row" style={{ alignItems: "flex-end", marginBottom: 8 }}>
        <label style={{ flex: "0 0 140px" }}>
          Preview ± seconds
          <input
            type="number"
            min={1}
            max={600}
            value={previewSeconds}
            onChange={(e) =>
              setPreviewSeconds(Math.max(1, parseInt(e.target.value || "1", 10) || 1))
            }
          />
        </label>
        <div className="muted" style={{ paddingBottom: 6, fontSize: 11 }}>
          window: {windowSec}s ({windowFrames} frames) around playhead ({playheadTime}s)
        </div>
      </div>

      <div className="toolbar">
        <button
          className="primary"
          disabled={!hasClips || running}
          onClick={() =>
            start("preview", {
              playheadFrame: currentFrame,
              secondsAround: previewSeconds,
            })
          }
          title={`Render ${windowSec}s around the current playhead`}
        >
          Render preview
        </button>
        <button disabled={!hasClips || running} onClick={() => start("full")}>
          Render full
        </button>
        {running && (
          <button
            className="danger"
            onClick={cancel}
            disabled={cancelRequested}
            title="Stop rendering and discard the partial output"
          >
            {cancelRequested ? "Cancelling..." : "Cancel"}
          </button>
        )}
      </div>

      {err && <div className="error" style={{ marginTop: 8 }}>{err}</div>}
      {job && (
        <div style={{ marginTop: 12 }}>
          <div className="row-meta">
            Job {job.id} - {job.status} - {job.phase}
          </div>
          <div style={{
            background: "var(--bg-elev-2)", border: "1px solid var(--border)",
            borderRadius: 6, height: 14, marginTop: 6, overflow: "hidden",
          }}>
            <div style={{
              width: `${Math.min(100, job.percent)}%`,
              height: "100%",
              background: job.status === "failed" ? "var(--danger)" : "var(--accent)",
              transition: "width 0.2s ease",
            }} />
          </div>
          <div className="row-meta" style={{ marginTop: 4 }}>
            {job.percent.toFixed(1)}% - {job.message}
          </div>
          {job.status === "done" && job.output_filename && (
            <div style={{ marginTop: 8 }}>
              <a href={api.downloadUrl(team, tournament, date, match, job.output_filename)} download>
                Download {job.output_filename}
              </a>
            </div>
          )}
          {job.status === "failed" && job.error && (
            <div className="error" style={{ marginTop: 8 }}>{job.error}</div>
          )}
        </div>
      )}

      {/* Existing renders on disk. Always shown (with an empty-state line)
          so the user knows where to find prior outputs. */}
      <div style={{ marginTop: 16 }}>
        <div className="row-meta" style={{ marginBottom: 6 }}>
          Saved renders ({renders.length})
        </div>
        {renders.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>No renders yet.</p>
        ) : (
          <div className="list">
            {renders.map((r) => (
              <div
                key={r.filename}
                className="list-row"
                style={{ alignItems: "center" }}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={r.filename}
                  >
                    <a
                      href={api.downloadUrl(team, tournament, date, match, r.filename)}
                      download
                    >
                      {r.filename}
                    </a>
                  </div>
                  <div className="row-meta">
                    {formatBytes(r.size_bytes)} · {formatAge(r.created_at)}
                  </div>
                </div>
                <button
                  className="danger"
                  onClick={() => removeRender(r.filename)}
                  title="Delete this render file"
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
