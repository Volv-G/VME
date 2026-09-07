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
 * The list refreshes itself so newly-finished renders and uploads
 * (driven by the global render queue) appear without a manual refresh.
 * Slowly, though: a render takes minutes to hours, so a fast poll only
 * bought a fresher timestamp. Live progress is the render queue
 * widget's job, and every action here refreshes on completion anyway.
 */
interface Props {
  team: string;
}

const POLL_MS = 60_000;

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
  // Per-row cache-buster: the thumbnail URL is stable, so a regenerated
  // image only shows up if we change the query string.
  const [thumbVersion, setThumbVersion] = useState<Record<string, number>>({});
  // Regenerate result. `ok` is false when YouTube declined the push -
  // that is a failure, not a footnote, so it must not be styled as one.
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  // Which match date is on screen. A single match day produces one full
  // render plus a reel per player, so a season's worth of rows is
  // unusable as one list - and the natural unit to work through is a
  // day: render it, thumbnail it, upload it, move on. Stored as the date
  // itself rather than an index so polling (which replaces the array)
  // can't silently slide the user onto a different day.
  const [activeDate, setActiveDate] = useState<string | null>(null);

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
    // With a minute between polls, coming back to the tab is exactly
    // when the list is most likely to be stale - so refresh on focus
    // instead of showing a minute-old view.
    const onFocus = () => void reload();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", onFocus);
    };
  }, [reload]);

  /** Clear a stale "uploaded" marker so the row offers upload again.
   *
   *  The server verifies the video is really gone from YouTube first and
   *  answers 409 if it isn't; we relay that and offer to force, which
   *  covers the case where verification itself failed (expired auth,
   *  offline). */
  async function forgetUpload(r: FullRenderDto) {
    const id = `${r.tournament}/${r.date}/${r.match}/${r.filename}`;
    setErr(null);
    setBusyId(id);
    try {
      await api.forgetYouTubeUpload(
        team,
        r.tournament,
        r.date,
        r.match,
        r.filename
      );
      await reload();
    } catch (e) {
      const msg = String(e);
      if (
        msg.includes("still exists") &&
        confirm(
          `${msg}\n\nClear the record anyway? The video on YouTube is not ` +
            `touched - you'd end up with a duplicate if you re-upload.`
        )
      ) {
        try {
          await api.forgetYouTubeUpload(
            team,
            r.tournament,
            r.date,
            r.match,
            r.filename,
            true
          );
          await reload();
        } catch (e2) {
          setErr(String(e2));
        }
      } else {
        setErr(msg);
      }
    } finally {
      setBusyId(null);
    }
  }

  /** Rebuild the thumbnail from the current colors / logos / names, and
   *  (for an already-uploaded render) replace the live one on YouTube.
   *  The server reports YouTube's verdict separately from generation, so
   *  a refused push still leaves a fresh local image. */
  async function regenerateThumbnail(r: FullRenderDto) {
    const id = `${r.tournament}/${r.date}/${r.match}/${r.filename}`;
    setErr(null);
    setNote(null);
    setBusyId(id);
    try {
      const res = await api.regenerateThumbnail(
        team,
        r.tournament,
        r.date,
        r.match,
        r.filename
      );
      setThumbVersion((v) => ({ ...v, [id]: Date.now() }));
      // Not pushing is only a success when there was nothing to push to.
      setNote({ text: res.message, ok: res.pushed || !res.video_id });
      await reload();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusyId(null);
    }
  }

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

  // Dates newest-first. `renders` already arrives in that order, so
  // first-seen order is the right order and no sort is needed.
  const dates: string[] = [];
  const countByDate = new Map<string, number>();
  for (const r of renders) {
    if (!countByDate.has(r.date)) dates.push(r.date);
    countByDate.set(r.date, (countByDate.get(r.date) ?? 0) + 1);
  }
  // Fall back to the newest date when nothing is chosen yet, or when the
  // chosen day no longer has renders (deleted on disk).
  const currentDate =
    activeDate && countByDate.has(activeDate) ? activeDate : dates[0] ?? null;
  const dateIndex = currentDate ? dates.indexOf(currentDate) : -1;
  const visible = currentDate
    ? renders.filter((r) => r.date === currentDate)
    : [];

  // Build a per-row download URL the same way the per-match panel does:
  // segment-encoded so nested filenames survive FastAPI's `{path}`
  // param. Reels live at `reels/<team>/<player>/<file>`, so the slashes
  // MUST stay slashes - encodeURIComponent on the whole path would turn
  // them into %2F and the route wouldn't match.
  function downloadUrl(r: FullRenderDto): string {
    const base =
      `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api/teams/` +
      `${encodeURIComponent(team)}/tournaments/${encodeURIComponent(r.tournament)}` +
      `/dates/${encodeURIComponent(r.date)}/matches/${encodeURIComponent(r.match)}/renders/`;
    return base + r.filename.split("/").map(encodeURIComponent).join("/");
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2>
          Full renders ({visible.length}
          {dates.length > 1 ? ` of ${renders.length}` : ""})
        </h2>
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
      {note && (
        <div
          className={note.ok ? "info" : "error"}
          style={{ marginBottom: 8, display: "flex", gap: 8 }}
        >
          <span style={{ flex: 1 }}>{note.text}</span>
          <button
            onClick={() => setNote(null)}
            style={{ padding: "0 6px", fontSize: 11 }}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}

      {dates.length > 1 && currentDate && (
        <div
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            marginBottom: 8,
          }}
        >
          <button
            onClick={() => setActiveDate(dates[dateIndex - 1])}
            disabled={dateIndex <= 0}
            title="Newer match day"
            style={{ padding: "2px 8px" }}
          >
            ‹
          </button>
          {/* A dropdown as well as arrows: stepping through a season one
              day at a time to reach a specific match would be tedious. */}
          <select
            value={currentDate}
            onChange={(e) => setActiveDate(e.target.value)}
            style={{ flex: 1, minWidth: 0 }}
          >
            {dates.map((d) => (
              <option key={d} value={d}>
                {d} ({countByDate.get(d)})
              </option>
            ))}
          </select>
          <button
            onClick={() => setActiveDate(dates[dateIndex + 1])}
            disabled={dateIndex < 0 || dateIndex >= dates.length - 1}
            title="Older match day"
            style={{ padding: "2px 8px" }}
          >
            ›
          </button>
          <span className="row-meta" style={{ whiteSpace: "nowrap" }}>
            day {dateIndex + 1} / {dates.length}
          </span>
        </div>
      )}

      {renders.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          No renders yet. Run a full render or player reels from a match
          page.
        </p>
      ) : (
        <div className="list">
          {visible.map((r) => {
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
            const isReel = r.kind === "reel";
            // Reels are one row per player: lead with the player so a
            // dozen rows from the same match stay distinguishable, and
            // show the leaf filename rather than the nested path.
            const leaf = r.filename.split("/").pop() || r.filename;
            return (
              <div
                key={id}
                className="list-row"
                style={{ alignItems: "center" }}
              >
                {/* Only show the image when it is what viewers see: for
                    an uploaded video that means YouTube accepted it.
                    Otherwise the row would advertise a thumbnail the
                    published video doesn't have. */}
                {r.has_thumbnail && (!uploaded || r.thumbnail_synced) && (
                  <a
                    href={api.renderThumbnailUrl(
                      team,
                      r.tournament,
                      r.date,
                      r.match,
                      r.filename,
                      thumbVersion[id]
                    )}
                    target="_blank"
                    rel="noreferrer"
                    title="Generated YouTube thumbnail - click to view full size"
                    style={{ flex: "0 0 auto", lineHeight: 0 }}
                  >
                    <img
                      src={api.renderThumbnailUrl(
                        team,
                        r.tournament,
                        r.date,
                        r.match,
                        r.filename,
                        thumbVersion[id]
                      )}
                      alt=""
                      width={64}
                      height={36}
                      style={{
                        borderRadius: 3,
                        border: "1px solid var(--border)",
                        objectFit: "cover",
                      }}
                    />
                  </a>
                )}
                {r.has_thumbnail && uploaded && !r.thumbnail_synced && (
                  <span
                    title={
                      "A thumbnail was generated but YouTube has not " +
                      "accepted it, so the video still shows its old " +
                      "image. Use the thumbnail button to try again."
                    }
                    style={{
                      flex: "0 0 auto",
                      width: 64,
                      height: 36,
                      borderRadius: 3,
                      border: "1px dashed var(--border)",
                      color: "var(--text-dim)",
                      fontSize: 14,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    ⚠
                  </span>
                )}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      alignItems: "baseline",
                      flexWrap: "wrap",
                    }}
                  >
                    {/* Redundant once the pager is showing the date;
                        without a pager it's the only place it appears. */}
                    {dates.length <= 1 && (
                      <strong style={{ fontSize: 13 }}>{r.date}</strong>
                    )}
                    {isReel && (
                      <>
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 6px",
                            borderRadius: 3,
                            border: "1px solid var(--border)",
                            color: "var(--text-dim)",
                          }}
                          title="Player reel: every play by this player, with chapters"
                        >
                          reel
                        </span>
                        {r.player_label && (
                          <strong style={{ fontSize: 13 }}>
                            {r.player_label}
                          </strong>
                        )}
                      </>
                    )}
                    <span>{matchLabel}</span>
                    <span className="muted" style={{ fontSize: 11 }}>
                      {displayName(r.tournament)}
                    </span>
                  </div>
                  <div className="row-meta" style={{ marginTop: 2 }}>
                    <a href={downloadUrl(r)} download title={r.filename}>
                      {leaf}
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
                    <div
                      style={{ display: "flex", gap: 8, alignItems: "center" }}
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
                      <button
                        onClick={() => regenerateThumbnail(r)}
                        disabled={busyId === id}
                        title={
                          "Regenerate the thumbnail from the current team " +
                          "colors, logos and names, and replace it on " +
                          "YouTube too."
                        }
                        style={{ padding: "1px 6px", fontSize: 11 }}
                      >
                        {busyId === id ? "…" : "🖼"}
                      </button>
                      <button
                        onClick={() => forgetUpload(r)}
                        disabled={busyId === id}
                        title={
                          "Forget this upload record so the render can be " +
                          "uploaded again (use after deleting the video on " +
                          "YouTube). The video itself is not deleted."
                        }
                        style={{ padding: "1px 6px", fontSize: 11 }}
                      >
                        {busyId === id ? "…" : "↺"}
                      </button>
                    </div>
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
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <button
                    onClick={() => regenerateThumbnail(r)}
                    disabled={busyId === id}
                    title={
                      "Generate the YouTube thumbnail for this render from " +
                      "the current team colors, logos and names."
                    }
                    style={{ padding: "2px 8px" }}
                  >
                    {busyId === id ? "…" : "🖼"}
                  </button>
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
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
