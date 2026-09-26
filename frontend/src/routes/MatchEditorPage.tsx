import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { MatchDto } from "../types/api";
import { ClipManager } from "../components/ClipManager";
import { resolveClipFromGlobal } from "../components/clipFrames";
import { ControlsPanel } from "../components/Controls/ControlsPanel";
import { EventList } from "../components/EventList";
import { Modal } from "../components/Modal";
import { RenderPanel } from "../components/RenderPanel";
import { Timeline } from "../components/Timeline/Timeline";
import { CurrentEventBadge } from "../components/CurrentEventBadge";
import { VideoActionBar } from "../components/VideoActionBar";
import { VideoPlayer, type VideoPlayerHandle } from "../components/VideoPlayer";
import { displayName } from "../util/names";

const TOP_H_KEY = "vme.topRowH";
const TOP_MIN = 220;
const TIMELINE_MIN = 120;

export function MatchEditorPage() {
  const { team = "", tournament = "", date = "", match = "" } = useParams();
  const tournamentHref = `/teams/${encodeURIComponent(team)}/tournaments/${encodeURIComponent(tournament)}`;
  const [data, setData] = useState<MatchDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null);
  const [clipsOpen, setClipsOpen] = useState(false);
  const [renderOpen, setRenderOpen] = useState(false);
  const playerRef = useRef<VideoPlayerHandle | null>(null);
  // Set by the controls panel while a prompt is waiting on an answer.
  const [playbackLock, setPlaybackLock] = useState<string | null>(null);
  // Which half of the editor a phone is showing. Ignored above the
  // breakpoint, where all three columns are on screen at once.
  const [mobilePane, setMobilePane] = useState<"video" | "panels">("video");
  // Bumped to ask the event list to scroll the selected row into view
  // even when the selection itself has not changed.
  const [revealSelected, setRevealSelected] = useState(0);
  // Frame of the scoring event just created, for the libero rotation
  // rule in ControlsPanel. Raised here rather than there because a point
  // can be scored from the bar under the video too, and the rule is
  // about the line-up, not about which button was pressed.
  const [liberoCheckFrame, setLiberoCheckFrame] = useState<number | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const [topH, setTopH] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(TOP_H_KEY);
    const n = raw ? parseInt(raw, 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : null;
  });

  function startResize(e: React.PointerEvent<HTMLDivElement>) {
    if (!gridRef.current) return;
    e.preventDefault();
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    const gridRect = gridRef.current.getBoundingClientRect();
    const prevCursor = document.body.style.cursor;
    const prevUserSelect = document.body.style.userSelect;
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";

    const onMove = (ev: PointerEvent) => {
      const y = ev.clientY - gridRect.top;
      const max = Math.max(TOP_MIN, gridRect.height - TIMELINE_MIN);
      const h = Math.max(TOP_MIN, Math.min(max, y));
      setTopH(h);
    };
    const onUp = () => {
      target.releasePointerCapture(e.pointerId);
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", onUp);
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = prevUserSelect;
      setTopH((h) => {
        if (h !== null) window.localStorage.setItem(TOP_H_KEY, String(Math.round(h)));
        return h;
      });
    };
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onUp);
  }

  function resetSplitter() {
    setTopH(null);
    window.localStorage.removeItem(TOP_H_KEY);
  }

  const load = useCallback(async () => {
    try {
      const m = await api.getMatch(team, tournament, date, match);
      setData(m);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [team, tournament, date, match]);

  useEffect(() => { void load(); }, [load]);

  /** Set by any deliberate move of the playhead, so a saved position of
   *  frame 0 can be told apart from a video that never loaded. */
  const playheadMovedRef = useRef(false);

  function seek(globalFrame: number) {
    playheadMovedRef.current = true;
    setCurrentFrame(globalFrame);
    playerRef.current?.seekToGlobalFrame(globalFrame);
  }

  async function uploadClip(
    file: File,
    onProgress: (pct: number) => void,
    signal?: AbortSignal
  ) {
    const m = await api.uploadClip(
      team,
      tournament,
      date,
      match,
      file,
      onProgress,
      signal
    );
    setData(m);
  }
  async function importClipPath(sourcePath: string, mode: "copy" | "move") {
    const res = await api.importClipPath(
      team,
      tournament,
      date,
      match,
      sourcePath,
      mode
    );
    setData(res.match);
    return res.imported;
  }
  async function reorderClips(ids: string[]) {
    setData(await api.reorderClips(team, tournament, date, match, ids));
  }
  async function deleteClip(id: string) {
    setData(await api.deleteClip(team, tournament, date, match, id));
  }

  async function setClipTime(
    id: string,
    startRecordingTime: number | null,
    shiftFollowing: boolean
  ) {
    setData(
      await api.setClipTime(
        team, tournament, date, match, id, startRecordingTime, shiftFollowing
      )
    );
  }

  // Global shift: the dialog, its draft offset, and what the last one
  // did. Kept here rather than in the list because the list is a list.
  const [shiftOpen, setShiftOpen] = useState(false);
  const [shiftDraft, setShiftDraft] = useState("-0.5");
  const [shiftBusy, setShiftBusy] = useState(false);
  const [shiftResult, setShiftResult] = useState<string | null>(null);

  async function applyShift(seconds: number) {
    if (!seconds || shiftBusy) return;
    setShiftBusy(true);
    try {
      const r = await api.shiftEvents(team, tournament, date, match, seconds);
      setData(r.match);
      setShiftResult(
        `Moved ${r.moved} event${r.moved === 1 ? "" : "s"} ` +
          `${seconds > 0 ? "later" : "earlier"} by ${Math.abs(seconds)}s` +
          (r.clamped
            ? `. ${r.clamped} landed outside the footage and were pulled ` +
              "back to the nearest frame."
            : ".")
      );
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setShiftBusy(false);
    }
  }

  /**
   * Delete every event on the match.
   *
   * Behind a confirmation that names the count, because the undo is
   * "re-tag the match" - and the usual reason to be here is a botched
   * phone import, where the next step is re-importing onto a clean
   * match rather than deleting three hundred rows one at a time.
   */
  function clearAllEvents() {
    const n = data?.events.length ?? 0;
    if (n === 0) return;
    if (
      !window.confirm(
        `Delete all ${n} event${n === 1 ? "" : "s"} on this match?\n\n` +
          "Clip transitions are kept. This cannot be undone."
      )
    ) {
      return;
    }
    void (async () => {
      setData(await api.clearEvents(team, tournament, date, match));
      setSelectedEventId(null);
    })();
  }
  /**
   * The event before / after the playhead, in timeline order.
   *
   * Strictly before or after: an event exactly at the playhead is the
   * one you are already on, so stepping onto it again would be a button
   * that looks broken.
   */
  function adjacentEvent(dir: 1 | -1, types?: ReadonlySet<string>) {
    const placed = (data?.events ?? []).filter(
      (e) => e.global_frame != null && (!types || types.has(e.type))
    );
    return dir > 0
      ? placed.find((e) => (e.global_frame as number) > currentFrame)
      : [...placed].reverse().find((e) => (e.global_frame as number) < currentFrame);
  }

  /**
   * Every way a point is put on the board.
   *
   * A `score` is the plain +1, used for every point the opponent wins
   * and for ours when nobody was credited; a kill and an ace are our
   * points with a player attached. Stepping by "points" has to mean all
   * three or it skips half the match.
   */
  const POINT_TYPES: ReadonlySet<string> = new Set(["score", "kill", "ace"]);

  /**
   * Open the panels on the event the video is sitting on.
   *
   * The list is off screen on a phone, so arriving at it with the
   * selection wherever it was last left means hunting for your place in
   * a list of hundreds. Selecting the playhead event makes the list
   * scroll to it (see `EventList`), and `revealSelected` forces that
   * even when the selection did not change - the row can be anywhere in
   * a pane that was hidden a moment ago.
   */
  function showPanels(): "panels" {
    const placed = (data?.events ?? []).filter((e) => e.global_frame != null);
    let current = null as (typeof placed)[number] | null;
    for (const e of placed) {
      if ((e.global_frame as number) <= currentFrame) current = e;
      else break;
    }
    if (current) setSelectedEventId(current.id);
    setRevealSelected((n) => n + 1);
    return "panels";
  }

  /**
   * The playhead's event, as a chip.
   *
   * Rendered over the top-left of the picture.
   */
  const currentEventBadge = data ? (
    <CurrentEventBadge
      events={data.events}
      currentFrame={currentFrame}
      fps={data.fps}
      homeRoster={data.home_roster}
      opponentRoster={data.opponent_roster}
      homeName={data.home_roster.team_name || data.team}
      opponentName={data.opponent_roster.team_name || data.opponent || "Away"}
    />
  ) : null;

  /**
   * Remember where the timeline is looking, on the match.
   *
   * Deliberately does NOT feed the response back into `data`: the view
   * is the one piece of match state the editor is allowed to write
   * without re-rendering from it, and re-rendering would push the
   * restored view back at the timeline mid-gesture.
   */
  const saveTimelineView = useCallback(
    (zoom: number, anchorFrame: number) => {
      void api
        .patchMatch(team, tournament, date, match, {
          timeline_zoom: zoom,
          timeline_anchor_frame: anchorFrame,
        })
        .catch(() => {
          // A lost view is not worth an error banner over the editor.
        });
    },
    [team, tournament, date, match],
  );

  /**
   * Put the playhead back where it was left, once per match.
   *
   * Waits for the player: `seekToGlobalFrame` needs the clip loaded and
   * its metadata read, and a seek issued before that is silently
   * dropped - which is what "it does not remember where I was" looked
   * like even with the frame stored correctly. Retried briefly, then
   * given up on rather than fought over.
   */
  const restoredPlayheadRef = useRef(false);
  const currentFrameRef = useRef(currentFrame);
  currentFrameRef.current = currentFrame;
  useEffect(() => {
    if (restoredPlayheadRef.current) return;
    const frame = data?.playhead_frame ?? 0;
    if (!data || frame <= 0) {
      if (data) restoredPlayheadRef.current = true;
      return;
    }
    restoredPlayheadRef.current = true;
    let tries = 0;
    const tick = () => {
      tries += 1;
      const player = playerRef.current;
      player?.seekToGlobalFrame(frame);
      // Ask the PLAYER where it is, not this component's own state:
      // `onFrame` only reports during playback, so `currentFrame` is
      // whatever the page last set - checking it would be checking our
      // own optimism. `getGlobalFrame` reads the video element.
      const real = player?.getGlobalFrame() ?? 0;
      if (Math.abs(real - frame) < 3) {
        // The header, the timeline marker and the event list all read
        // `currentFrame`; without this they sit at zero while the
        // picture shows the restored moment.
        setCurrentFrame(frame);
        return;
      }
      if (tries < 40) window.setTimeout(tick, 150);
    };
    tick();
  }, [data]);

  /**
   * Remember where the video is, debounced.
   *
   * The timer restarts on every frame, so playback writes nothing at
   * all - it saves once the picture settles, which is exactly when the
   * position becomes worth keeping.
   */
  useEffect(() => {
    if (!restoredPlayheadRef.current) return;
    // Frame 0 is only saved when the user actually went there. A player
    // that never loaded also reports 0, and writing that back would
    // erase the stored position on nothing more than a slow video.
    if (currentFrame === 0 && !playheadMovedRef.current) return;
    const timer = window.setTimeout(() => {
      void api
        .patchMatch(team, tournament, date, match, {
          playhead_frame: Math.max(0, Math.round(currentFrameRef.current)),
        })
        .catch(() => {
          // Losing a playhead is not worth an error banner.
        });
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [currentFrame, team, tournament, date, match]);

  function stepToEvent(dir: 1 | -1, types?: ReadonlySet<string>) {
    const target = adjacentEvent(dir, types);
    if (!target) return;
    seek(target.global_frame as number);
    setSelectedEventId(target.id);
  }

  /**
   * Post an event, and make sure it actually landed.
   *
   * A tap that does not reach the server used to vanish: `createEvent`
   * had no catch, so the rejected promise went nowhere and the only
   * symptom was an event missing from the list, noticed at the next
   * tap. Courtside that is a lost kill nobody can reconstruct.
   *
   * Retried, but never blindly. Creating an event is not idempotent and
   * a lost RESPONSE looks exactly like a lost request from here, so
   * each attempt asks the server what it has before trying again -
   * otherwise one tap becomes two events, which is worse than none.
   *
   * Returns the new match, or null when every attempt failed - by which
   * point the banner is up and the event is known not to be saved.
   */
  async function createWithRetry(
    body: Parameters<typeof api.createEvent>[4],
    prevIds: Set<number>
  ): Promise<MatchDto | null> {
    const ATTEMPTS = 3;
    let lastErr: unknown = null;
    for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
      try {
        return await api.createEvent(team, tournament, date, match, body);
      } catch (e) {
        lastErr = e;
        // Did it land anyway?
        const fresh = await api
          .getMatch(team, tournament, date, match)
          .catch(() => null);
        const landed = fresh?.events.find(
          (ev) => !prevIds.has(ev.id) && ev.type === body.type
        );
        if (fresh && landed) return fresh;
        if (attempt < ATTEMPTS - 1) {
          await new Promise((r) => setTimeout(r, 250 * (attempt + 1)));
        }
      }
    }
    setError(
      `Could not save "${body.type.replace(/_/g, " ")}" - ${String(lastErr)}. ` +
        "It is NOT on the server. Tag it again."
    );
    return null;
  }

  async function createEvent(body: Parameters<typeof api.createEvent>[4]) {
    // Capture the existing event ids BEFORE the call so we can identify
    // the newly added one in the response. The server returns the whole
    // match (no dedicated "created event" response), so a set-diff is
    // the simplest robust way - works even if the server inserts events
    // out of submission order (e.g. when sorting by frame).
    const prevIds = new Set(data?.events.map((e) => e.id) ?? []);
    const updated = await createWithRetry(body, prevIds);
    if (!updated) return;
    setData(updated);
    // Only scoring events rotate, so only they can carry a libero into
    // the front row. Checked once `data` reflects the new event.
    if (body.type === "score" || body.type === "kill" || body.type === "ace") {
      setLiberoCheckFrame(currentFrame);
    }
    const created = updated.events.find((e) => !prevIds.has(e.id));
    if (created) {
      // Select it so the EventList scrolls to and highlights the new row.
      // Don't seek - the playhead is already where the user added it.
      setSelectedEventId(created.id);
    }
  }
  async function deleteEvent(id: number) {
    setData(await api.deleteEvent(team, tournament, date, match, id));
    if (selectedEventId === id) setSelectedEventId(null);
  }

  /** Shift one event along the timeline. The server owns the arithmetic:
   *  it moves by wall clock where the clips have recording times and by
   *  frames where they do not, and either way returns the whole match so
   *  the recomputed states arrive with it. */
  /** Drop an event behind another; it lands one frame later. */
  async function moveEvent(id: number, afterId: number | null) {
    try {
      setData(await api.moveEvent(team, tournament, date, match, id, afterId));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function nudgeEvent(id: number, seconds: number) {
    try {
      const updated = await api.nudgeEvent(
        team, tournament, date, match, id, seconds
      );
      setData(updated);
      setError(null);
      // Follow the event you just moved. Nudging is aiming - a second
      // either way until the tag sits on the moment it describes - and
      // that is only judgeable by looking at the frame it landed on,
      // which meant a nudge, then a click on the row, then the next
      // nudge. The playhead goes where the event went instead.
      const moved = updated.events.find((e) => e.id === id);
      if (moved?.global_frame != null) {
        seek(moved.global_frame);
        // Nudging a row makes it the one being worked on, so the
        // selection follows too rather than staying on whatever was
        // highlighted before.
        setSelectedEventId(id);
      }
    } catch (e) {
      setError(String(e));
    }
  }
  /** Create a cut_start/cut_end pair spanning [startFrame, endFrame].
   *
   * There is no batch event endpoint, so this is two POSTs. If the second
   * fails the first is rolled back: a lone cut_start is an orphan that
   * drops nothing at render time but does show up in red, and leaving the
   * user to clean up after a failure they didn't cause is rude. The
   * rollback is best-effort - if it also fails, the orphan badge is
   * exactly the right way to surface that.
   */
  async function insertCut(startFrame: number, endFrame: number) {
    if (!data) return;
    const s = resolveClipFromGlobal(data.clips, startFrame);
    const e = resolveClipFromGlobal(data.clips, endFrame);
    if (!s || !e) return;
    const prevIds = new Set(data.events.map((ev) => ev.id));
    try {
      const afterStart = await api.createEvent(team, tournament, date, match, {
        type: "cut_start",
        clip_id: s.clipId,
        local_frame: s.localFrame,
      });
      const startId = afterStart.events.find((ev) => !prevIds.has(ev.id))?.id;
      let updated;
      try {
        updated = await api.createEvent(team, tournament, date, match, {
          type: "cut_end",
          clip_id: e.clipId,
          local_frame: e.localFrame,
        });
      } catch (err) {
        if (startId !== undefined) {
          try {
            setData(
              await api.deleteEvent(team, tournament, date, match, startId)
            );
          } catch {
            setData(afterStart);
          }
        }
        throw err;
      }
      setData(updated);
      // Select the cut_start, not the cut_end: it's the edge the user is
      // most likely to want to nudge, and it keeps the list parked at the
      // top of the span rather than at the serve they were already looking
      // at.
      if (startId !== undefined) setSelectedEventId(startId);
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }
  /** Persist a team color edited from a team-card swatch.
   *
   * Home is a team-level property (roster.json, shared by every match),
   * while the opponent roster is embedded in this match's match.json -
   * hence the two different endpoints. Both send the full roster back
   * because PATCH/PUT replace the object wholesale.
   */
  async function setTeamColor(which: "home" | "opponent", hex: string) {
    if (!data) return;
    try {
      if (which === "home") {
        const saved = await api.putRoster(team, {
          ...data.home_roster,
          team_color: hex,
        });
        setData((d) => (d ? { ...d, home_roster: saved } : d));
      } else {
        setData(
          await api.patchMatch(team, tournament, date, match, {
            opponent_roster: { ...data.opponent_roster, team_color: hex },
          })
        );
      }
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function setLiberos(liberos: number[]) {
    if (!data) return;
    setData(await api.patchMatch(team, tournament, date, match, { liberos }));
    setError(null);
  }

  async function runAutoCuts(opts: { hasIntroClip?: boolean } = {}) {
    const r = await api.autoCuts(team, tournament, date, match, opts);
    setData(r.match);
    return r;
  }

  if (!data) {
    return (
      <div className="page">
        {error ? <div className="error">{error}</div> : <p className="muted">Loading match...</p>}
        <div style={{ marginTop: 16 }}>
          <Link to={tournamentHref}>← Back to {displayName(tournament)}</Link>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Editor-wide failures, over the layout rather than in it: the
          grid is sized to the viewport, so a banner in the flow would
          push the timeline off the bottom. Every `setError` in this
          screen used to land here and be rendered nowhere - the only
          place `error` appeared was the "could not load" screen, which
          is why a failed save looked like nothing happening at all. */}
      {error && (
        <div className="editor-error" role="alert">
          <span>{error}</span>
          <button onClick={() => setError(null)} title="Dismiss">✕</button>
        </div>
      )}
      <div className="editor-header">
        {/* Phones only (CSS hides it above the breakpoint). The editor is
            three columns of dense controls; on a phone there is room for
            exactly one, so the panels take the video's place rather than
            being squeezed alongside it. */}
        <button
          className="pane-toggle"
          onClick={() => setMobilePane((p) => (p === "video" ? showPanels() : "video"))}
          title={
            mobilePane === "video"
              ? "Show the controls and the event list"
              : "Back to the video"
          }
        >
          {mobilePane === "video" ? "▤ Panels" : "▶ Video"}
        </button>
        <Link to={tournamentHref} className="muted">← {displayName(tournament)}</Link>
        <strong>
          {/* 0 means "unnumbered": there is only one match that day. */}
          {data.match_index ? `Match ${data.match_index} – ` : ""}
          vs {data.opponent || "TBD"}
        </strong>
        <span className="muted">{data.date || "no date"}</span>
        <span className="muted frame-display">
          frame {currentFrame} / {data.clips.reduce((s, c) => s + c.frame_count, 0)}
          {" · "}
          {data.fps.toFixed(2)} fps
        </span>
        <div className="header-spacer" />
        <button onClick={() => setClipsOpen(true)}>Clips...</button>
        <button
          onClick={() => {
            // Nobody wants the match footage still running behind a
            // dialog they opened to watch a render.
            playerRef.current?.pause();
            setRenderOpen(true);
          }}
        >
          Render...
        </button>
      </div>

      <div
        className={`editor-grid pane-${mobilePane}`}
        ref={gridRef}
        style={topH !== null ? { gridTemplateRows: `${topH}px 6px auto` } : undefined}
      >
        <div className="editor-video">
          {/* Over the picture: the point is to read it without looking
              away from the video. Absolutely positioned, so it never
              takes a row from the player underneath. */}
          {currentEventBadge}
          <VideoPlayer
            ref={playerRef}
            playbackLock={playbackLock}
            team={team}
            tournament={tournament}
            date={date}
            match={match}
            clips={data.clips}
            fps={data.fps}
            onFrame={(f) => setCurrentFrame(f)}
          />
          <VideoActionBar
            clips={data.clips}
            currentFrame={currentFrame}
            homeName={data.home_roster.team_name || data.team}
            opponentName={data.opponent_roster.team_name || data.opponent || "Away"}
            homeColor={data.home_roster.team_color}
            opponentColor={data.opponent_roster.team_color}
            onCreate={createEvent}
            onPrevEvent={() => stepToEvent(-1)}
            onNextEvent={() => stepToEvent(1)}
            hasPrevEvent={adjacentEvent(-1) != null}
            hasNextEvent={adjacentEvent(1) != null}
          />
        </div>

        <div className="editor-controls">
          <ControlsPanel
            data={data}
            currentFrame={currentFrame}
            onCreate={createEvent}
            onAutoCuts={runAutoCuts}
            onTeamColorChange={setTeamColor}
            onLiberosChange={setLiberos}
            onPlaybackLock={setPlaybackLock}
            liberoCheckFrame={liberoCheckFrame}
            onStepPoint={(dir) => stepToEvent(dir, POINT_TYPES)}
            onLiberoChecked={() => setLiberoCheckFrame(null)}
            onRosterChanged={load}
            team={team}
            tournament={tournament}
            date={date}
            match={match}
          />
        </div>

        <div className="editor-events">
          <EventList
            events={data.events}
            selectedId={selectedEventId}
            currentFrame={currentFrame}
            onSelect={setSelectedEventId}
            revealSelected={revealSelected}
            onDelete={(id) => void deleteEvent(id)}
            onNudge={nudgeEvent}
            onMove={moveEvent}
            onClearAll={clearAllEvents}
            onShiftAll={() => {
              setShiftResult(null);
              setShiftOpen(true);
            }}
            liberos={data.liberos}
            onSeek={seek}
            onInsertCut={insertCut}
            fps={data.fps}
            homeRoster={data.home_roster}
            opponentRoster={data.opponent_roster}
            homeName={data.home_roster.team_name || data.team}
            opponentName={data.opponent_roster.team_name || data.opponent || "Away"}
          />
        </div>

        <div
          className="row-splitter"
          onPointerDown={startResize}
          onDoubleClick={resetSplitter}
          role="separator"
          aria-orientation="horizontal"
          title="Drag to resize · Double-click to reset"
        />

        <div className="editor-timeline">
          <Timeline
            savedZoom={data.timeline_zoom}
            savedAnchorFrame={data.timeline_anchor_frame}
            onViewChange={saveTimelineView}
            clips={data.clips}
            events={data.events}
            currentFrame={currentFrame}
            selectedEventId={selectedEventId}
            onSeek={seek}
            onSelectEvent={setSelectedEventId}
          />
        </div>
      </div>

      <Modal open={clipsOpen} onClose={() => setClipsOpen(false)} title="Clips" width="min(900px, 95vw)">
        <ClipManager
          clips={data.clips}
          onUpload={uploadClip}
          onImportPath={importClipPath}
          onReorder={reorderClips}
          onDelete={deleteClip}
          onSetClipTime={setClipTime}
        />
      </Modal>

      <Modal
        open={shiftOpen}
        onClose={() => setShiftOpen(false)}
        title="Shift every event"
        width="min(520px, 92vw)"
      >
        <p className="muted" style={{ marginTop: 0 }}>
          Moves the whole log against the footage, for when it is late or
          early as a whole - tagging lag that was not compensated at the
          time, or a clip whose recording time was corrected afterwards.
          Nothing changes order, so a shift the wrong way is undone by
          shifting back. Clip transitions stay where the files join.
        </p>
        <div className="toolbar" style={{ flexWrap: "wrap", marginBottom: 10 }}>
          {[-1, -0.5, -0.2, 0.2, 0.5, 1].map((s) => (
            <button
              key={s}
              onClick={() => void applyShift(s)}
              disabled={shiftBusy}
              title={s < 0 ? "Move events earlier" : "Move events later"}
            >
              {s > 0 ? `+${s}` : s}s
            </button>
          ))}
        </div>
        <label style={{ display: "block", marginBottom: 10 }}>
          Or an exact amount, in seconds (negative = earlier)
          <br />
          <input
            type="number"
            step="0.05"
            min={-60}
            max={60}
            value={shiftDraft}
            onChange={(e) => setShiftDraft(e.target.value)}
            style={{ width: 120 }}
          />{" "}
          <button
            className="primary"
            disabled={shiftBusy || !Number(shiftDraft)}
            onClick={() => void applyShift(Number(shiftDraft))}
          >
            {shiftBusy ? "Shifting…" : "Apply"}
          </button>
        </label>
        {shiftResult && <p className="muted">{shiftResult}</p>}
        <div className="toolbar" style={{ justifyContent: "flex-end" }}>
          <button onClick={() => setShiftOpen(false)} disabled={shiftBusy}>
            Close
          </button>
        </div>
      </Modal>

      <Modal open={renderOpen} onClose={() => setRenderOpen(false)} title="Render" width="min(640px, 95vw)">
        <RenderPanel
          team={team}
          tournament={tournament}
          date={date}
          match={match}
          hasClips={data.clips.length > 0}
          currentFrame={currentFrame}
          fps={data.fps}
          reelLeadSeconds={data.reel_lead_seconds}
          reelTailSeconds={data.reel_tail_seconds}
          reelLeadDefault={data.reel_lead_default}
          reelTailDefault={data.reel_tail_default}
          onMatchPatched={setData}
        />
      </Modal>
    </div>
  );
}
