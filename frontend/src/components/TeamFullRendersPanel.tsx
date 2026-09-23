import { useCallback, useEffect, useRef, useState } from "react";
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

/** Stable identity for a render row: unique, and the same string the
 *  per-row busy indicator already keys on. */
function rowId(r: FullRenderDto): string {
  return `${r.tournament}/${r.date}/${r.match}/${r.filename}`;
}

/** Which match a render belongs to. Tournament is part of it: two
 *  tournaments can each have a `01_Bellevue` on the same date, and the
 *  folder name alone would merge them into one group. */
function matchKey(r: FullRenderDto): string {
  return `${r.tournament}/${r.date}/${r.match}`;
}

/** One entry in the match picker. */
interface MatchGroup {
  key: string;
  date: string;
  matchIndex: number | null;
  opponent: string;
  tournament: string;
  count: number;
}

/** "2026-09-16 · M2 vs Woodinville · KingCo Conference". The number is
 *  left out for the only match of a day, where it would be noise. */
function matchOptionLabel(g: MatchGroup): string {
  const num = g.matchIndex ? `M${g.matchIndex} ` : "";
  return (
    `${g.date} · ${num}vs ${displayName(g.opponent)} · ` +
    `${displayName(g.tournament)} (${g.count})`
  );
}

/** Has this render been uploaded anywhere?
 *
 *  Both engines write a sidecar, so a row is done if either did.
 *  Checking only `youtube_video_id` made bulk-select offer to upload
 *  files that were already in OneDrive. */
function isUploaded(r: FullRenderDto): boolean {
  return !!r.youtube_video_id || !!r.onedrive_item_id;
}

function leafOf(r: FullRenderDto): string {
  return r.filename.split("/").pop() || r.filename;
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
        "Upload is waiting its turn - it needs the Uploads lane " +
        "running (team home page). Rendering is a separate lane.",
    });
  } else if (r.upload_state === "waiting") {
    items.push({
      text: "↑ waiting",
      warn: true,
      title:
        r.upload_detail ||
        "Upload is parked until YouTube accepts it again. It retries " +
          "by itself as long as the Uploads lane is running; renders " +
          "are not held up by it.",
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
  // Which match is on screen, by `matchKey`. A match produces one full
  // render plus a reel per player, so a season's worth of rows is
  // unusable as one list - and the natural unit to work through is a
  // match: render it, thumbnail it, upload it, move on. A match rather
  // than a day because a tournament day is often two or three of them,
  // and their reels interleaved into one list is exactly the mess this
  // grouping is here to prevent. Stored as the key rather than an index
  // so polling (which replaces the array) can't silently slide the user
  // onto a different match.
  const [activeMatch, setActiveMatch] = useState<string | null>(null);
  // Render currently open in the player dialog, if any.
  const [playing, setPlaying] = useState<FullRenderDto | null>(null);
  // Media-server folder from the team profile. Null until loaded; the
  // copy button only appears once a folder is configured, because
  // without one the action can only fail.
  const [mediaServerPath, setMediaServerPath] = useState<string | null>(null);
  // Rows ticked for a bulk action, by `rowId`. Ids rather than indices:
  // the list is re-fetched every 5s, and an index would silently point
  // at a different render after a delete or a new render landing.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Progress of a bulk action. Also acts as the "a bulk run is in
  // flight" flag that disables every other control - a delete racing a
  // bulk upload over the same rows is not worth supporting.
  const [bulk, setBulk] = useState<{
    label: string;
    done: number;
    total: number;
  } | null>(null);
  // Anchor for shift-click range selection, in VISIBLE-row order.
  const lastClickedRef = useRef<number | null>(null);

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

  // Drop selected ids that no longer exist. The list is re-fetched
  // every 5s, and a render can vanish under the selection (deleted
  // here, deleted elsewhere, or a job that rewrote the folder). Keeping
  // a dead id would let a later bulk action 404 on a row the user can't
  // even see.
  useEffect(() => {
    setSelected((prev) => {
      if (!prev.size) return prev;
      const live = new Set(renders.map(rowId));
      const next = new Set([...prev].filter((id) => live.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [renders]);

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
   *  (for an already-uploaded render) replace the live one wherever it
   *  is hosted. The server picks the engine from the render's own upload
   *  sidecar and reports its verdict separately from generation, so a
   *  refused push still leaves a fresh local image. */
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
      setNote({ text: res.message, ok: res.pushed || !res.destination });
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

  // ---- Bulk actions ---------------------------------------------------

  /** Run `fn` over `rows` one at a time, collecting failures.
   *
   *  Sequential on purpose. Deletes and thumbnail regeneration do real
   *  disk/CPU work, thumbnail pushes hit a per-channel rate limit that
   *  parallelism turns into 429s, and a half-applied bulk action is far
   *  easier to reason about when the failures are in row order. One
   *  row's failure never stops the rest - the whole point of selecting
   *  twelve reels is not to babysit them.
   */
  async function runBulk(
    label: string,
    rows: FullRenderDto[],
    fn: (r: FullRenderDto) => Promise<string | null>,
    opts: { skipped?: number; keepSelection?: boolean } = {}
  ) {
    if (!rows.length) return;
    setErr(null);
    setNote(null);
    setBulk({ label, done: 0, total: rows.length });
    const problems: string[] = [];
    for (const r of rows) {
      setBusyId(rowId(r));
      try {
        const problem = await fn(r);
        if (problem) problems.push(`${leafOf(r)}: ${problem}`);
      } catch (e) {
        problems.push(`${leafOf(r)}: ${String(e)}`);
      }
      setBulk((b) => (b ? { ...b, done: b.done + 1 } : b));
    }
    setBusyId(null);
    setBulk(null);
    if (!opts.keepSelection) setSelected(new Set());
    await reload();
    const ok = rows.length - problems.length;
    const skipped = opts.skipped
      ? `, ${opts.skipped} skipped`
      : "";
    if (problems.length) {
      // A partial failure is a failure. Name the rows that didn't make
      // it - "3 of 12 failed" with no names means re-checking twelve.
      setErr(
        `${label}: ${problems.length} of ${rows.length} failed.\n` +
          problems.join("\n")
      );
      setNote({ text: `${label}: ${ok} succeeded${skipped}.`, ok: false });
    } else {
      setNote({ text: `${label}: ${ok} succeeded${skipped}.`, ok: true });
    }
  }

  async function bulkUpload(rows: FullRenderDto[]) {
    // Already-uploaded rows are skipped rather than refused: selecting a
    // whole match and pressing upload should do the remaining ones,
    // not error because one is done.
    const todo = rows.filter((r) => !isUploaded(r));
    const skipped = rows.length - todo.length;
    if (!todo.length) {
      setNote({
        text: "Every selected render is already on YouTube.",
        ok: true,
      });
      return;
    }
    await runBulk(
      "Queue uploads",
      todo,
      async (r) => {
        await api.enqueueYouTubeUpload(team, r.tournament, r.date, r.match, {
          filename: r.filename,
        });
        return null;
      },
      { skipped }
    );
  }

  async function bulkThumbnails(rows: FullRenderDto[]) {
    await runBulk("Regenerate thumbnails", rows, async (r) => {
      const res = await api.regenerateThumbnail(
        team,
        r.tournament,
        r.date,
        r.match,
        r.filename
      );
      setThumbVersion((v) => ({ ...v, [rowId(r)]: Date.now() }));
      // A refused push is a failure even though the local image was
      // written - the published copy still shows the old one.
      return res.pushed || !res.destination ? null : res.message;
    });
  }

  async function bulkMediaServer(rows: FullRenderDto[]) {
    await runBulk("Copy to media server", rows, async (r) => {
      await api.copyToMediaServer(team, r.tournament, r.date, r.match, r.filename);
      return null;
    });
  }

  async function bulkDelete(rows: FullRenderDto[]) {
    const bytes = rows.reduce((n, r) => n + r.size_bytes, 0);
    const alreadyUp = rows.filter((r) => isUploaded(r)).length;
    if (
      !confirm(
        `Delete ${rows.length} render${rows.length === 1 ? "" : "s"} ` +
          `(${formatBytes(bytes)})?\n\n` +
          `The files and their thumbnail / poster / timestamp sidecars are ` +
          `removed from disk. Copies already on the media server are not ` +
          `touched.` +
          (alreadyUp
            ? `\n\n${alreadyUp} of them ${
                alreadyUp === 1 ? "has" : "have"
              } been uploaded. Those copies stay up, but you won't be ` +
              `able to re-upload or re-thumbnail them without rendering ` +
              `again.`
            : "")
      )
    )
      return;
    await runBulk("Delete", rows, async (r) => {
      await api.deleteRender(team, r.tournament, r.date, r.match, r.filename);
      return null;
    });
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

  // Matches, latest first. Sorted by the MATCH's own date and number,
  // not by when the files were written: `renders` arrives ordered by
  // created_at, so re-rendering a match from last month would otherwise
  // jump it to the top and make the season order meaningless. Within a
  // day the higher number is the later match. Dates are ISO
  // (`YYYY-MM-DD`), so a string compare is a date compare.
  const groupsByKey = new Map<string, MatchGroup>();
  for (const r of renders) {
    const key = matchKey(r);
    const g = groupsByKey.get(key);
    if (g) {
      g.count += 1;
    } else {
      groupsByKey.set(key, {
        key,
        date: r.date,
        matchIndex: r.match_index,
        opponent: r.opponent || r.match,
        tournament: r.tournament,
        count: 1,
      });
    }
  }
  const matches: MatchGroup[] = [...groupsByKey.values()].sort(
    (a, b) =>
      b.date.localeCompare(a.date) ||
      (b.matchIndex ?? 0) - (a.matchIndex ?? 0) ||
      a.tournament.localeCompare(b.tournament) ||
      a.key.localeCompare(b.key)
  );
  // Fall back to the latest match when nothing is chosen yet, or when
  // the chosen one no longer has renders (deleted on disk).
  const currentMatch =
    activeMatch && groupsByKey.has(activeMatch)
      ? activeMatch
      : matches[0]?.key ?? null;
  const visible = currentMatch
    ? renders.filter((r) => matchKey(r) === currentMatch)
    : [];

  // Selection is only ever over the visible match: a bulk delete that
  // also took rows from a match you can't see would be indefensible.
  const visibleIds = visible.map(rowId);
  const selectedRows = visible.filter((r) => selected.has(rowId(r)));
  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));

  function toggleRow(index: number, shift: boolean) {
    const id = visibleIds[index];
    const turningOn = !selected.has(id);
    // Shift extends from the last row clicked, the way every file list
    // does it - twelve reels from one match are always contiguous.
    const anchor =
      shift && lastClickedRef.current !== null ? lastClickedRef.current : index;
    const lo = Math.min(anchor, index);
    const hi = Math.max(anchor, index);
    const next = new Set(selected);
    for (let i = lo; i <= hi; i++) {
      if (turningOn) next.add(visibleIds[i]);
      else next.delete(visibleIds[i]);
    }
    lastClickedRef.current = index;
    setSelected(next);
  }

  // Changing match clears the selection. Carrying it across would leave
  // ticked rows off-screen, and a bulk delete whose scope you cannot see
  // is not something to be clever about.
  function goToMatch(key: string) {
    setSelected(new Set());
    lastClickedRef.current = null;
    setActiveMatch(key);
  }

  function selectWhere(pred: (r: FullRenderDto) => boolean) {
    const next = new Set(selected);
    for (const r of visible) if (pred(r)) next.add(rowId(r));
    setSelected(next);
  }

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
          {matches.length > 1 ? ` of ${renders.length}` : ""})
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
        {/* YouTube's daily quota. Labelled as such since reels may be
            going to OneDrive, where it does not apply at all. */}
        {status?.configured && status.quota && (() => {
          const q = status.quota;
          // Two independent ceilings, and the honest label is whichever
          // one is actually binding. Saying "4 uploads left today" while
          // YouTube refuses every video (channel cap) describes our
          // ledger, not reality - and the ledger is the one thing the
          // user can't check.
          const blockedFor = q.upload_limit_until
            ? q.upload_limit_until * 1000 - Date.now()
            : 0;
          const blocked = blockedFor > 0;
          const resetH = Math.floor(q.seconds_until_reset / 3600);
          const resetM = Math.floor((q.seconds_until_reset % 3600) / 60);
          const blockedH = Math.max(1, Math.round(blockedFor / 3600000));
          let label: string;
          if (blocked) {
            label = `channel upload limit - retrying for ~${blockedH}h`;
          } else if (q.uploads_remaining > 0) {
            label = `${q.uploads_remaining} upload${
              q.uploads_remaining === 1 ? "" : "s"
            } left today`;
          } else {
            label = `quota used up - resets in ${resetH}h`;
          }
          return (
            <span
              className="row-meta"
              title={
                (blocked
                  ? "YouTube refused an upload with 'uploadLimitExceeded': " +
                    "this CHANNEL has published as many videos as it " +
                    "currently allows. That is separate from the API " +
                    "quota and clears as earlier uploads age out - " +
                    "queued uploads keep retrying on their own for up " +
                    "to a week. "
                  : "") +
                `${q.spent} of ${q.limit} API units used today ` +
                `(${q.uploads_today} successful upload(s)). ` +
                `videos.insert costs 1600, so ${
                  Math.floor(
                    Math.max(0, q.limit - q.spent) / 1600
                  )
                } more fit in today's quota. Resets in ${resetH}h ` +
                `${resetM}m (midnight US/Pacific). Queue as many as you ` +
                `like - the queue parks what doesn't fit and resumes by ` +
                `itself. Refused attempts are refunded, so this counts ` +
                `uploads that actually happened.`
              }
              style={{
                color:
                  !blocked && q.uploads_remaining > 0
                    ? "var(--text-dim)"
                    : "var(--warn, #d29922)",
              }}
            >
              {label}
            </span>
          );
        })()}
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

      {/* The match picker is the group header, so it shows even when
          there is only one match: the rows below no longer repeat which
          match they belong to, and this is where that is said. No
          previous/next arrows - a season is dozens of matches, and the
          one you want is almost never adjacent to the one you are on. */}
      {currentMatch && (
        <select
          value={currentMatch}
          onChange={(e) => goToMatch(e.target.value)}
          disabled={!!bulk}
          style={{ width: "100%", marginBottom: 8 }}
          title="Choose a match. Latest first."
        >
          {matches.map((g) => (
            <option key={g.key} value={g.key}>
              {matchOptionLabel(g)}
            </option>
          ))}
        </select>
      )}

      {/* Selection toolbar. Always present (not only once something is
          ticked) so the checkboxes are discoverable; the actions appear
          with a selection, because a row of dead buttons reads as
          broken. */}
      {visible.length > 0 && (
        <div
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
            marginBottom: 8,
            paddingBottom: 6,
            borderBottom: "1px solid var(--border)",
          }}
        >
          <label
            style={{
              display: "flex",
              flexDirection: "row",
              gap: 6,
              alignItems: "center",
              fontSize: 12,
            }}
            title="Select or clear every render in this match"
          >
            <input
              type="checkbox"
              checked={allVisibleSelected}
              ref={(el) => {
                // Mixed state needs the DOM property; React has no prop
                // for it.
                if (el)
                  el.indeterminate =
                    !allVisibleSelected && selectedRows.length > 0;
              }}
              onChange={() =>
                setSelected(
                  allVisibleSelected ? new Set() : new Set(visibleIds)
                )
              }
              disabled={!!bulk}
            />
            All
          </label>
          {/* One click for the case this list exists for: a match is
              one full render plus a reel per player. */}
          <button
            onClick={() => selectWhere((r) => r.kind === "reel")}
            disabled={!!bulk || !visible.some((r) => r.kind === "reel")}
            title="Add every player reel in this match to the selection"
            style={{ padding: "1px 8px", fontSize: 11 }}
          >
            + reels
          </button>
          <button
            onClick={() => selectWhere((r) => !isUploaded(r))}
            disabled={!!bulk || !visible.some((r) => !isUploaded(r))}
            title="Add everything not yet on YouTube to the selection"
            style={{ padding: "1px 8px", fontSize: 11 }}
          >
            + not uploaded
          </button>

          {bulk ? (
            <span className="row-meta" style={{ marginLeft: "auto" }}>
              {bulk.label}: {bulk.done} / {bulk.total}…
            </span>
          ) : selectedRows.length > 0 ? (
            <>
              <span
                className="row-meta"
                style={{ marginLeft: "auto", whiteSpace: "nowrap" }}
              >
                {selectedRows.length} selected ·{" "}
                {formatBytes(
                  selectedRows.reduce((n, r) => n + r.size_bytes, 0)
                )}
              </span>
              <button
                onClick={() => bulkThumbnails(selectedRows)}
                title={
                  "Regenerate thumbnails for the selected renders (and " +
                  "replace them on YouTube or OneDrive where the render " +
                  "is uploaded)."
                }
                style={{ padding: "2px 8px" }}
              >
                🖼
              </button>
              {mediaServerPath && (
                <button
                  onClick={() => bulkMediaServer(selectedRows)}
                  title={`Copy the selected renders and their images to ${mediaServerPath}`}
                  style={{ padding: "2px 8px" }}
                >
                  📺
                </button>
              )}
              <button
                onClick={() => bulkDelete(selectedRows)}
                title="Delete the selected renders from disk"
                style={{ padding: "2px 8px" }}
              >
                🗑
              </button>
              <button
                onClick={() => bulkUpload(selectedRows)}
                disabled={!status?.configured}
                title={
                  !status?.configured
                    ? status?.reason ||
                      "YouTube uploads are not configured for this server"
                    : "Queue an upload for each selected render that isn't " +
                      "on YouTube yet. The queue parks whatever doesn't fit " +
                      "in today's quota and resumes by itself."
                }
                style={{ padding: "2px 8px" }}
              >
                📤
              </button>
              <button
                onClick={() => setSelected(new Set())}
                title="Clear selection"
                style={{ padding: "2px 8px" }}
              >
                ×
              </button>
            </>
          ) : (
            <span
              className="row-meta"
              style={{ marginLeft: "auto", whiteSpace: "nowrap" }}
            >
              tick rows for bulk actions (shift-click for a range)
            </span>
          )}
        </div>
      )}

      {renders.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          No renders yet. Run a full render or player reels from a match
          page.
        </p>
      ) : (
        <div className="list render-list">
          {visible.map((r, rowIndex) => {
            const id = `${r.tournament}/${r.date}/${r.match}/${r.filename}`;
            // A row belongs to one engine. `destination` is where a
            // future upload goes; `uploaded`/`url` describe where it
            // already went, which can differ if the setting changed
            // after the fact - so read the sidecar, not the config.
            const toOneDrive = r.upload_destination === "onedrive";
            const onOneDrive = !!r.onedrive_item_id;
            const uploaded = isUploaded(r);
            const ytUrl = r.youtube_video_id
              ? `https://youtu.be/${r.youtube_video_id}`
              : null;
            const url = ytUrl ?? r.onedrive_url ?? null;
            const whereLabel = r.youtube_video_id
              ? "on YouTube"
              : onOneDrive
                ? "in OneDrive"
                : null;
            const uploadedAt = r.youtube_video_id
              ? r.youtube_uploaded_at
              : r.onedrive_uploaded_at;
            // "The published copy is showing OUR image." Each engine
            // answers for itself: `thumbnail_synced` is the YouTube
            // sidecar's field and is false on every OneDrive row, which
            // used to make them all look like a failed push.
            const hostHasThumb = r.youtube_video_id
              ? !!r.thumbnail_synced
              : !!r.onedrive_thumbnail_set;
            // "on YouTube" / "in OneDrive" reads as a phrase; the bare
            // noun is what a sentence about pushing needs.
            const hostName = r.youtube_video_id
              ? "YouTube"
              : onOneDrive
                ? "OneDrive"
                : null;
            const isReel = r.kind === "reel";
            const isCondensed = r.kind === "condensed";
            // Reels are one row per player: lead with the player so a
            // dozen rows from the same match stay distinguishable, and
            // show the leaf filename rather than the nested path.
            const leaf = r.filename.split("/").pop() || r.filename;
            return (
              <div
                key={id}
                className="list-row render-row"
                style={{
                  alignItems: "center",
                  background: selected.has(id)
                    ? "rgba(88, 166, 255, 0.08)"
                    : undefined,
                }}
              >
                <input
                  type="checkbox"
                  checked={selected.has(id)}
                  disabled={!!bulk}
                  onChange={() => {}}
                  onClick={(e) => toggleRow(rowIndex, e.shiftKey)}
                  title="Select for a bulk action (shift-click to extend)"
                  style={{ flex: "0 0 auto" }}
                />
                {/* Only show the image when it is what viewers see: for
                    an uploaded render that means its host accepted it.
                    Otherwise the row would advertise a thumbnail the
                    published copy doesn't have. */}
                {r.has_thumbnail && (!uploaded || hostHasThumb) && (
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
                {r.has_thumbnail && uploaded && !hostHasThumb && (
                  <span
                    title={
                      `A thumbnail was generated but ${hostName} has not ` +
                      "accepted it, so the published copy still shows its " +
                      "own image. Use the thumbnail button to try again."
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
                    {/* Rows lead with WHAT they are. Which match they
                        belong to is the picker above - every row on
                        screen shares it, so repeating date, opponent and
                        tournament fourteen times was the noise that
                        buried the one thing that differs. */}
                    {isReel ? (
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
                        <strong style={{ fontSize: 13 }}>
                          {r.player_label || leaf}
                        </strong>
                      </>
                    ) : isCondensed ? (
                      // Same match at half the length; named, or the
                      // pair looks like one of them failed.
                      <strong
                        style={{ fontSize: 13 }}
                        title="Condensed: only the plays, set breaks faded"
                      >
                        Condensed match
                      </strong>
                    ) : (
                      <strong style={{ fontSize: 13 }}>Full match</strong>
                    )}
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
                      {url ? (
                        <a
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          title={
                            uploadedAt
                              ? `Uploaded ${formatAge(uploadedAt)}`
                              : "Uploaded"
                          }
                          style={{ color: "#3aa55d" }}
                        >
                          ▶ {whereLabel}
                        </a>
                      ) : (
                        // A OneDrive upload whose tenant forbade even a
                        // signed-in link. It is still up; there is just
                        // nowhere to point.
                        <span style={{ color: "#3aa55d" }} title="Uploaded">
                          {whereLabel} (no link)
                        </span>
                      )}
                      {/* Offered for both engines. OneDrive takes a
                          custom thumbnail through the `source` member of
                          the item's thumbnail set; on a Business or
                          SharePoint drive it refuses, and the server says
                          so rather than the button disappearing. */}
                      <button
                        onClick={() => regenerateThumbnail(r)}
                        disabled={busyId === id || !!bulk}
                        title={
                          "Regenerate the thumbnail from the current team " +
                          `colors, logos and names, and replace it on ${hostName} ` +
                          "too."
                        }
                        style={{ padding: "1px 6px", fontSize: 11 }}
                      >
                        {busyId === id ? "…" : "🖼"}
                      </button>
                      <button
                        onClick={() => forgetUpload(r)}
                        disabled={busyId === id || !!bulk}
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
                          disabled={busyId === id || !!bulk}
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
                        disabled={busyId === id || !!bulk}
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
                    disabled={busyId === id || !!bulk}
                    title={
                      "Generate the thumbnail for this render from the " +
                      "current team colors, logos and names. It is used " +
                      "when the render is uploaded, and by the media server."
                    }
                    style={{ padding: "2px 8px" }}
                  >
                    {busyId === id ? "…" : "🖼"}
                  </button>
                  {mediaServerPath && (
                    <button
                      onClick={() => copyToMediaServer(r)}
                      disabled={busyId === id || !!bulk}
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
                    disabled={busyId === id || !!bulk}
                    title="Delete this render from disk"
                    style={{ padding: "2px 8px" }}
                  >
                    {busyId === id ? "…" : "🗑"}
                  </button>
                  <button
                    onClick={() => upload(r)}
                    // OneDrive rows are not gated on YouTube's status or
                    // quota: neither applies to them, and a mixed queue
                    // drains the OneDrive half while the YouTube half
                    // waits out the reset.
                    disabled={
                      (!toOneDrive && !status?.configured) ||
                      busyId === id ||
                      !!bulk
                    }
                    title={
                      toOneDrive
                        ? "Upload this render to OneDrive"
                        : !status?.configured
                          ? status?.reason ||
                            "YouTube uploads are not configured for this server"
                          : status.quota?.uploads_blocked
                            ? "YouTube is refusing new videos on this channel " +
                              "right now (channel upload limit). This will be " +
                              "queued and retried automatically - for up to a " +
                              "week - without you watching it."
                            : status.quota && status.quota.uploads_remaining <= 0
                              ? "Today's YouTube API quota is used up - this " +
                                "will be queued and uploaded automatically " +
                                "after the reset (midnight US/Pacific)."
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
