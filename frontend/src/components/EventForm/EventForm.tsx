import { useState } from "react";
import { EVENT_TYPES, type EventType } from "../../types/api";
import type { ClipDto } from "../../types/api";

interface Props {
  clips: ClipDto[];
  currentFrame: number;
  onCreate: (body: { type: string; clip_id?: string; local_frame?: number; payload?: Record<string, unknown> }) => Promise<void>;
}

function resolveClipFromGlobal(clips: ClipDto[], globalFrame: number) {
  let acc = 0;
  for (const c of clips) {
    if (globalFrame < acc + c.frame_count) return { clipId: c.id, localFrame: globalFrame - acc };
    acc += c.frame_count;
  }
  if (clips.length === 0) return null;
  const last = clips[clips.length - 1];
  return { clipId: last.id, localFrame: last.frame_count - 1 };
}

export function EventForm({ clips, currentFrame, onCreate }: Props) {
  const [type, setType] = useState<EventType>("ball_served");
  const [team, setTeam] = useState<"home" | "away">("home");
  const [playerNumber, setPlayerNumber] = useState<number>(0);
  const [text, setText] = useState("");
  const [homeDelta, setHomeDelta] = useState<number>(0);
  const [awayDelta, setAwayDelta] = useState<number>(0);
  const [position, setPosition] = useState<number>(1);
  const [fadeFrames, setFadeFrames] = useState<number>(30);
  const [frameShift, setFrameShift] = useState<number>(-30);
  const [fromClip, setFromClip] = useState<string>(clips[0]?.id ?? "");
  const [toClip, setToClip] = useState<string>(clips[1]?.id ?? clips[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function buildPayload(): Record<string, unknown> {
    switch (type) {
      case "score":
      case "first_serve":
        return { team };
      case "kill":
      case "assist":
      case "block":
      case "dive":
      case "dig":
      case "highlight":
      case "focus_in":
        return { team, player_number: playerNumber };
      case "ace":
        return { team, player_number: playerNumber };
      case "substitution":
        return { team, position, player_in_number: playerNumber };
      case "score_correction":
        return { home_delta: homeDelta, away_delta: awayDelta };
      case "message":
        return { text };
      case "cut_end":
        return { fade_frames: fadeFrames, frame_shift: frameShift };
      case "clip_transition":
        return { from_clip_id: fromClip, to_clip_id: toClip, fade_frames: fadeFrames, frame_shift: frameShift };
      case "game_start":
      case "game_end":
      case "set_end":
        return { fade_frames: fadeFrames };
      default:
        return {};
    }
  }

  async function submit() {
    setErr(null);
    setBusy(true);
    try {
      const payload = buildPayload();
      if (type === "clip_transition") {
        await onCreate({ type, payload });
      } else {
        const r = resolveClipFromGlobal(clips, currentFrame);
        if (!r) throw new Error("No clips loaded");
        await onCreate({ type, clip_id: r.clipId, local_frame: r.localFrame, payload });
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const showTeam = ["score", "first_serve", "kill", "ace", "assist", "block", "dive", "dig", "highlight", "substitution", "focus_in"].includes(type);
  const showPlayer = ["kill", "ace", "assist", "block", "dive", "dig", "highlight", "substitution", "focus_in"].includes(type);
  const showPosition = type === "substitution";
  const showCorrection = type === "score_correction";
  const showText = type === "message";
  const showFadeFrames = ["cut_end", "clip_transition", "game_start", "game_end", "set_end"].includes(type);
  const showFrameShift = ["cut_end", "clip_transition"].includes(type);
  const showClipPair = type === "clip_transition";

  return (
    <div style={{ padding: 12, borderTop: "1px solid var(--border)" }}>
      <div className="card-header">
        <h2>Add event</h2>
        <span className="muted">@ frame {currentFrame}</span>
      </div>
      {err && <div className="error" style={{ marginBottom: 8 }}>{err}</div>}
      <div className="field-row">
        <label style={{ flex: 1 }}>
          Type
          <select value={type} onChange={(e) => setType(e.target.value as EventType)}>
            {EVENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </div>
      {showTeam && (
        <div className="field-row">
          <label>
            Team
            <select value={team} onChange={(e) => setTeam(e.target.value as "home" | "away")}>
              <option value="home">Home</option>
              <option value="away">Away</option>
            </select>
          </label>
          {showPlayer && (
            <label>
              Player #
              <input type="number" value={playerNumber} onChange={(e) => setPlayerNumber(parseInt(e.target.value || "0", 10))} />
            </label>
          )}
          {showPosition && (
            <label>
              Position
              <input type="number" min={1} max={6} value={position} onChange={(e) => setPosition(parseInt(e.target.value || "1", 10))} />
            </label>
          )}
        </div>
      )}
      {showCorrection && (
        <div className="field-row">
          <label>
            Home Δ
            <input type="number" value={homeDelta} onChange={(e) => setHomeDelta(parseInt(e.target.value || "0", 10))} />
          </label>
          <label>
            Away Δ
            <input type="number" value={awayDelta} onChange={(e) => setAwayDelta(parseInt(e.target.value || "0", 10))} />
          </label>
        </div>
      )}
      {showText && (
        <div className="field-row">
          <label style={{ flex: 1 }}>
            Text
            <input value={text} onChange={(e) => setText(e.target.value)} />
          </label>
        </div>
      )}
      {showFadeFrames && (
        <div className="field-row">
          <label>
            Fade frames
            <input type="number" value={fadeFrames} onChange={(e) => setFadeFrames(parseInt(e.target.value || "0", 10))} />
          </label>
          {showFrameShift && (
            <label>
              Frame shift
              <input type="number" value={frameShift} onChange={(e) => setFrameShift(parseInt(e.target.value || "0", 10))} />
            </label>
          )}
        </div>
      )}
      {showClipPair && (
        <div className="field-row">
          <label>
            From clip
            <select value={fromClip} onChange={(e) => setFromClip(e.target.value)}>
              {clips.map((c) => <option key={c.id} value={c.id}>{c.filename}</option>)}
            </select>
          </label>
          <label>
            To clip
            <select value={toClip} onChange={(e) => setToClip(e.target.value)}>
              {clips.map((c) => <option key={c.id} value={c.id}>{c.filename}</option>)}
            </select>
          </label>
        </div>
      )}
      <div className="toolbar" style={{ marginTop: 12, justifyContent: "flex-end" }}>
        <button className="primary" onClick={submit} disabled={busy}>
          {busy ? "Adding..." : "Add event"}
        </button>
      </div>
    </div>
  );
}
