import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { MatchDto } from "../types/api";
import { ClipManager } from "../components/ClipManager";
import { ControlsPanel } from "../components/Controls/ControlsPanel";
import { EventList } from "../components/EventList";
import { Modal } from "../components/Modal";
import { RenderPanel } from "../components/RenderPanel";
import { Timeline } from "../components/Timeline/Timeline";
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

  function seek(globalFrame: number) {
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
  async function createEvent(body: Parameters<typeof api.createEvent>[4]) {
    // Capture the existing event ids BEFORE the call so we can identify
    // the newly added one in the response. The server returns the whole
    // match (no dedicated "created event" response), so a set-diff is
    // the simplest robust way - works even if the server inserts events
    // out of submission order (e.g. when sorting by frame).
    const prevIds = new Set(data?.events.map((e) => e.id) ?? []);
    const updated = await api.createEvent(team, tournament, date, match, body);
    setData(updated);
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
      <div className="editor-header">
        <Link to={tournamentHref} className="muted">← {displayName(tournament)}</Link>
        <strong>
          {data.match_index != null ? `Match ${data.match_index} – ` : ""}
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
        <button onClick={() => setRenderOpen(true)}>Render...</button>
      </div>

      <div
        className="editor-grid"
        ref={gridRef}
        style={topH !== null ? { gridTemplateRows: `${topH}px 6px auto` } : undefined}
      >
        <div className="editor-video">
          <VideoPlayer
            ref={playerRef}
            team={team}
            tournament={tournament}
            date={date}
            match={match}
            clips={data.clips}
            fps={data.fps}
            onFrame={(f) => setCurrentFrame(f)}
          />
        </div>

        <div className="editor-controls">
          <ControlsPanel
            data={data}
            currentFrame={currentFrame}
            onCreate={createEvent}
            onAutoCuts={runAutoCuts}
            onTeamColorChange={setTeamColor}
          />
        </div>

        <div className="editor-events">
          <EventList
            events={data.events}
            selectedId={selectedEventId}
            onSelect={setSelectedEventId}
            onDelete={(id) => void deleteEvent(id)}
            onSeek={seek}
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
        />
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
        />
      </Modal>
    </div>
  );
}
