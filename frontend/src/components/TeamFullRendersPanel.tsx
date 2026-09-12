import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { FullRenderDto, YouTubeStatusDto } from "../types/api";
import { displayName } from "../util/names";
import { RenderPlayerModal } from "./RenderPlayerModal";

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

/** Work still owed to YouTube for one render.
 *
 *  Both queues are "offline" in the sense that they finish on their own
 *  schedule: an upload can be parked for hours by a throttle or for a
 *  day by the quota, and a refused thumbnail is retried by a background
 *  worker. Without a mark on the row, the only evidence is a job buried
 *  in the queue list, so a render that is on its way looks exactly like
 *  one nobody touched - and the user re-clicks Upload. */
function PendingBadges({ r }: { r: FullRenderDto }) {
  const items: { text: string; title: string; warn?: boolean }[] = [];
  if (r.upload_state === "uploading") {
    items.push({
      text: "↑ uploading",
      title: r.upload_detail || "Uploading to YouTube now",
    });
  } else if (r.upload_state === "queued") {
    items.push({
      text: "↑ queued",
      title:
        (r.upload_detail ? r.upload_detail + " · " : "") +
        "Upload is waiting its turn - it needs the render queue running.",
    });
  } else if (r.upload_state === "waiting") {
    items.push({
      text: "↑ waiting",
      warn: true,
      title:
        r.upload_detail ||
        "Upload is parked until YouTube accepts it again. It retries " +
          "by itself; nothing to do.",
    });
  }
  if (r.thumbnail_pending) {
    items.push({
      text: "🖼 queued",
      title:
        "YouTube hasn't accepted this thumbnail yet (usually its " +
        "per-channel rate limit). A background worker keeps trying; " +
        "the video shows its old image until then.",
    });
  } else if (r.thumbnail_refused) {
    items.push({
      text: "🖼 refused",
      warn: true,
      title:
        "YouTube refused this thumbnail repeatedly and the retry " +
        "worker gave up. Common cause: the channel isn't verified " +
        "(youtube.com/verify). Press 🖼 to regenerate and try again.",
    });
  }
  if (!items.length) return null;
  return (
    <>
      {items.map((it) => (
        <span
          key={it.text}
          title={it.title}
          style={{
            fontSize: 10,
            padding: "1px 6px",
            borderRadius: 3,
            border: "1px solid",
            borderColor: it.warn ? "var(--warn, #d29922)" : "var(--border)",
            color: it.warn ? "var(--warn, #d29922)" : "var(--text-dim)",
            background: it.warn ? "rgba(210, 153, 34, 0.10)" : "transparent",
            whiteSpace: "nowrap",
          }}
        >
          {it.text}
        </span>
      ))}
    </>
  );
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
  // Render currently open in the player dialog, if any.
  const [playing, setPlaying] = useState<FullRenderDto | null>(null);
  // Media-server folder from the team profile. Null until loaded; the
  // copy button only appears once a folder is configured, because
  // without one the action can only fail.
  const [mediaServerPath, setMediaServerPath] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setRenders(await api.listTeamFullRenders(team));
    } catch (e) {
      setErr(String(e));
    }
  }, [team]);

  // Status is re-fetched on the same cadence as the renders list, not
  // just once: it carries today's quota spend, which moves as uploads
  // run and is the thing that decides whether queueing more is useful.
  // The endpoint reads two local files - no API round-trip.
  const refreshStatus = useCallback(() => {
    void api.youtubeStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    void reload();
    refreshStatus();
    const t = window.setInterval(() => {
      void reload();
      refreshStatus();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [reload, refreshStatus]);

  // Roster is fetched once (not polled): the media-server path changes
  // when the user edits team settings, which remounts this panel.
  useEffect(() => {
    void api
      .getRoster(team)
      .then((r) => setMediaServerPath(r.media_server?.path?.trim() || null))
      .catch(() => setMediaServerPath(null));
  }, [team]);

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

  /** Copy this render + its Jellyfin images to the media-server folder.
   *
   *  The copy itself runs as a background job (gigabytes), so this only
   *  reports what was started - progress shows up in the render queue
   *  widget. An unchanged destination is reported as skipped rather
   *  than re-copied. */
  async function copyToMediaServer(r: FullRenderDto) {
    const id = `${r.tournament}/${r.date}/${r.match}/${r.filename}`;
    setErr(null);
    setNote(null);
    setBusyId(id);
    try {
      const res = await api.copyToMediaServer(
        team,
        r.tournament,
        r.date,
        r.match,
        r.filename
      );
      setNote({ text: res.message, ok: true });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusyId(null);
    }
  }

  /** Delete the render file (and its sidecars) from disk.
   *
   *  Confirmed, because it's gigabytes of irreversible work - and the
   *  confirmation says so if the render is already on YouTube, since
   *  deleting the local file leaves that video in place with nothing
   *  behind it to re-upload or re-thumbnail. */
  async function remove(r: FullRenderDto) {
    const id = `${r.tournament}/${r.date}/${r.match}/${r.filename}`;
    const leaf = r.filename.split("/").pop() || r.filename;
    const uploadedWarning = r.youtube_video_id
      ? `\n\nThis render is on YouTube (${r.youtube_video_id}). That video ` +
        `stays up, but you won't be able to re-upload or re-thumbnail it ` +
        `without rendering again.`
      : "";
    if (
      !confirm(
        `Delete ${leaf} (${formatBytes(r.size_bytes)})?\n\nThe file and its ` +
          `thumbnail / poster / chapter sidecars are removed from disk. ` +
          `Copies already on the media server are not touched.` +
          uploadedWarning
      )
    )
      return;
    setErr(null);
    setNote(null);
    setBusyId(id);
    try {
      await api.deleteRender(team, r.tournament, r.date, r.match, r.filename);
      setNote({ text: `Deleted ${leaf}`, ok: true });
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

  // Match days, newest first. Sorted by the MATCH date, not by when the
  // files were written: `renders` arrives ordered by created_at, so
  // re-rendering a match from last month would otherwise jump that day
  // to the front of the pager and make the season order meaningless.
  // Dates are ISO (`YYYY-MM-DD`), so a string compare is a date compare.
  const countByDate = new Map<string, number>();
  for (const r of renders) {
    countByDate.set(r.date, (countByDate.get(r.date) ?? 0) + 1);
  }
  const dates: string[] = [...countByDate.keys()].sort((a, b) =>
    b.localeCompare(a)
  );
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
    <>
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
        {/* The API's daily quota is the real constraint on a match day:
            six uploads, then nothing until Pacific midnight. Say so
            up front instead of letting the 7th upload discover it. */}
        {status?.configured && status.quota && (
          <span
            className="row-meta"
            title={
              `${status.quota.spent} of ${status.quota.limit} API units used ` +
              `today (${status.quota.uploads_today} upload(s)). ` +
              `videos.insert costs 1600, so ${status.quota.uploads_remaining} ` +
              `more fit today. Resets in ` +
              `${Math.floor(status.quota.seconds_until_reset / 3600)}h ` +
              `${Math.floor((status.quota.seconds_until_reset % 3600) / 60)}m ` +
              `(midnight US/Pacific). Queue as many as you like - the queue ` +
              `parks what doesn't fit and resumes after the reset.`
            }
            style={{
              color:
                status.quota.uploads_remaining > 0
                  ? "var(--text-dim)"
                  : "var(--warn, #d29922)",
            }}
          >
            {status.quota.uploads_remaining > 0
              ? `${status.quota.uploads_remaining} upload${
                  status.quota.uploads_remaining === 1 ? "" : "s"
                } left today`
              : `quota used up - resets in ${Math.floor(
                  status.quota.seconds_until_reset / 3600
                )}h`}
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
            const isCondensed = r.kind === "condensed";
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
                    {/* A condensed render is the same match at half the
                        length; without a badge the pair looks like one
                        of them failed. */}
                    {isCondensed && (
                      <span
                        style={{
                          fontSize: 10,
                          padding: "1px 6px",
                          borderRadius: 3,
                          border: "1px solid var(--border)",
                          color: "var(--text-dim)",
                        }}
                        title="Condensed: only the plays, set breaks faded"
                      >
                        condensed
                      </span>
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
                    <PendingBadges r={r} />
                  </div>
                  <div className="row-meta" style={{ marginTop: 2 }}>
                    {/* Opens the player, not a download: the usual
                        reason to click a render is to check it. */}
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault();
                        setPlaying(r);
                      }}
                      title={`Play ${r.filename}`}
                    >
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
                      {mediaServerPath && (
                        <button
                          onClick={() => copyToMediaServer(r)}
                          disabled={busyId === id}
                          title={`Copy this render and its images to ${mediaServerPath}`}
                          style={{ padding: "1px 6px", fontSize: 11 }}
                        >
                          {busyId === id ? "…" : "📺"}
                        </button>
                      )}
                      <a href={downloadUrl(r)} download title="Download">
                        <button style={{ padding: "1px 6px", fontSize: 11 }}>
                          ⬇
                        </button>
                      </a>
                      <button
                        onClick={() => remove(r)}
                        disabled={busyId === id}
                        title={
                          "Delete this render from disk. The YouTube video " +
                          "is not touched."
                        }
                        style={{ padding: "1px 6px", fontSize: 11 }}
                      >
                        {busyId === id ? "…" : "🗑"}
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
                  {mediaServerPath && (
                    <button
                      onClick={() => copyToMediaServer(r)}
                      disabled={busyId === id}
                      title={`Copy this render and its images to ${mediaServerPath}`}
                      style={{ padding: "2px 8px" }}
                    >
                      {busyId === id ? "…" : "📺"}
                    </button>
                  )}
                  <a href={downloadUrl(r)} download title="Download this render">
                    <button style={{ padding: "2px 8px" }}>⬇</button>
                  </a>
                  <button
                    onClick={() => remove(r)}
                    disabled={busyId === id}
                    title="Delete this render from disk"
                    style={{ padding: "2px 8px" }}
                  >
                    {busyId === id ? "…" : "🗑"}
                  </button>
                  <button
                    onClick={() => upload(r)}
                    disabled={!status?.configured || busyId === id}
                    title={
                      !status?.configured
                        ? status?.reason ||
                          "YouTube uploads are not configured for this server"
                        : status.quota && status.quota.uploads_remaining <= 0
                          ? "Today's YouTube API quota is used up - this will " +
                            "be queued and uploaded automatically after the " +
                            "reset (midnight US/Pacific)."
                          : "Upload this render to YouTube"
                    }
                    style={{ padding: "2px 8px" }}
                  >
                    {/* Not ▶: that already means "play" on the filename
                        link above, and this queues an upload. */}
                    {busyId === id ? "…" : "📤"}
                  </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
    {playing && (
      <RenderPlayerModal
        open
        onClose={() => setPlaying(null)}
        title={playing.filename.split("/").pop() || playing.filename}
        subtitle={`${playing.date} · ${displayName(
          playing.opponent || playing.match
        )} · ${formatBytes(playing.size_bytes)}`}
        src={api.renderStreamUrl(
          team,
          playing.tournament,
          playing.date,
          playing.match,
          playing.filename
        )}
        downloadUrl={downloadUrl(playing)}
        youtubeUrl={
          playing.youtube_video_id
            ? `https://youtu.be/${playing.youtube_video_id}`
            : null
        }
      />
    )}
    </>
  );
}
