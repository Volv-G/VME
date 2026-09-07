import { useEffect, useRef, useState } from "react";
import type { AutoCutsResultDto, ClipDto, MatchDto } from "../../types/api";
import { LineupGrid } from "./LineupGrid";
import { RosterPicker } from "./RosterPicker";
import { ScoreDisplay } from "./ScoreDisplay";
import { ScoreFixDialog, MessageDialog } from "./InlineDialogs";
import { stateAtPlayhead } from "./state";
import { useHotkeyAction } from "../../hotkeys";
import { eventIcon } from "../eventStyle";
import { ColorPicker } from "../ColorPicker";
import { LogoPicker } from "../LogoPicker";
import { api } from "../../api/client";

type CreateBody = {
  type: string;
  clip_id?: string;
  local_frame?: number;
  payload?: Record<string, unknown>;
};

interface Props {
  data: MatchDto;
  currentFrame: number;
  onCreate: (body: CreateBody) => Promise<void>;
  onAutoCuts: (opts: {
    hasIntroClip?: boolean;
  }) => Promise<AutoCutsResultDto>;
  /** Persist a new team color (team roster for home, match's embedded
   *  opponent roster for away). Wired to the swatch in each team card. */
  onTeamColorChange?: (which: "home" | "opponent", hex: string) => Promise<void>;
  /** Reload the match after a logo upload/removal so the roster DTOs
   *  (and therefore the "has a logo" state) reflect what's on disk. */
  onRosterChanged?: () => Promise<void>;
  /** Match coordinates - the logo endpoints are path-scoped (team logo
   *  vs. this match's opponent logo). */
  team: string;
  tournament: string;
  date: string;
  match: string;
}

interface ActionDef {
  type: string;
  label: string;
  /** True when the action is attributed to a specific home player. */
  needsPlayer: boolean;
  /** Static payload merged in when the event is created. */
  payload?: Record<string, unknown>;
  /**
   * Override the emoji shown on the button. Falls back to `eventIcon(type)`
   * when omitted - which is the right answer for almost every action since
   * the type itself already tells you what verb is being logged.
   */
  icon?: string;
}

// Per-player home-team action buttons rendered under the lineup grid. Order
// matters: the 2-column layout pairs adjacent items, so keep related verbs
// next to each other. Focus In/Out live here (rather than in the Match
// section) because Focus In attributes to a specific player; Focus Out is
// global but stays alongside its pair for discoverability.
// Ace is `needsPlayer: false` because the server is always at home P1 -
// no point making the user click the lineup. The button handler below
// resolves the jersey from `live.home_positions[1]` and commits
// directly. Errors surface as a banner if P1 is empty (lineup not set).
const PLAYER_ACTIONS: ActionDef[] = [
  { type: "kill", label: "Kill", needsPlayer: true },
  { type: "ace", label: "Ace", needsPlayer: false },
  { type: "dig", label: "Dig", needsPlayer: true },
  { type: "dive", label: "Dive", needsPlayer: true },
  { type: "block", label: "Block", needsPlayer: true },
  { type: "highlight", label: "Highlight", needsPlayer: true },
  { type: "focus_in", label: "Focus In", needsPlayer: true },
  { type: "focus_out", label: "Focus Out", needsPlayer: false },
];

// Match-level actions in display order. One flat 2-column grid; ordering
// is chosen so each row is a natural pair:
//   Ball Served | Replay        (rally markers)
//   Cut Start   | Cut End       (cut boundaries)
//   Game Start  | Game End      (game lifecycle)
//   End Set     | Auto Cuts     (two singletons paired together)
//   Score Fix   | Message       (annotations / dialogs)
// Score Fix, Message, and Auto Cuts open dialogs / run bulk operations, so
// they're rendered inline below rather than driven by this table.
const MATCH_ACTIONS: ActionDef[] = [
  { type: "ball_served", label: "Ball Served", needsPlayer: false },
  { type: "replay", label: "Replay", needsPlayer: false },
  { type: "cut_start", label: "Cut Start", needsPlayer: false },
  {
    type: "cut_end",
    label: "Cut End",
    needsPlayer: false,
    payload: { fade_frames: 30, frame_shift: -30 },
  },
  {
    type: "game_start",
    label: "Game Start",
    needsPlayer: false,
    payload: { fade_frames: 30 },
  },
  {
    type: "game_end",
    label: "Game End",
    needsPlayer: false,
    payload: { fade_frames: 30 },
  },
  {
    type: "set_end",
    label: "End Set",
    needsPlayer: false,
    payload: { fade_frames: 30 },
  },
];

function resolveClipFromGlobal(
  clips: ClipDto[],
  globalFrame: number
): { clipId: string; localFrame: number } | null {
  let acc = 0;
  for (const c of clips) {
    if (globalFrame < acc + c.frame_count) return { clipId: c.id, localFrame: globalFrame - acc };
    acc += c.frame_count;
  }
  if (clips.length === 0) return null;
  const last = clips[clips.length - 1];
  return { clipId: last.id, localFrame: last.frame_count - 1 };
}

export function ControlsPanel({
  data,
  currentFrame,
  onCreate,
  onAutoCuts,
  onTeamColorChange,
  onRosterChanged,
  team,
  tournament,
  date,
  match,
}: Props) {
  // Logos are uploaded immediately (files, not form state); bumping this
  // version busts the browser cache for the fixed logo filename.
  const [logoVersion, setLogoVersion] = useState<number>(() => Date.now());
  const live = stateAtPlayhead(data.events, currentFrame);
  const homeName = data.home_roster.team_name || data.team;
  const opponentName = data.opponent_roster.team_name || data.opponent || "Away";

  const [armed, setArmed] = useState<ActionDef | null>(null);
  const [subPosition, setSubPosition] = useState<number | null>(null);
  const [scoreFixOpen, setScoreFixOpen] = useState(false);
  const [messageOpen, setMessageOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  // After a Kill is committed we transition into "pick the assister"
  // mode. Holds the killer's jersey so the picker can (a) suppress
  // self-assist and (b) show a helpful banner. `null` = not waiting.
  const [pendingAssistKiller, setPendingAssistKiller] = useState<
    number | null
  >(null);

  async function runAutoCuts() {
    setErr(null);
    setInfo(null);
    // Ask up-front whether clip 0 is an intro/title reel. The answer
    // changes WHERE the GameStart event lands (intro = start of clip 1;
    // no intro = start of clip 0) and whether the crossfade between
    // clips 0 and 1 is suppressed. Native confirm is fine - it's a
    // single yes/no and blocks until answered. Only ask when there's
    // more than one clip; a one-clip match can't have an intro AND
    // gameplay, so the question is meaningless.
    let hasIntroClip = false;
    if (data.clips.length >= 2) {
      hasIntroClip = window.confirm(
        "Is the FIRST clip an intro / title reel?\n\n" +
          "OK  - skip clip 1; place GameStart at clip 2 with a fade-in.\n" +
          "Cancel - clip 1 is gameplay; GameStart goes at its first frame."
      );
    }
    setBusy(true);
    try {
      const r = await onAutoCuts({ hasIntroClip });
      const parts: string[] = [`Added ${r.added} cut${r.added === 1 ? "" : "s"}`];
      if (r.added_set_ends)
        parts.push(`${r.added_set_ends} set end${r.added_set_ends === 1 ? "" : "s"}`);
      if (r.added_game_start) parts.push("game start");
      if (r.added_game_end) parts.push("game end");
      if (r.skipped_existing) parts.push(`${r.skipped_existing} already marked`);
      if (r.skipped_too_close) parts.push(`${r.skipped_too_close} continuous (no cut needed)`);
      if (r.skipped_too_short) parts.push(`${r.skipped_too_short} too short`);
      if (r.skipped_missing_time) parts.push(`${r.skipped_missing_time} missing timestamp`);
      setInfo(parts.join(" · "));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  function clearOverlays() {
    setArmed(null);
    setSubPosition(null);
    setScoreFixOpen(false);
    setMessageOpen(false);
    setPendingAssistKiller(null);
  }

  async function commit(
    type: string,
    payload: Record<string, unknown> = {},
    opts: { keepOverlays?: boolean } = {}
  ): Promise<boolean> {
    if (data.clips.length === 0) {
      setErr("No clips uploaded yet.");
      return false;
    }
    const r = resolveClipFromGlobal(data.clips, currentFrame);
    if (!r) return false;
    setErr(null);
    setBusy(true);
    try {
      await onCreate({ type, clip_id: r.clipId, local_frame: r.localFrame, payload });
      if (!opts.keepOverlays) clearOverlays();
      return true;
    } catch (e) {
      setErr(String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  // Hotkey: Q -> Ball Served at current playhead.
  useHotkeyAction("events.ballServed", () => {
    if (busy) return;
    void commit("ball_served");
  });

  function onActionButton(a: ActionDef) {
    setErr(null);
    // Cancel any pending assist flow when the user picks a new action.
    setPendingAssistKiller(null);
    if (a.needsPlayer) {
      // Toggle armed state: clicking the same one cancels.
      setArmed((cur) => (cur?.type === a.type ? null : a));
      setSubPosition(null);
      return;
    }
    if (a.type === "ace") {
      // The server is always home P1 in volleyball - no need to ask.
      const server = live.home_positions[1];
      if (server == null) {
        setErr("No player at P1 - set the home lineup first.");
        return;
      }
      void commit("ace", { team: "home", player_number: server });
      return;
    }
    if (a.type === "score_correction") {
      setScoreFixOpen(true);
      setArmed(null);
      setSubPosition(null);
      return;
    }
    if (a.type === "message") {
      setMessageOpen(true);
      setArmed(null);
      setSubPosition(null);
      return;
    }
    void commit(a.type, a.payload ?? {});
  }

  async function onLineupCellClick(position: number) {
    // Pending-assist flow takes priority over the armed-action flow:
    // once a Kill is logged we want the next lineup tap to be the
    // assister, even if `armed` was somehow non-null.
    if (pendingAssistKiller != null) {
      const jersey = live.home_positions[position];
      if (jersey == null) return;
      if (jersey === pendingAssistKiller) return; // no self-assist
      await commit("assist", { team: "home", player_number: jersey });
      // commit() runs clearOverlays() which also clears
      // pendingAssistKiller - good, the flow ends here.
      return;
    }
    if (armed) {
      const jersey = live.home_positions[position];
      if (jersey == null) return; // no player to attribute to
      const armedType = armed.type;
      const armedPayload = armed.payload ?? {};
      const ok = await commit(
        armedType,
        { team: "home", player_number: jersey, ...armedPayload },
        // Keep overlays for the Kill -> Assist follow-up so the lineup
        // grid stays mounted in the same render pass. Without this the
        // grid would flash closed before pendingAssistKiller takes
        // effect.
        { keepOverlays: armedType === "kill" }
      );
      if (ok && armedType === "kill") {
        setArmed(null);
        setPendingAssistKiller(jersey);
      }
      return;
    }
    setSubPosition(position);
  }

  function onSubPick(jersey: number) {
    if (subPosition == null) return;
    void commit("substitution", {
      team: "home",
      position: subPosition,
      player_in_number: jersey,
    });
  }

  return (
    <div className="controls-panel">
      <ScoreDisplay
        state={live}
        homeName={homeName}
        opponentName={opponentName}
        homeColor={data.home_roster.team_color}
        opponentColor={data.opponent_roster.team_color}
      />

      {err && <div className="error" style={{ marginBottom: 8 }}>{err}</div>}
      {info && <div className="info" style={{ marginBottom: 8 }}>{info}</div>}

      {/* Home team card */}
      <div className="team-card" style={data.home_roster.team_color ? { borderColor: data.home_roster.team_color } : undefined}>
        <div className="team-card-header">
          <TeamSwatch
            color={data.home_roster.team_color || "#2d8a4e"}
            teamName={homeName}
            onChange={
              onTeamColorChange
                ? (hex) => void onTeamColorChange("home", hex)
                : undefined
            }
          />
          <LogoPicker
            size={18}
            label={homeName}
            url={
              data.home_roster.team_logo_path
                ? api.teamLogoUrl(team, logoVersion)
                : null
            }
            onUpload={async (file) => {
              await api.uploadTeamLogo(team, file);
              setLogoVersion(Date.now());
              await onRosterChanged?.();
            }}
            onRemove={async () => {
              await api.deleteTeamLogo(team);
              setLogoVersion(Date.now());
              await onRosterChanged?.();
            }}
          />
          <strong className="team-name">{homeName}</strong>
          <button
            className="first-serve-btn"
            onClick={() => commit("first_serve", { team: "home" })}
            disabled={busy}
            title={`Mark ${homeName} as first server (this set)`}
            aria-label={`Mark ${homeName} as first server`}
          >
            {eventIcon("first_serve")}
          </button>
          <button className="primary" onClick={() => commit("score", { team: "home" })} disabled={busy}>
            +1
          </button>
        </div>

        {subPosition != null ? (
          <RosterPicker
            roster={data.home_roster}
            lineup={live.home_positions}
            position={subPosition}
            onPick={onSubPick}
            onClose={() => setSubPosition(null)}
          />
        ) : (
          <LineupGrid
            positions={live.home_positions}
            roster={data.home_roster}
            armedActionLabel={
              pendingAssistKiller != null
                ? `Assister for kill by #${pendingAssistKiller}`
                : armed?.label ?? null
            }
            onPositionClick={onLineupCellClick}
          />
        )}

        {armed && (
          <div className="armed-banner">
            <span>Pick a position for <strong>{armed.label}</strong></span>
            <button onClick={() => setArmed(null)}>Cancel</button>
          </div>
        )}
        {pendingAssistKiller != null && (
          <div className="armed-banner">
            <span>
              Pick the assister for kill by{" "}
              <strong>#{pendingAssistKiller}</strong> - or skip
            </span>
            <button onClick={() => setPendingAssistKiller(null)}>
              Skip assist
            </button>
          </div>
        )}

        <div className="action-grid">
          {PLAYER_ACTIONS.map((a) => (
            <ActionButton
              key={a.type}
              action={a}
              armed={armed?.type === a.type}
              disabled={busy}
              onClick={() => onActionButton(a)}
            />
          ))}
        </div>
      </div>

      {/* Opponent team card */}
      <div
        className="team-card"
        style={data.opponent_roster.team_color ? { borderColor: data.opponent_roster.team_color } : undefined}
      >
        <div className="team-card-header">
          <TeamSwatch
            color={data.opponent_roster.team_color || "#8a2d2d"}
            teamName={opponentName}
            onChange={
              onTeamColorChange
                ? (hex) => void onTeamColorChange("opponent", hex)
                : undefined
            }
          />
          <LogoPicker
            size={18}
            label={opponentName}
            url={
              data.opponent_roster.team_logo_path
                ? api.opponentLogoUrl(
                    team,
                    tournament,
                    date,
                    match,
                    logoVersion
                  )
                : null
            }
            onUpload={async (file) => {
              await api.uploadOpponentLogo(
                team,
                tournament,
                date,
                match,
                file
              );
              setLogoVersion(Date.now());
              await onRosterChanged?.();
            }}
            onRemove={async () => {
              await api.deleteOpponentLogo(team, tournament, date, match);
              setLogoVersion(Date.now());
              await onRosterChanged?.();
            }}
          />
          <strong className="team-name">{opponentName}</strong>
          <button
            className="first-serve-btn"
            onClick={() => commit("first_serve", { team: "away" })}
            disabled={busy}
            title={`Mark ${opponentName} as first server (this set)`}
            aria-label={`Mark ${opponentName} as first server`}
          >
            {eventIcon("first_serve")}
          </button>
          <button className="primary" onClick={() => commit("score", { team: "away" })} disabled={busy}>
            +1
          </button>
        </div>
      </div>

      {/* Match-level actions, grouped by purpose so related buttons sit
          next to each other. */}
      <div className="match-actions">
        <div className="section-title">Match</div>

        <div className="action-grid">
          {MATCH_ACTIONS.map((a) => (
            <ActionButton
              key={a.type}
              action={a}
              armed={armed?.type === a.type}
              disabled={busy}
              onClick={() => onActionButton(a)}
            />
          ))}
          {/* Auto Cuts sits in the same grid right after End Set so the two
              singletons share a row instead of leaving an empty cell.
              Doesn't map to an event type (it's a bulk operation), so we
              hand-pick the icon rather than going through ActionButton. */}
          <button
            type="button"
            className="action-btn"
            onClick={() => void runAutoCuts()}
            disabled={busy || data.clips.length < 2}
            title="Crossfade stop/restart joins; insert SetEnd at long gaps"
          >
            <span aria-hidden="true" className="action-icon">
              ⚙️
            </span>
            <span className="action-label">Auto Cuts</span>
          </button>
          {/* Score corrections and inline notes - both open dialogs rather
              than committing immediately. Reuse the event icons so the
              relationship to the underlying event type is obvious. */}
          <button
            type="button"
            className={`action-btn${scoreFixOpen ? " armed" : ""}`}
            onClick={() => {
              setScoreFixOpen((v) => !v);
              setMessageOpen(false);
              setArmed(null);
            }}
            disabled={busy}
          >
            <span aria-hidden="true" className="action-icon">
              {eventIcon("score_correction")}
            </span>
            <span className="action-label">Score Fix</span>
          </button>
          <button
            type="button"
            className={`action-btn${messageOpen ? " armed" : ""}`}
            onClick={() => {
              setMessageOpen((v) => !v);
              setScoreFixOpen(false);
              setArmed(null);
            }}
            disabled={busy}
          >
            <span aria-hidden="true" className="action-icon">
              {eventIcon("message")}
            </span>
            <span className="action-label">Message</span>
          </button>
        </div>
      </div>

      {scoreFixOpen && (
        <ScoreFixDialog
          onSubmit={(home, away) =>
            commit("score_correction", { home_delta: home, away_delta: away })
          }
          onClose={() => setScoreFixOpen(false)}
        />
      )}
      {messageOpen && (
        <MessageDialog
          onSubmit={(text) => commit("message", { text })}
          onClose={() => setMessageOpen(false)}
        />
      )}
    </div>
  );
}

/**
 * Single button in either action grid. Renders an emoji prefix + label,
 * with a tooltip that mirrors the label so the icon stays decorative.
 * Pulling this out of the parent body avoids repeating the icon-lookup
 * boilerplate in two near-identical .map() blocks.
 */
function ActionButton({
  action,
  armed,
  disabled,
  onClick,
}: {
  action: ActionDef;
  armed: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const icon = action.icon ?? eventIcon(action.type);
  return (
    <button
      type="button"
      className={`action-btn${armed ? " armed" : ""}`}
      onClick={onClick}
      disabled={disabled}
      title={action.label}
    >
      <span aria-hidden="true" className="action-icon">
        {icon}
      </span>
      <span className="action-label">{action.label}</span>
    </button>
  );
}

/**
 * The little color chip in a team-card header. Read-only `<span>` when no
 * `onChange` is supplied; otherwise it becomes the trigger for the shared
 * `ColorPicker` popover (same dialog the roster editor uses).
 *
 * The picker fires `onChange` continuously while the user drags across the
 * saturation square, so persistence is debounced and the swatch renders
 * from local state for instant feedback. `color` re-syncs the local value
 * whenever the server round-trip lands (or another edit path changes it).
 */
function TeamSwatch({
  color,
  teamName,
  onChange,
}: {
  color: string;
  teamName: string;
  onChange?: (hex: string) => void;
}) {
  const [local, setLocal] = useState(color);
  const timerRef = useRef<number | null>(null);

  useEffect(() => { setLocal(color); }, [color]);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  if (!onChange) {
    return <span className="team-swatch" style={{ background: color }} />;
  }

  return (
    <ColorPicker
      value={local}
      swatchClassName="team-swatch"
      title={`Change ${teamName} color`}
      onChange={(hex) => {
        setLocal(hex);
        if (timerRef.current !== null) window.clearTimeout(timerRef.current);
        timerRef.current = window.setTimeout(() => {
          timerRef.current = null;
          onChange(hex);
        }, 300);
      }}
    />
  );
}
