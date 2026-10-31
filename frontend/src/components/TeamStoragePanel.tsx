import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { MatchStorageDto, StorageCleanupIn, StorageDto } from "../types/api";
import { formatBytes } from "../util/bytes";
import { displayName } from "../util/names";

/**
 * Disk use per match - footage, renders and the two together - with
 * buttons to delete either for one match or for the whole team.
 *
 * Not polled like the renders list: sizes only change when something is
 * rendered or deleted, and walking a season's folders on a timer would
 * be work for nothing. ↻ re-reads, and every cleanup re-reads.
 */
export function TeamStoragePanel({ team }: { team: string }) {
  const [storage, setStorage] = useState<StorageDto | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  // What is being deleted: a match's `rowKey`, or "team".
  const [busy, setBusy] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setStorage(await api.getStorage(team));
    } catch (e) {
      setErr(String(e));
    }
  }, [team]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function run(
    key: string,
    body: StorageCleanupIn,
    match?: MatchStorageDto
  ) {
    setErr(null);
    setNote(null);
    setBusy(key);
    try {
      const res = await api.cleanupStorage(body, team, match);
      const freed = `Freed ${formatBytes(res.freed_bytes)}.`;
      if (res.skipped.length) {
        setNote({
          text: `${freed} Left alone:\n${res.skipped.join("\n")}`,
          ok: false,
        });
      } else {
        setNote({ text: freed, ok: true });
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
      await reload();
    }
  }

  function cleanMatch(m: MatchStorageDto, what: "footage" | "renders") {
    const label = matchLabel(m);
    const text =
      what === "footage"
        ? `Delete the footage of ${label} (${m.footage_files} clip` +
          `${m.footage_files === 1 ? "" : "s"}, ${formatBytes(m.footage_bytes)})?\n\n` +
          "The events, stats and renders stay, but the match can no longer " +
          "be watched in the editor or rendered again." +
          (m.has_full_render
            ? ""
            : "\n\nThis match has NO full render yet - the footage is the only copy of it.")
        : `Delete every render of ${label} (${m.renders_files} file` +
          `${m.renders_files === 1 ? "" : "s"}, ${formatBytes(m.renders_bytes)})?\n\n` +
          "Full match, condensed, reels and highlights, with their thumbnails " +
          "and upload records. Uploaded videos stay up, and as long as the " +
          "footage is here they can be rendered again.";
    if (!confirm(text)) return;
    void run(rowKey(m), { [what]: true }, m);
  }

  function cleanTeam(what: "footage" | "renders") {
    if (!storage) return;
    const rows = storage.matches.filter((m) =>
      what === "footage" ? m.footage_bytes > 0 : m.renders_bytes > 0
    );
    const bytes = rows.reduce(
      (n, m) => n + (what === "footage" ? m.footage_bytes : m.renders_bytes),
      0
    );
    const unrendered = rows.filter((m) => !m.has_full_render).length;
    const busyCount = rows.filter((m) => m.busy).length;
    const text =
      `Delete the ${what} of ALL ${rows.length} match${rows.length === 1 ? "" : "es"} ` +
      `of ${displayName(team)} (${formatBytes(bytes)})?\n\n` +
      (what === "footage"
        ? "Events, stats and renders stay; nothing can be rendered again."
        : "Uploaded videos stay up; the renders can be redone while the footage is here.") +
      (what === "footage" && unrendered
        ? `\n\n${unrendered} of them have NO full render yet - their footage is the only copy.`
        : "") +
      (busyCount
        ? `\n\n${busyCount} with a render or upload under way will be skipped.`
        : "") +
      `\n\nType DELETE to confirm.`;
    // Typed, not OK/Cancel: this is hundreds of gigabytes in one click,
    // and a reflexive Enter on a confirm box should not be enough.
    if ((prompt(text) ?? "").trim().toUpperCase() !== "DELETE") return;
    void run("team", { [what]: true });
  }

  // Latest first, as in the renders list.
  const matches = [...(storage?.matches ?? [])].sort(
    (a, b) =>
      b.date.localeCompare(a.date) ||
      (b.match_index ?? 0) - (a.match_index ?? 0) ||
      a.tournament.localeCompare(b.tournament)
  );
  const total = storage?.total;
  const working = busy !== null;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Storage</h2>
        <div className="toolbar">
          {storage?.disk_free_bytes != null && (
            <span className="row-meta" title="Free space on the disk the media library is on">
              {formatBytes(storage.disk_free_bytes)} free
            </span>
          )}
          <button onClick={() => void reload()} disabled={working} title="Measure again">
            ↻
          </button>
          <button
            onClick={() => cleanTeam("footage")}
            disabled={working || !total?.footage_bytes}
            title="Delete the source clips (and their preview proxies) of every match"
          >
            {busy === "team" ? "…" : "🗑 All footage"}
          </button>
          <button
            onClick={() => cleanTeam("renders")}
            disabled={working || !total?.renders_bytes}
            title="Delete every render of every match"
          >
            {busy === "team" ? "…" : "🗑 All renders"}
          </button>
        </div>
      </div>

      {err && <div className="error" style={{ marginBottom: 8 }}>{err}</div>}
      {note && (
        <div
          className={note.ok ? "info" : "error"}
          style={{ marginBottom: 8, display: "flex", gap: 8, whiteSpace: "pre-line" }}
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

      {!storage ? (
        !err && <p className="muted" style={{ margin: 0 }}>Measuring…</p>
      ) : matches.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>No matches yet.</p>
      ) : (
        <div className="stats-scroll storage-scroll">
          <table className="stats-table">
            <thead>
              <tr>
                <th className="stats-name">Match</th>
                <th>Footage</th>
                <th>Renders</th>
                <th>Total</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {matches.map((m) => {
                const key = rowKey(m);
                const disabled = working || m.busy;
                const why = m.busy ? "A render or upload for this match is queued or running" : "";
                return (
                  <tr key={key}>
                    <td className="stats-name">
                      <Link
                        to={
                          `/teams/${encodeURIComponent(team)}/tournaments/${encodeURIComponent(m.tournament)}` +
                          `/dates/${encodeURIComponent(m.date)}/matches/${encodeURIComponent(m.match)}`
                        }
                      >
                        {matchLabel(m)}
                      </Link>{" "}
                      <span className="muted">· {displayName(m.tournament)}</span>
                      {m.busy && <span className="muted"> · busy</span>}
                    </td>
                    <td
                      title={
                        m.footage_files
                          ? `${m.footage_files} clip${m.footage_files === 1 ? "" : "s"}, with preview proxies`
                          : undefined
                      }
                    >
                      {m.footage_bytes ? formatBytes(m.footage_bytes) : "—"}
                      {m.footage_bytes > 0 && !m.has_full_render && (
                        <span
                          className="storage-warn"
                          title="No full render yet: this footage is the only copy of the match"
                        >
                          {" "}⚠
                        </span>
                      )}
                    </td>
                    <td title={m.renders_files ? `${m.renders_files} files` : undefined}>
                      {m.renders_bytes ? formatBytes(m.renders_bytes) : "—"}
                    </td>
                    <td>{formatBytes(m.footage_bytes + m.renders_bytes + m.other_bytes)}</td>
                    <td className="storage-actions">
                      <button
                        onClick={() => cleanMatch(m, "footage")}
                        disabled={disabled || !m.footage_bytes}
                        title={why || "Delete this match's source clips and their preview proxies"}
                      >
                        {busy === key ? "…" : "🗑 footage"}
                      </button>
                      <button
                        onClick={() => cleanMatch(m, "renders")}
                        disabled={disabled || !m.renders_bytes}
                        title={why || "Delete every render of this match"}
                      >
                        {busy === key ? "…" : "🗑 renders"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
            {total && (
              <tfoot>
                <tr className="stats-total">
                  <td className="stats-name">
                    {matches.length} match{matches.length === 1 ? "" : "es"}
                  </td>
                  <td>{formatBytes(total.footage_bytes)}</td>
                  <td>{formatBytes(total.renders_bytes)}</td>
                  <td>
                    {formatBytes(
                      total.footage_bytes + total.renders_bytes + total.other_bytes
                    )}
                  </td>
                  <td />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </div>
  );
}

function rowKey(m: MatchStorageDto): string {
  return `${m.tournament}/${m.date}/${m.match}`;
}

/** "2026-09-16 · M2 vs Woodinville", the number left out for a day's
 *  only match - as the renders list labels them. */
function matchLabel(m: MatchStorageDto): string {
  const num = m.match_index ? `M${m.match_index} ` : "";
  return `${m.date} · ${num}vs ${displayName(m.opponent || m.match)}`;
}
