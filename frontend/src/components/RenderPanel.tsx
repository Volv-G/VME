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

// How often to poll the team-wide jobs list while this panel is open.
// 2s is responsive enough for a status indicator without flooding the
// backend - the per-job SSE handles the smooth progress bar.
const JOBS_POLL_MS = 2000;

export function RenderPanel({
  team,
  tournament,
  date,
  match,
  hasClips,
  currentFrame,
  fps,
}: Props) {
  const [err, setErr] = useState<string | null>(null);
  const [previewSeconds, setPreviewSeconds] = useState<number>(
    DEFAULT_PREVIEW_SECONDS
  );
  const [renders, setRenders] = useState<RenderFileDto[]>([]);
  // Jobs for THIS match only. Filtered client-side from the team list so
  // we share one polling loop with the queue widget on other pages.
  const [matchJobs, setMatchJobs] = useState<RenderJobDto[]>([]);
  // Per-job SSE subscriptions for live progress. Keyed by job.id.
  const esMap = useRef<Map<string, EventSource>>(new Map());

  // ---- Renders folder ---------------------------------------------------

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

  // ---- Per-match jobs ---------------------------------------------------

  const reloadJobs = useCallback(async () => {
    try {
      const all = await api.listTeamJobs(team);
      const mine = all.filter(
        (j) => j.tournament === tournament && j.date === date && j.match === match
      );
      setMatchJobs(mine);
      return mine;
    } catch (e) {
      setErr(String(e));
      return [];
    }
  }, [team, tournament, date, match]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      if (cancelled) return;
      const jobs = await reloadJobs();
      // If any job just transitioned to `done`, refresh the renders list
      // so the new file shows up in "Saved renders".
      if (jobs.some((j) => j.status === "done")) {
        void reloadRenders();
      }
      timer = window.setTimeout(tick, JOBS_POLL_MS);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [reloadJobs, reloadRenders]);

  // Subscribe to SSE for every running job so the progress bar is smooth
  // (the 2s poll alone would feel choppy). Closes when the job leaves
  // the running state or the panel unmounts.
  useEffect(() => {
    const map = esMap.current;
    const running = new Set(
      matchJobs.filter((j) => j.status === "running").map((j) => j.id)
    );
    // Open streams for running jobs we're not yet subscribed to.
    for (const id of running) {
      if (map.has(id)) continue;
      const es = new EventSource(api.jobEventsUrl(id));
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as RenderJobDto;
          setMatchJobs((prev) =>
            prev.map((j) => (j.id === data.id ? { ...j, ...data } : j))
          );
          if (
            data.status === "done" ||
            data.status === "failed" ||
            data.status === "cancelled"
          ) {
            es.close();
            map.delete(id);
          }
        } catch {
          /* ignore */
        }
      };
      es.onerror = () => {
        /* keepalive disconnects are browser-retried */
      };
      map.set(id, es);
    }
    // Close streams for jobs that are no longer running.
    for (const [id, es] of map.entries()) {
      if (!running.has(id)) {
        es.close();
        map.delete(id);
      }
    }
  }, [matchJobs]);

  useEffect(
    () => () => {
      // Close any remaining streams on unmount.
      for (const es of esMap.current.values()) es.close();
      esMap.current.clear();
    },
    []
  );

  // ---- Actions ----------------------------------------------------------

  async function enqueue(
    label: string,
    opts: {
      kind?: "full" | "preview" | "highlights" | "focused_highlights";
      playheadFrame?: number;
      secondsAround?: number;
      immediate?: boolean;
    } = {}
  ) {
    setErr(null);
    try {
      await api.enqueueRender(team, tournament, date, match, {
        label,
        ...opts,
      });
      await reloadJobs();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function cancelJob(id: string) {
    try {
      await api.cancelRender(id);
      await reloadJobs();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function deleteJob(id: string) {
    try {
      await api.deleteJob(id);
      await reloadJobs();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function removeRender(filename: string) {
    if (!confirm(`Delete ${filename}?`)) return;
    try {
      await api.deleteRender(team, tournament, date, match, filename);
      await reloadRenders();
    } catch (e) {
      setErr(String(e));
    }
  }

  const windowSec = previewSeconds * 2;
  const windowFrames = Math.round(previewSeconds * (fps || 30) * 2);
  const playheadTime = (currentFrame / (fps || 30)).toFixed(1);

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div className="card-header">
        <h2>Render</h2>
      </div>
      {!hasClips && <p className="muted">Upload clips first.</p>}

      <div
        className="field-row"
        style={{ alignItems: "flex-end", marginBottom: 8 }}
      >
        <label style={{ flex: "0 0 140px" }}>
          Preview ± seconds
          <input
            type="number"
            min={1}
            max={600}
            value={previewSeconds}
            onChange={(e) =>
              setPreviewSeconds(
                Math.max(1, parseInt(e.target.value || "1", 10) || 1)
              )
            }
          />
        </label>
        <div className="muted" style={{ paddingBottom: 6, fontSize: 11 }}>
          window: {windowSec}s ({windowFrames} frames) around playhead (
          {playheadTime}s)
        </div>
      </div>

      <div className="toolbar" style={{ flexWrap: "wrap" }}>
        <button
          className="primary"
          disabled={!hasClips}
          onClick={() =>
            enqueue("preview", {
              kind: "preview",
              playheadFrame: currentFrame,
              secondsAround: previewSeconds,
              immediate: true,
            })
          }
          title={`Render a ${windowSec}s preview around the current playhead right now`}
        >
          Render preview
        </button>
        <button
          disabled={!hasClips}
          onClick={() => enqueue("full", { kind: "full" })}
          title="Add a full-match render to the team queue"
        >
          Enqueue full
        </button>
        <button
          disabled={!hasClips}
          onClick={() => enqueue("highlights", { kind: "highlights" })}
          title="Enqueue one rally clip per Highlight event, organized by player"
        >
          Enqueue highlights
        </button>
        <button
          disabled={!hasClips}
          onClick={() =>
            enqueue("focused", { kind: "focused_highlights" })
          }
          title="Enqueue one clip per FocusIn/FocusOut span, organized by player"
        >
          Enqueue focused
        </button>
      </div>
      <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>
        Previews run immediately. Other renders are queued - start the queue
        from the team dashboard when ready.
      </p>

      {err && (
        <div className="error" style={{ marginTop: 8 }}>
          {err}
        </div>
      )}

      {/* Jobs for this match: pending / running / recently terminated. */}
      {matchJobs.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="row-meta" style={{ marginBottom: 6 }}>
            Jobs ({matchJobs.length})
          </div>
          <div className="list">
            {matchJobs.map((j) => (
              <JobRow
                key={j.id}
                job={j}
                onCancel={() => cancelJob(j.id)}
                onDelete={() => deleteJob(j.id)}
                downloadUrl={
                  j.output_filename
                    ? api.downloadUrl(
                        team,
                        tournament,
                        date,
                        match,
                        j.output_filename
                      )
                    : undefined
                }
              />
            ))}
          </div>
        </div>
      )}

      {/* Existing rendered files on disk, grouped by directory so the
          batch outputs under highlights/<team>/<player>/ stay readable. */}
      <div style={{ marginTop: 16 }}>
        <div className="row-meta" style={{ marginBottom: 6 }}>
          Saved renders ({renders.length})
        </div>
        {renders.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>
            No renders yet.
          </p>
        ) : (
          <RendersByDir
            renders={renders}
            team={team}
            tournament={tournament}
            date={date}
            match={match}
            onDelete={removeRender}
          />
        )}
      </div>
    </div>
  );
}

// ---- Renders grouped by directory --------------------------------------

interface RendersByDirProps {
  renders: RenderFileDto[];
  team: string;
  tournament: string;
  date: string;
  match: string;
  onDelete: (filename: string) => void;
}

function RendersByDir({
  renders,
  team,
  tournament,
  date,
  match,
  onDelete,
}: RendersByDirProps) {
  // Group by parent directory. Top-level files (no slash) collect under
  // the empty-string key and render without a group heading so the
  // common case (just a few full / preview files) stays uncluttered.
  const groups = new Map<string, RenderFileDto[]>();
  for (const r of renders) {
    const i = r.filename.lastIndexOf("/");
    const dir = i === -1 ? "" : r.filename.slice(0, i);
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir)!.push(r);
  }
  // Stable order: top-level first, then nested groups alphabetically.
  const dirs = Array.from(groups.keys()).sort((a, b) => {
    if (a === "") return -1;
    if (b === "") return 1;
    return a.localeCompare(b);
  });
  return (
    <div className="list">
      {dirs.map((dir) => (
        <div key={dir || "_root"}>
          {dir && (
            <div
              className="row-meta"
              style={{
                padding: "6px 4px 2px",
                fontFamily: "monospace",
                letterSpacing: 0.2,
              }}
              title={dir}
            >
              {dir}/
            </div>
          )}
          {groups.get(dir)!.map((r) => {
            const leaf = r.filename.split("/").pop() || r.filename;
            return (
              <div
                key={r.filename}
                className="list-row"
                style={{ alignItems: "center", paddingLeft: dir ? 16 : 8 }}
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
                      href={api.downloadUrl(
                        team,
                        tournament,
                        date,
                        match,
                        r.filename
                      )}
                      download
                    >
                      {leaf}
                    </a>
                  </div>
                  <div className="row-meta">
                    {formatBytes(r.size_bytes)} · {formatAge(r.created_at)}
                  </div>
                </div>
                <button
                  className="danger"
                  onClick={() => onDelete(r.filename)}
                  title="Delete this render file"
                >
                  Delete
                </button>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ---- Job row (shared shape; queue widget can reuse if desired) -----------

interface JobRowProps {
  job: RenderJobDto;
  onCancel: () => void;
  onDelete: () => void;
  downloadUrl?: string;
}

export function JobRow({ job, onCancel, onDelete, downloadUrl }: JobRowProps) {
  const live = job.status === "pending" || job.status === "running";
  return (
    <div className="list-row" style={{ alignItems: "center" }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <StatusPill status={job.status} />
          <strong style={{ fontSize: 12 }}>{job.label || "render"}</strong>
          {job.playhead_frame != null && (
            <span className="muted" style={{ fontSize: 11 }}>
              preview @ frame {job.playhead_frame}
            </span>
          )}
        </div>
        {job.status === "running" && (
          <div
            style={{
              background: "var(--bg-elev-2)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              height: 6,
              marginTop: 4,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${Math.min(100, job.percent)}%`,
                height: "100%",
                background: "var(--accent)",
                transition: "width 0.2s ease",
              }}
            />
          </div>
        )}
        <div className="row-meta" style={{ marginTop: 2 }}>
          {job.status === "running"
            ? `${job.percent.toFixed(1)}% — ${job.message || job.phase}`
            : job.status === "failed"
              ? job.error || "failed"
              : job.status === "done" && downloadUrl
                ? (
                    <a href={downloadUrl} download>
                      Download {job.output_filename}
                    </a>
                  )
                : job.phase}
        </div>
      </div>
      {live ? (
        <button
          className="danger"
          onClick={onCancel}
          disabled={job.cancel_requested}
          title="Cancel this job"
        >
          {job.cancel_requested ? "Cancelling…" : "Cancel"}
        </button>
      ) : (
        <button
          className="danger"
          onClick={onDelete}
          title="Remove this job from history"
        >
          Delete
        </button>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: RenderJobDto["status"] }) {
  const colors: Record<RenderJobDto["status"], string> = {
    pending: "var(--text-dim)",
    running: "var(--accent)",
    done: "#3aa55d",
    failed: "var(--danger)",
    cancelled: "var(--text-dim)",
  };
  return (
    <span
      style={{
        fontSize: 10,
        textTransform: "uppercase",
        letterSpacing: 0.5,
        padding: "1px 6px",
        borderRadius: 4,
        border: `1px solid ${colors[status]}`,
        color: colors[status],
        whiteSpace: "nowrap",
      }}
    >
      {status}
    </span>
  );
}
