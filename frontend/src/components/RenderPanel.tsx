import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { displayName } from "../util/names";
import { RenderPlayerModal } from "./RenderPlayerModal";
import type {
  RenderFileDto,
  RenderJobDto,
  YouTubeStatusDto,
} from "../types/api";

// How long the "queued" confirmation stays up before fading itself out.
// Long enough to read a two-line message, short enough not to linger
// over the panel while the user queues several renders in a row.
const QUEUED_NOTICE_MS = 9000;

/** Is `filename` a top-level full render (not a highlight / focused
 *  subfolder output, not a preview)? Matches `_is_full_render` on the
 *  backend so the upload action only appears where the server will
 *  accept it. */
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"];

/** Render kinds that go through the queue (previews run immediately and
 *  have their own button, so they're not in here). */
type QueueKind =
  | "full"
  | "condensed"
  | "highlights"
  | "focused_highlights"
  | "player_reels";

/** The queue-able kinds, in the order they're offered.
 *
 *  `label` is short enough for a dropdown; `blurb` is shown under the
 *  selector, because what each kind produces is not guessable from its
 *  name and a tooltip is invisible until it's too late. */
const QUEUE_KINDS: {
  kind: QueueKind;
  label: string;
  /** Job label, which becomes the `{label}` segment of the output
   *  filename. Kept as the historical short forms so filenames don't
   *  change shape just because the UI moved to a dropdown. */
  jobLabel: string;
  blurb: string;
}[] = [
  {
    kind: "full",
    label: "Full match",
    jobLabel: "full",
    blurb: "One video of the whole match, scoreboard and popups included.",
  },
  {
    kind: "condensed",
    label: "Condensed match",
    jobLabel: "condensed",
    blurb:
      "Only the plays: each rally from just before the serve to just " +
      "after the point, cut together with fades between sets. Typically " +
      "just over half the length of the full match.",
  },
  {
    kind: "highlights",
    label: "Highlights (one clip per play)",
    jobLabel: "highlights",
    blurb:
      "One rally clip per tagged action, filed by player and action. " +
      "Dozens of files - they stay on this page, not the team dashboard.",
  },
  {
    kind: "focused_highlights",
    label: "Focused spans (one clip per focus)",
    jobLabel: "focused",
    blurb: "One clip per FocusIn/FocusOut span, filed by player.",
  },
  {
    kind: "player_reels",
    label: "Player reels (one video per player)",
    jobLabel: "reels",
    blurb:
      "One video per player with all of their plays and a chapter list, " +
      "ready to upload. Separate from highlights - both can be run.",
  },
];

function isFullRender(filename: string): boolean {
  if (filename.includes("/") || filename.includes("\\")) return false;
  const lower = filename.toLowerCase();
  // The render container is configurable on the backend (.mov for the
  // DNxHR default, .mp4 for legacy renders), so accept any video ext.
  if (!VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext))) return false;
  const stem = lower.replace(/\.[^.]+$/, "");
  if (lower.startsWith("preview_") || stem === "preview") return false;
  return true;
}

/** Is `filename` a player reel (`reels/<team>/<player>/...`)?
 *  Reels are uploadable even though they live in a subfolder - one video
 *  per player is exactly what they're for. Mirrors the backend check in
 *  `api/renders.py::enqueue_youtube_upload`. */
function isPlayerReel(filename: string): boolean {
  const parts = filename.split(/[/\\]/).filter(Boolean);
  if (parts.length < 2 || parts[0] !== "reels") return false;
  const leaf = parts[parts.length - 1].toLowerCase();
  return VIDEO_EXTENSIONS.some((ext) => leaf.endsWith(ext));
}

/** Everything the YouTube upload endpoint accepts: the full match render
 *  or a player reel. Per-play highlight/focused clips are excluded on
 *  purpose (dozens of uploads per match). */
function isUploadable(filename: string): boolean {
  return isFullRender(filename) || isPlayerReel(filename);
}

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
  // Which queued render to enqueue. A dropdown rather than one button
  // per kind: five buttons already wrapped onto a second row, and each
  // needed a tooltip nobody reads to say what it produces. One choice
  // plus a visible description of it says more in less space.
  const [queueKind, setQueueKind] = useState<QueueKind>("full");
  // YouTube readiness check. Fetched once on mount; controls whether
  // the per-render "Upload" button is enabled.
  const [ytStatus, setYtStatus] = useState<YouTubeStatusDto | null>(null);
  // Map of <filename> -> youtube video id. Populated by polling the
  // team's full-renders list (which carries sidecar info) so the
  // upload button on this match page can switch to "on YouTube" after
  // the queue finishes an upload.
  const [uploads, setUploads] = useState<Record<string, string>>({});
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  // Transient "job queued" confirmation. `queueActive` decides whether
  // it explains how to START the queue or just says work is under way.
  const [notice, setNotice] = useState<{ queueActive: boolean } | null>(null);
  const noticeTimer = useRef<number | null>(null);
  // Jobs for THIS match only. Filtered client-side from the team list so
  // we share one polling loop with the queue widget on other pages.
  const [matchJobs, setMatchJobs] = useState<RenderJobDto[]>([]);
  // Per-job SSE subscriptions for live progress. Keyed by job.id.
  const esMap = useRef<Map<string, EventSource>>(new Map());
  // Render open in the player dialog. Just the relative filename -
  // everything else (stream URL, download URL) derives from it.
  const [playing, setPlaying] = useState<string | null>(null);

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

  // YouTube status (cheap; one-shot).
  useEffect(() => {
    void api.youtubeStatus().then(setYtStatus).catch(() => setYtStatus(null));
  }, []);

  // Pull the team's full-render listing and project the entries
  // belonging to THIS match into a {filename -> video_id} map. We
  // could expose this on the per-match endpoint instead, but reusing
  // the team-wide endpoint keeps the team dashboard and the match
  // page in agreement about which renders are "uploaded".
  const reloadUploads = useCallback(async () => {
    try {
      const all = await api.listTeamFullRenders(team);
      const mine: Record<string, string> = {};
      for (const r of all) {
        if (
          r.tournament === tournament &&
          r.date === date &&
          r.match === match &&
          r.youtube_video_id
        ) {
          mine[r.filename] = r.youtube_video_id;
        }
      }
      setUploads(mine);
    } catch {
      /* non-fatal - the upload button will just stay enabled */
    }
  }, [team, tournament, date, match]);

  useEffect(() => {
    void reloadUploads();
  }, [reloadUploads]);

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

  // Clear the pending notice timer on unmount so a closing modal can't
  // set state on an unmounted component.
  useEffect(
    () => () => {
      if (noticeTimer.current !== null) {
        window.clearTimeout(noticeTimer.current);
      }
    },
    []
  );

  // ---- Actions ----------------------------------------------------------

  async function enqueue(
    label: string,
    opts: {
      kind?: QueueKind | "preview";
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
      // Immediate jobs (previews) start on their own; everything else
      // waits for the dispatcher, which is easy to miss - a queued job
      // just sits at "pending" with no explanation. Say so, and say
      // where to start it. Queue state is fetched per enqueue (cheap)
      // rather than polled, so the message reflects reality at that
      // moment instead of guessing.
      if (!opts.immediate) {
        let active = false;
        try {
          active = (await api.getQueueState()).active;
        } catch {
          /* non-fatal: fall back to the "how to start it" wording */
        }
        showQueuedNotice(active);
      }
    } catch (e) {
      setErr(String(e));
    }
  }

  function showQueuedNotice(queueActive: boolean) {
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
    setNotice({ queueActive });
    noticeTimer.current = window.setTimeout(() => {
      noticeTimer.current = null;
      setNotice(null);
    }, QUEUED_NOTICE_MS);
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
      await reloadUploads();
    } catch (e) {
      setErr(String(e));
    }
  }

  /** Clear a stale upload record (video deleted on YouTube) so the row
   *  offers "Upload" again. The server verifies the video is really gone
   *  and 409s otherwise; we relay that and offer to force. */
  async function forgetUpload(filename: string) {
    setErr(null);
    setUploadingName(filename);
    try {
      await api.forgetYouTubeUpload(team, tournament, date, match, filename);
      await reloadUploads();
    } catch (e) {
      const msg = String(e);
      if (
        msg.includes("still exists") &&
        confirm(
          `${msg}

Clear the record anyway? The video on YouTube is not ` +
            `touched - you'd end up with a duplicate if you re-upload.`
        )
      ) {
        try {
          await api.forgetYouTubeUpload(
            team,
            tournament,
            date,
            match,
            filename,
            true
          );
          await reloadUploads();
        } catch (e2) {
          setErr(String(e2));
        }
      } else {
        setErr(msg);
      }
    } finally {
      setUploadingName(null);
    }
  }

  async function uploadToYouTube(filename: string) {
    if (!ytStatus?.configured) return;
    setErr(null);
    setUploadingName(filename);
    try {
      await api.enqueueYouTubeUpload(team, tournament, date, match, {
        filename,
      });
      await reloadJobs();
    } catch (e) {
      setErr(String(e));
    } finally {
      setUploadingName(null);
    }
  }

  // When a job (incl. uploads) finishes, refresh the uploads map so
  // the row switches to "on YouTube".
  useEffect(() => {
    if (matchJobs.some((j) => j.status === "done" && j.kind === "youtube_upload")) {
      void reloadUploads();
    }
  }, [matchJobs, reloadUploads]);

  const windowSec = previewSeconds * 2;
  const windowFrames = Math.round(previewSeconds * (fps || 30) * 2);
  const playheadTime = (currentFrame / (fps || 30)).toFixed(1);

  return (
    <>
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

      <div className="toolbar">
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
        <span className="muted" style={{ fontSize: 11 }}>
          runs immediately
        </span>
      </div>

      <div
        className="toolbar"
        style={{ marginTop: 10, alignItems: "center", gap: 8 }}
      >
        <select
          value={queueKind}
          onChange={(e) => setQueueKind(e.target.value as QueueKind)}
          disabled={!hasClips}
          style={{ flex: 1, minWidth: 0 }}
          aria-label="Render kind"
        >
          {QUEUE_KINDS.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>
        <button
          disabled={!hasClips}
          onClick={() =>
            enqueue(
              QUEUE_KINDS.find((k) => k.kind === queueKind)?.jobLabel ??
                queueKind,
              { kind: queueKind }
            )
          }
          title="Add this render to the team queue"
        >
          Enqueue
        </button>
      </div>
      <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>
        {QUEUE_KINDS.find((k) => k.kind === queueKind)?.blurb} Queued renders
        start when the queue is running - start it from the team dashboard.
      </p>

      {notice && (
        <div
          role="status"
          style={{
            marginTop: 8,
            padding: "8px 10px",
            borderRadius: 6,
            fontSize: 12,
            lineHeight: 1.5,
            border: "1px solid",
            borderColor: notice.queueActive
              ? "var(--border)"
              : "var(--warn, #d29922)",
            background: notice.queueActive
              ? "var(--bg-elev-2)"
              : "rgba(210, 153, 34, 0.10)",
            display: "flex",
            gap: 8,
            alignItems: "baseline",
          }}
        >
          <span style={{ flex: 1 }}>
            {notice.queueActive ? (
              <>Render queued - the queue is running, it will start shortly.</>
            ) : (
              <>
                Render queued, but <strong>the queue is stopped</strong>. Start
                it from the{" "}
                <Link to={`/teams/${encodeURIComponent(team)}`}>
                  {displayName(team)} home page
                </Link>
                .
              </>
            )}
          </span>
          <button
            onClick={() => setNotice(null)}
            title="Dismiss"
            style={{ padding: "0 6px", fontSize: 11 }}
          >
            ×
          </button>
        </div>
      )}

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
                onPlay={
                  j.output_filename
                    ? () => setPlaying(j.output_filename!)
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
            onUpload={uploadToYouTube}
            onForgetUpload={forgetUpload}
            onPlay={setPlaying}
            uploads={uploads}
            uploadingName={uploadingName}
            ytStatus={ytStatus}
          />
        )}
      </div>
    </div>
    {playing && (
      <RenderPlayerModal
        open
        onClose={() => setPlaying(null)}
        title={playing.split("/").pop() || playing}
        subtitle={playing.includes("/") ? playing : undefined}
        src={api.renderStreamUrl(team, tournament, date, match, playing)}
        downloadUrl={api.downloadUrl(team, tournament, date, match, playing)}
        youtubeUrl={
          uploads[playing] ? `https://youtu.be/${uploads[playing]}` : null
        }
      />
    )}
    </>
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
  /** Trigger a YouTube upload (full renders + player reels). */
  onUpload: (filename: string) => void;
  /** Clear a stale upload record so the row can be uploaded again. */
  onForgetUpload: (filename: string) => void;
  /** Open a render in the in-app player. */
  onPlay: (filename: string) => void;
  /** Map of filename -> YouTube video id for already-uploaded renders. */
  uploads: Record<string, string>;
  /** Filename currently being enqueued (button disabled / spinner). */
  uploadingName: string | null;
  /** Whether the server can upload at all; null while loading. */
  ytStatus: YouTubeStatusDto | null;
}

function RendersByDir({
  renders,
  team,
  tournament,
  date,
  match,
  onDelete,
  onUpload,
  onForgetUpload,
  onPlay,
  uploads,
  uploadingName,
  ytStatus,
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
            const canUpload = isUploadable(r.filename);
            const uploaded = uploads[r.filename];
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
                    {/* Play, don't download: checking the output is
                        the reason to click, and a full match is
                        gigabytes. Download has its own button. */}
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault();
                        onPlay(r.filename);
                      }}
                      title={`Play ${r.filename}`}
                    >
                      {leaf}
                    </a>
                  </div>
                  <div className="row-meta">
                    {formatBytes(r.size_bytes)} · {formatAge(r.created_at)}
                  </div>
                </div>
                <a
                  href={api.downloadUrl(
                    team,
                    tournament,
                    date,
                    match,
                    r.filename
                  )}
                  download
                  title="Download this file"
                >
                  <button style={{ padding: "2px 8px" }}>⬇</button>
                </a>
                {canUpload &&
                  (uploaded ? (
                    <span
                      style={{ display: "flex", gap: 6, alignItems: "center" }}
                    >
                      <a
                        href={`https://youtu.be/${uploaded}`}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: "#3aa55d", whiteSpace: "nowrap" }}
                        title="Open on YouTube"
                      >
                        ▶ YouTube
                      </a>
                      <button
                        onClick={() => onForgetUpload(r.filename)}
                        disabled={uploadingName === r.filename}
                        title={
                          "Forget this upload record so the render can be " +
                          "uploaded again (use after deleting the video on " +
                          "YouTube). The video itself is not deleted."
                        }
                        style={{ padding: "1px 6px", fontSize: 11 }}
                      >
                        ↺
                      </button>
                    </span>
                  ) : (
                    <button
                      onClick={() => onUpload(r.filename)}
                      disabled={
                        !ytStatus?.configured ||
                        uploadingName === r.filename
                      }
                      title={
                        ytStatus?.configured
                          ? "Enqueue an upload of this render to YouTube"
                          : ytStatus?.reason ||
                            "YouTube uploads are not configured for this server"
                      }
                    >
                      {uploadingName === r.filename
                        ? "Enqueueing…"
                        : "Upload"}
                    </button>
                  ))}
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
  /** Open the finished output in the in-app player. Omitted by callers
   *  that don't have a player mounted (e.g. the team queue widget). */
  onPlay?: () => void;
}

export function JobRow({
  job,
  onCancel,
  onDelete,
  downloadUrl,
  onPlay,
}: JobRowProps) {
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
                    <>
                      {onPlay ? (
                        <a
                          href="#"
                          onClick={(e) => {
                            e.preventDefault();
                            onPlay();
                          }}
                          title="Play in the browser"
                        >
                          ▶ {job.output_filename}
                        </a>
                      ) : (
                        <span>{job.output_filename}</span>
                      )}
                      {" · "}
                      <a href={downloadUrl} download>
                        download
                      </a>
                    </>
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
