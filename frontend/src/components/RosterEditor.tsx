import { useState } from "react";
import type { PlayerDto, RosterDto } from "../types/api";
import { ColorPicker } from "./ColorPicker";

interface Props {
  value: RosterDto;
  onSave: (next: RosterDto) => Promise<void>;
  defaultName?: string;
}

export function RosterEditor({ value, onSave, defaultName }: Props) {
  const [draft, setDraft] = useState<RosterDto>({
    team_name: value.team_name ?? defaultName ?? null,
    team_color: value.team_color ?? "#2d8a4e",
    players: value.players,
  });
  const [saving, setSaving] = useState(false);

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
          <ColorPicker
            value={draft.team_color ?? "#2d8a4e"}
            onChange={(hex) => update("team_color", hex)}
          />
        </label>
      </div>
      <div style={{ marginTop: 16 }}>
        {draft.players.length === 0 && <p className="muted">No players yet.</p>}
        <div className="list">
          {draft.players.map((p, i) => (
            <div key={i} className="list-row">
              <div className="field-row" style={{ flex: 1 }}>
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
