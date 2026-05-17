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

  async function uploadClip(file: File, onProgress: (pct: number) => void) {
    const m = await api.uploadClip(team, tournament, date, match, file, onProgress);
    setData(m);
  }
  async function reorderClips(ids: string[]) {
    setData(await api.reorderClips(team, tournament, date, match, ids));
  }
  async function deleteClip(id: string) {
    setData(await api.deleteClip(team, tournament, date, match, id));
  }
  async function rescan() {
    setData(await api.rescanMatch(team, tournament, date, match));
  }
  async function createEvent(body: Parameters<typeof api.createEvent>[4]) {
    setData(await api.createEvent(team, tournament, date, match, body));
  }
  async function deleteEvent(id: number) {
    setData(await api.deleteEvent(team, tournament, date, match, id));
    if (selectedEventId === id) setSelectedEventId(null);
  }
  async function runAutoCuts() {
    const r = await api.autoCuts(team, tournament, date, match);
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
          onReorder={reorderClips}
          onDelete={deleteClip}
          onRescan={rescan}
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
