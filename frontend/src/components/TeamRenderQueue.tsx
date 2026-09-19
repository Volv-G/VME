import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { QueueLane, QueueStateDto, RenderJobDto } from "../types/api";
import { JobRow } from "./RenderPanel";
import { displayName } from "../util/names";

interface Props {
  team: string;
}

// Poll cadence: same as the match-panel polling so the two views stay in
// sync without per-job SSE here.
const POLL_MS = 2000;

// One control per lane. Rendering and uploading are independent workers
// (see `app/jobs/dispatcher.py`), so they get independent buttons: an
// upload parked for hours on a YouTube quota reset must not be a reason
// to leave rendering stopped, and stopping uploads while YouTube refuses
// them must not stop rendering.
const LANES: { lane: QueueLane; label: string; hint: string }[] = [
  {
    lane: "render",
    label: "Rendering",
    hint: "Encodes run one at a time - a render owns the GPU and the disk.",
  },
  {
    lane: "upload",
    label: "Uploads",
    hint:
      "YouTube uploads. These can park for hours waiting on a quota " +
      "reset or the channel upload cap; they retry on their own as long " +
      "as this lane is running.",
  },
];

/**
 * Team-scoped job queue with per-lane start/stop control.
 *
 * The queue itself is global (one worker per lane, shared across all
 * teams), but this widget filters jobs by the current team so the
 * dashboard stays focused on the user's current context. Start/Stop
 * affect the global workers regardless - we still surface them here
 * because this is the place the user lives between editing sessions.
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

  async function toggle(lane: QueueLane, active: boolean) {
    try {
      if (active) await api.stopQueue(lane);
      else await api.startQueue(lane);
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

  return (
    <div className="card">
      <div className="card-header">
        <h2>Job queue</h2>
        <div
          style={{
            display: "flex",
            gap: 14,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          {LANES.map(({ lane, label, hint }) => {
            const s = state?.lanes?.[lane];
            const active = s?.active ?? false;
            const pending = s?.pending ?? 0;
            const running = s?.running ?? 0;
            return (
              <div
                key={lane}
                style={{ display: "flex", gap: 6, alignItems: "center" }}
                title={hint}
              >
                <span className="muted" style={{ fontSize: 12 }}>
                  {label}: {running} running · {pending} pending
                </span>
                <button
                  className={active ? "danger" : "primary"}
                  onClick={() => toggle(lane, active)}
                  disabled={!active && pending === 0 && running === 0}
                  title={
                    active
                      ? `Pause ${label.toLowerCase()}. The running job (if any) finishes; nothing else starts. The other lane keeps going.`
                      : `Run pending ${label.toLowerCase()} jobs FIFO. The other lane is unaffected.`
                  }
                >
                  {active ? `Stop ${label.toLowerCase()}` : `Start ${label.toLowerCase()}`}
                </button>
              </div>
            );
          })}
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
                {j.match_index ? `Match ${j.match_index} – ` : ""}
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
