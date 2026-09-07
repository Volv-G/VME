import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { QueueStateDto, RenderJobDto } from "../types/api";
import { JobRow } from "./RenderPanel";
import { displayName } from "../util/names";

interface Props {
  team: string;
}

// Poll cadence: same as the match-panel polling so the two views stay in
// sync without per-job SSE here.
const POLL_MS = 2000;

/**
 * Team-scoped render queue with start/stop control.
 *
 * The queue itself is global (one worker shared across all teams), but
 * this widget filters jobs by the current team so the dashboard stays
 * focused on the user's current context. Start/Stop affect the global
 * worker regardless - we still surface them here because this is the
 * place the user lives between editing sessions.
 */
export function TeamRenderQueue({ team }: Props) {
  const [jobs, setJobs] = useState<RenderJobDto[]>([]);
  const [state, setState] = useState<QueueStateDto | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Single in-flight reload to avoid stampedes when the user clicks
  // start/stop while a poll is mid-flight.
  const inFlight = useRef(false);

  const reload = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const [s, j] = await Promise.all([
        api.getQueueState(),
        api.listTeamJobs(team),
      ]);
      setState(s);
      setJobs(j);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      inFlight.current = false;
    }
  }, [team]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      if (cancelled) return;
      await reload();
      timer = window.setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [reload]);

  async function toggle() {
    try {
      if (state?.active) await api.stopQueue();
      else await api.startQueue();
      await reload();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function cancelJob(id: string) {
    try {
      await api.cancelRender(id);
      await reload();
    } catch (e) {
      setErr(String(e));
    }
  }

  async function deleteJob(id: string) {
    try {
      await api.deleteJob(id);
      await reload();
    } catch (e) {
      setErr(String(e));
    }
  }

  const active = state?.active ?? false;
  const pending = state?.pending ?? 0;
  const running = state?.running ?? 0;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Render queue</h2>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="muted" style={{ fontSize: 12 }}>
            {running} running · {pending} pending
          </span>
          <button
            className={active ? "danger" : "primary"}
            onClick={toggle}
            disabled={!active && pending === 0 && running === 0}
            title={
              active
                ? "Pause the dispatcher. The running job (if any) finishes; nothing else starts."
                : "Resume the dispatcher and run pending jobs FIFO."
            }
          >
            {active ? "Stop queue" : "Start queue"}
          </button>
        </div>
      </div>

      {err && <div className="error">{err}</div>}

      {jobs.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          No jobs queued. Use the Render panel inside a match to add one.
        </p>
      ) : (
        <div className="list">
          {jobs.map((j) => (
            <div key={j.id}>
              <div className="row-meta" style={{ marginBottom: 2 }}>
                {j.match_index != null ? `Match ${j.match_index} – ` : ""}
                vs {j.opponent || "TBD"}
                {" · "}
                {displayName(j.tournament)} · {j.date}
              </div>
              <JobRow
                job={j}
                onCancel={() => cancelJob(j.id)}
                onDelete={() => deleteJob(j.id)}
                downloadUrl={
                  j.output_filename
                    ? api.downloadUrl(
                        j.team,
                        j.tournament,
                        j.date,
                        j.match,
                        j.output_filename
                      )
                    : undefined
                }
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
