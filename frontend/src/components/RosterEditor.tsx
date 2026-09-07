import { useState } from "react";
import { api } from "../api/client";
import type { PlayerDto, RosterDto } from "../types/api";
import { ColorPicker } from "./ColorPicker";
import { LogoPicker } from "./LogoPicker";

interface Props {
  value: RosterDto;
  onSave: (next: RosterDto) => Promise<void>;
  defaultName?: string;
  /** Team slug - needed for the logo endpoints, which are separate from
   *  the roster PUT (the file is uploaded immediately, not on Save). */
  team: string;
}

export function RosterEditor({ value, onSave, defaultName, team }: Props) {
  const [draft, setDraft] = useState<RosterDto>({
    team_name: value.team_name ?? defaultName ?? null,
    team_color: value.team_color ?? "#2d8a4e",
    players: value.players,
  });
  const [saving, setSaving] = useState(false);
  // Logo uploads are immediate (they're files, not form state), so the
  // preview is refreshed by bumping a cache-busting version rather than
  // by re-fetching the roster.
  const [logoName, setLogoName] = useState<string | null>(
    value.team_logo_path ?? null
  );
  const [logoVersion, setLogoVersion] = useState<number>(() => Date.now());
  // Same immediate-upload treatment for player photos, keyed by jersey
  // number (which is how the backend stores them).
  const [photoVersion, setPhotoVersion] = useState<Record<number, number>>({});

  function update<K extends keyof RosterDto>(key: K, val: RosterDto[K]) {
    setDraft({ ...draft, [key]: val });
  }

  function updatePlayer(idx: number, p: PlayerDto) {
    const next = [...draft.players];
    next[idx] = p;
    update("players", next);
  }

  function addPlayer() {
    update("players", [...draft.players, { name: "", number: 0 }]);
  }

  function removePlayer(idx: number) {
    update("players", draft.players.filter((_, i) => i !== idx));
  }

  async function save() {
    setSaving(true);
    try { await onSave(draft); } finally { setSaving(false); }
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2>Roster</h2>
        <button className="primary" onClick={save} disabled={saving}>
          {saving ? "Saving..." : "Save roster"}
        </button>
      </div>
      <div className="field-row">
        <label>
          Team display name
          <input value={draft.team_name ?? ""} onChange={(e) => update("team_name", e.target.value)} />
        </label>
        <label>
          Team color
          <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <ColorPicker
              value={draft.team_color ?? "#2d8a4e"}
              onChange={(hex) => update("team_color", hex)}
            />
            <LogoPicker
              url={
                logoName ? api.teamLogoUrl(team, logoVersion) : null
              }
              label={draft.team_name || defaultName || team}
              onUpload={async (file) => {
                const r = await api.uploadTeamLogo(team, file);
                setLogoName(r.team_logo_path);
                setLogoVersion(Date.now());
              }}
              onRemove={async () => {
                await api.deleteTeamLogo(team);
                setLogoName(null);
                setLogoVersion(Date.now());
              }}
            />
          </span>
        </label>
      </div>
      <div style={{ marginTop: 16 }}>
        {draft.players.length === 0 && <p className="muted">No players yet.</p>}
        <div className="list">
          {draft.players.map((p, i) => (
            <div key={i} className="list-row">
              {/* Photo is stored per jersey number, so it can only be
                  uploaded for a player who already has one saved -
                  otherwise the file would land under #0 and be orphaned
                  the moment the number is filled in. */}
              <LogoPicker
                size={40}
                label={p.name || `#${p.number}`}
                disabled={!p.number || p.number !== value.players[i]?.number}
                url={
                  p.profile_pic_path
                    ? api.playerPhotoUrl(team, p.number, photoVersion[p.number])
                    : null
                }
                onUpload={async (file) => {
                  const r = await api.uploadPlayerPhoto(team, p.number, file);
                  updatePlayer(i, { ...p, profile_pic_path: r.profile_pic_path });
                  setPhotoVersion((v) => ({ ...v, [p.number]: Date.now() }));
                }}
                onRemove={async () => {
                  await api.deletePlayerPhoto(team, p.number);
                  updatePlayer(i, { ...p, profile_pic_path: null });
                  setPhotoVersion((v) => ({ ...v, [p.number]: Date.now() }));
                }}
              />
              <div className="field-row" style={{ flex: 1, marginLeft: 8 }}>
                <label style={{ flex: "0 0 80px" }}>
                  Number
                  <input
                    type="number"
                    value={p.number}
                    onChange={(e) => updatePlayer(i, { ...p, number: parseInt(e.target.value || "0", 10) })}
                  />
                </label>
                <label style={{ flex: 2 }}>
                  Name
                  <input value={p.name} onChange={(e) => updatePlayer(i, { ...p, name: e.target.value })} />
                </label>
                <label style={{ flex: 1 }}>
                  Short name
                  <input value={p.short_name ?? ""} onChange={(e) => updatePlayer(i, { ...p, short_name: e.target.value })} />
                </label>
              </div>
              <button className="danger" onClick={() => removePlayer(i)} style={{ marginLeft: 8 }}>Remove</button>
            </div>
          ))}
        </div>
        <button onClick={addPlayer} style={{ marginTop: 8 }}>+ Add player</button>
      </div>
    </div>
  );
}
