import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { RenderJobDto } from "../types/api";

interface Props {
  team: string;
  match: string;
  hasClips: boolean;
  /** Current source/global frame. Used as the preview window center. */
  currentFrame: number;
  /** Match FPS - used to display preview window length in seconds. */
  fps: number;
}

const DEFAULT_PREVIEW_SECONDS = 30;

export function RenderPanel({ team, match, hasClips, currentFrame, fps }: Props) {
  const [job, setJob] = useState<RenderJobDto | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [previewSeconds, setPreviewSeconds] = useState<number>(DEFAULT_PREVIEW_SECONDS);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => () => { esRef.current?.close(); }, []);

  async function start(label: string, opts: { playheadFrame?: number; secondsAround?: number } = {}) {
    setErr(null);
    try {
      const j = await api.startRender(team, match, { label, ...opts });
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
              <a href={api.downloadUrl(team, match, job.output_filename)} download>
                Download {job.output_filename}
              </a>
            </div>
          )}
          {job.status === "failed" && job.error && (
            <div className="error" style={{ marginTop: 8 }}>{job.error}</div>
          )}
        </div>
      )}
    </div>
  );
}
