import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ClubDto } from "../types/api";
import { ColorPicker } from "../components/ColorPicker";
import { LogoPicker } from "../components/LogoPicker";
import { ClubLookup } from "../components/ClubLookup";

/**
 * The opponents' clubs and schools, set up before anyone knows who they
 * will play: a name, the colours their teams wear (the first is the
 * main one) and the badge. Picking a club on a new match - here, on the
 * site or on the phone - fills in the opponent's name, colour and
 * badge; the name can then be made particular ("NW Jrs. Grey").
 *
 * Adding a club looks its badge and colours up online straight away
 * (ClubLookup), narrowed by the hints given - "Seattle", "Washington
 * State", the club's website; "Find online" does it again later.
 *
 * Every team's matches pick from the same list. A match keeps its own
 * copy, so changing or deleting a club leaves matches already made as
 * they are.
 */
export function ClubsPage() {
  const [clubs, setClubs] = useState<ClubDto[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [lookingUp, setLookingUp] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setClubs(await api.listClubs());
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function create() {
    if (!newName.trim()) return;
    setError(null);
    try {
      // Just the name: the badge and colours come with the online search
      // that opens next. Its hints start as "volleyball" (unless the
      // name says so already), which steers it off same-named clubs in
      // other sports; it stays in the hint box to edit like the rest.
      const name = newName.trim();
      const hint = /volleyball/i.test(name) ? "" : "volleyball";
      const club = await api.createClub({ name, colors: [], hint });
      setNewName("");
      await load();
      setLookingUp(club.id);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="page page-narrow">
      <div className="page-head">
        <h1>Clubs</h1>
        <Link to="/">← All teams</Link>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        The clubs and schools your teams play. Pick one on a new match to fill in the opponent's name,
        colour and badge; the name and colour can still be changed for that match ("NW Jrs." → "NW Jrs. Grey").
      </p>
      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="card-header"><h2>New club</h2></div>
        <div className="toolbar">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void create()}
            placeholder="Club or school name"
            style={{ flex: 1, minWidth: 200 }}
          />
          <button className="primary" onClick={() => void create()} disabled={!newName.trim()}>
            Add club
          </button>
        </div>
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
          Once added, its badge and colours are looked up online to choose from; they can also be set by hand.
        </p>
      </div>

      <div className="card">
        <div className="card-header"><h2>{clubs.length} club{clubs.length === 1 ? "" : "s"}</h2></div>
        {clubs.length === 0 && <p className="muted">None yet.</p>}
        <div className="list">
          {clubs.map((c) => (
            <ClubRow
              key={c.id}
              club={c}
              onChanged={load}
              onError={setError}
              lookingUp={lookingUp === c.id}
              onLookUp={(open) => setLookingUp(open ? c.id : null)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ClubRow({
  club,
  onChanged,
  onError,
  lookingUp,
  onLookUp,
}: {
  club: ClubDto;
  onChanged: () => Promise<void>;
  onError: (e: string | null) => void;
  lookingUp: boolean;
  onLookUp: (open: boolean) => void;
}) {
  const [name, setName] = useState(club.name);
  const [version, setVersion] = useState(() => Date.now());
  useEffect(() => setName(club.name), [club.name]);

  async function save(body: { name?: string; colors?: string[] }) {
    onError(null);
    try {
      await api.updateClub(club.id, body);
      await onChanged();
    } catch (e) {
      onError(String(e));
      setName(club.name);
    }
  }

  return (
    <div className="list-row" style={{ flexWrap: "wrap" }}>
      <LogoPicker
        url={club.has_logo ? api.clubLogoUrl(club.id, version) : null}
        label={club.name}
        size={36}
        onUpload={async (file) => {
          await api.uploadClubLogo(club.id, file);
          setVersion(Date.now());
          await onChanged();
        }}
        onRemove={async () => {
          await api.deleteClubLogo(club.id);
          setVersion(Date.now());
          await onChanged();
        }}
        onColors={(colors) => save({ colors })}
      />
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => name.trim() && name.trim() !== club.name && void save({ name: name.trim() })}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        style={{ flex: "1 1 180px" }}
        aria-label="Club name"
      />
      {/* With the search open its form holds the colours (and has its
          own Close), so the row doesn't show them a second time. */}
      {!lookingUp && <ClubColors colors={club.colors} onSave={(colors) => save({ colors })} />}
      {!lookingUp && (
        <button onClick={() => onLookUp(true)} title="Look the badge and colours up online">
          Find online
        </button>
      )}
      <button
        className="danger"
        onClick={() => {
          if (window.confirm(`Delete ${club.name}?\n\nMatches already made keep its name, colour and badge.`)) {
            void api.deleteClub(club.id).then(onChanged, (e) => onError(String(e)));
          }
        }}
      >
        Delete
      </button>
      {lookingUp && (
        <ClubLookup
          club={club}
          current={<ClubColors colors={club.colors} onSave={(colors) => save({ colors })} />}
          onClose={() => onLookUp(false)}
          onSaved={async () => {
            setVersion(Date.now());
            await onChanged();
          }}
        />
      )}
    </div>
  );
}

/** A club's colours, editable: the first is the main one. */
function ClubColors({ colors, onSave }: { colors: string[]; onSave: (colors: string[]) => void }) {
  return (
    <span style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }} title="Colours their teams wear; the first is the main one">
      {colors.map((c, i) => (
        <span key={`${i}-${c}`} style={{ display: "inline-flex", alignItems: "center" }}>
          <ColorPicker
            value={c}
            onChange={(hex) => onSave(colors.map((x, j) => (j === i ? hex : x)))}
            align="right"
            title={i === 0 ? "Main colour" : "Colour"}
          />
          <button
            title="Remove this colour"
            onClick={() => onSave(colors.filter((_, j) => j !== i))}
            style={{ padding: "0 4px", background: "none", border: "none", color: "var(--text-dim)", cursor: "pointer" }}
          >
            ×
          </button>
        </span>
      ))}
      {colors.length < 6 && (
        <button onClick={() => onSave([...colors, colors.length ? "#ffffff" : "#2d8a4e"])} title="Add a colour">
          + colour
        </button>
      )}
    </span>
  );
}
