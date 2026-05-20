import { useRef, useState } from "react";
import type { ClipDto } from "../types/api";
import { Modal } from "./Modal";

interface Props {
  clips: ClipDto[];
  /** Upload one file via the chunked-upload protocol. `signal` is hooked
   *  to the queue item's AbortController so the row's ✕ Cancel button
   *  aborts the in-flight chunk and tells the server to clean up. */
  onUpload: (
    file: File,
    onProgress: (pct: number) => void,
    signal: AbortSignal
  ) => Promise<void>;
  /** Server-side path import. Returns the basenames the backend
   *  imported, so we can show a quick "imported N file(s)" confirmation. */
  onImportPath: (
    sourcePath: string,
    mode: "copy" | "move"
  ) => Promise<string[]>;
  onReorder: (ids: string[]) => Promise<void>;
  onDelete: (clipId: string) => Promise<void>;
}

/** Queue row state machine:
 *    pending -> uploading -> done
 *                         \-> failed   (manual retry returns it to pending)
 *                         \-> cancelled (user clicked ✕)
 *
 *  Each row owns its own AbortController so cancelling one row doesn't
 *  affect the others. We hold the controller in a sibling array keyed
 *  by row index rather than putting it in state - AbortController isn't
 *  serializable and stuffing it into setState confuses React DevTools.
 */
interface QueueItem {
  file: File;
  status: "pending" | "uploading" | "done" | "failed" | "cancelled";
  percent: number;
  error?: string;
}

const VIDEO_RX = /\.(mp4|mov|mkv|avi|m4v|webm)$/i;

export function ClipManager({
  clips,
  onUpload,
  onImportPath,
  onReorder,
  onDelete,
}: Props) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const queueRef = useRef<QueueItem[]>([]);
  const controllersRef = useRef<(AbortController | null)[]>([]);
  const runningRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // Path-import modal state.
  const [importOpen, setImportOpen] = useState(false);
  const [importPath, setImportPath] = useState("");
  const [importMode, setImportMode] = useState<"copy" | "move">("copy");
  const [importBusy, setImportBusy] = useState(false);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  function syncQueue(next: QueueItem[]) {
    queueRef.current = next;
    setQueue(next);
  }

  function pick() {
    fileRef.current?.click();
  }

  function enqueue(files: File[]) {
    const accepted = files.filter((f) => VIDEO_RX.test(f.name));
    if (accepted.length === 0) return;
    const items: QueueItem[] = accepted.map((file) => ({
      file,
      status: "pending",
      percent: 0,
    }));
    // Allocate controller slots in lock-step with the queue so indices
    // line up; we add nulls now and replace with real controllers when
    // the row actually starts uploading.
    controllersRef.current = [
      ...controllersRef.current,
      ...items.map(() => null),
    ];
    syncQueue([...queueRef.current, ...items]);
    if (!runningRef.current) void runQueue();
  }

  function patchItem(idx: number, patch: Partial<QueueItem>) {
    const next = queueRef.current.slice();
    if (next[idx]) next[idx] = { ...next[idx], ...patch };
    syncQueue(next);
  }

  async function runQueue() {
    runningRef.current = true;
    setBusy(true);
    try {
      while (true) {
        const idx = queueRef.current.findIndex((q) => q.status === "pending");
        if (idx < 0) break;
        const item = queueRef.current[idx];
        const ctrl = new AbortController();
        controllersRef.current[idx] = ctrl;
        patchItem(idx, { status: "uploading", percent: 0, error: undefined });
        try {
          await onUpload(
            item.file,
            (pct) => patchItem(idx, { percent: pct }),
            ctrl.signal
          );
          patchItem(idx, { status: "done", percent: 100 });
        } catch (e) {
          // Distinguish "user aborted" from "server / network failure"
          // so the row label is honest. Cancelled rows shouldn't show
          // "failed" in red - the user knows they cancelled.
          if (ctrl.signal.aborted) {
            patchItem(idx, { status: "cancelled" });
          } else {
            patchItem(idx, { status: "failed", error: String(e) });
          }
        } finally {
          controllersRef.current[idx] = null;
        }
      }
    } finally {
      runningRef.current = false;
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  function cancelItem(idx: number) {
    const ctrl = controllersRef.current[idx];
    if (ctrl) ctrl.abort();
  }

  function retryItem(idx: number) {
    // Flip the row back to pending; the queue loop will pick it up the
    // next time it spins. If the loop is idle we kick it ourselves.
    patchItem(idx, { status: "pending", percent: 0, error: undefined });
    if (!runningRef.current) void runQueue();
  }

  function clearFinished() {
    // Drop done / failed / cancelled rows and their (now-null) controllers.
    // Reindex controllersRef in lockstep so live uploads still match
    // the right row.
    const kept: QueueItem[] = [];
    const keptControllers: (AbortController | null)[] = [];
    for (let i = 0; i < queueRef.current.length; i++) {
      const q = queueRef.current[i];
      if (q.status === "uploading" || q.status === "pending") {
        kept.push(q);
        keptControllers.push(controllersRef.current[i]);
      }
    }
    controllersRef.current = keptControllers;
    syncQueue(kept);
  }

  function move(idx: number, dir: -1 | 1) {
    const next = [...clips];
    const j = idx + dir;
    if (j < 0 || j >= next.length) return;
    [next[idx], next[j]] = [next[j], next[idx]];
    void onReorder(next.map((c) => c.id));
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length) void enqueue(files);
  }

  async function submitImport() {
    const trimmed = importPath.trim();
    if (!trimmed) return;
    setImportBusy(true);
    setImportError(null);
    setImportMessage(null);
    try {
      const imported = await onImportPath(trimmed, importMode);
      setImportMessage(
        `Imported ${imported.length} file${imported.length === 1 ? "" : "s"}: ` +
          imported.join(", ")
      );
      setImportPath("");
    } catch (e) {
      setImportError(String(e));
    } finally {
      setImportBusy(false);
    }
  }

  const pendingCount = queue.filter(
    (q) => q.status === "pending" || q.status === "uploading"
  ).length;
  const finishedCount = queue.filter(
    (q) =>
      q.status === "done" || q.status === "failed" || q.status === "cancelled"
  ).length;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Clips</h2>
        <div className="toolbar">
          <button onClick={() => setImportOpen(true)} disabled={busy}>
            Import from path…
          </button>
          <button className="primary" onClick={pick} disabled={busy}>
            Upload videos
          </button>
        </div>
      </div>
      <input
        ref={fileRef}
        type="file"
        accept="video/*"
        multiple
        style={{ display: "none" }}
        onChange={(e) => {
          const fs = Array.from(e.target.files || []);
          if (fs.length) void enqueue(fs);
        }}
      />

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        style={{
          marginBottom: 12,
          padding: 14,
          border: `1px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 8,
          textAlign: "center",
          color: "var(--text-dim)",
          background: dragOver ? "rgba(80,160,255,0.08)" : "transparent",
          transition: "background 0.1s, border-color 0.1s",
        }}
      >
        {dragOver
          ? "Drop video files to add them as clips"
          : 'Drop video files here, or click "Upload videos" to pick multiple at once.'}
      </div>

      {queue.length > 0 && (
        <div className="card" style={{ marginBottom: 12, padding: 8 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 6,
            }}
          >
            <strong style={{ fontSize: 13 }}>
              Upload queue: {pendingCount} pending, {finishedCount} done
            </strong>
            {finishedCount > 0 && (
              <button
                onClick={clearFinished}
                disabled={busy && pendingCount === 0}
              >
                Clear done
              </button>
            )}
          </div>
          {queue.map((q, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "4px 0",
                fontSize: 12,
              }}
            >
              <span
                style={{
                  flex: 1,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {q.file.name}
              </span>
              <span
                style={{
                  width: 90,
                  textAlign: "right",
                  color: "var(--text-dim)",
                }}
              >
                {q.status === "uploading" && `${q.percent.toFixed(0)}%`}
                {q.status === "pending" && "queued"}
                {q.status === "done" && "✓"}
                {q.status === "cancelled" && (
                  <span style={{ color: "var(--text-dim)" }}>cancelled</span>
                )}
                {q.status === "failed" && (
                  <span
                    title={q.error || ""}
                    style={{ color: "var(--danger, #d44)" }}
                  >
                    failed
                  </span>
                )}
              </span>
              {/* Per-row controls. Cancel only while in flight; Retry
                  for any non-terminal-success state so the user can
                  recover from network blips without re-picking the
                  file from disk. */}
              {q.status === "uploading" && (
                <button
                  onClick={() => cancelItem(i)}
                  title="Cancel this upload"
                  style={{ padding: "2px 8px" }}
                >
                  ✕
                </button>
              )}
              {(q.status === "failed" || q.status === "cancelled") && (
                <button
                  onClick={() => retryItem(i)}
                  title="Retry this upload"
                  style={{ padding: "2px 8px" }}
                >
                  ↻
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {clips.length === 0 && pendingCount === 0 && (
        <p className="muted">
          No clips yet. Drop one above, click <strong>Upload videos</strong>,
          or use <strong>Import from path…</strong> if the source is already
          on the server.
        </p>
      )}

      <div className="list">
        {clips.map((c, i) => (
          <div key={c.id} className="list-row">
            <div>
              <div>
                {i + 1}. {c.filename}
              </div>
              <div className="row-meta">
                {c.frame_count} frames @ {c.fps.toFixed(2)} fps - {c.width}×
                {c.height}
              </div>
            </div>
            <div className="toolbar">
              <button onClick={() => move(i, -1)} disabled={i === 0 || busy}>
                ↑
              </button>
              <button
                onClick={() => move(i, 1)}
                disabled={i === clips.length - 1 || busy}
              >
                ↓
              </button>
              <button
                className="danger"
                onClick={() => void onDelete(c.id)}
                disabled={busy}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* ----- Import-from-path modal ------------------------------------ */}
      <Modal
        open={importOpen}
        onClose={() => {
          if (importBusy) return; // don't allow close mid-import
          setImportOpen(false);
          setImportError(null);
          setImportMessage(null);
        }}
        title="Import from server-side path"
        width="min(560px, 95vw)"
      >
        <p className="muted" style={{ marginTop: 0 }}>
          Tell the server to ingest video files that already live on its
          local filesystem. Skips HTTP transfer entirely — perfect when
          the browser and server are on the same machine. Accepts a
          single video file path or a directory of them.
        </p>
        <label style={{ display: "block", marginBottom: 8 }}>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 4 }}>
            Path (absolute)
          </div>
          <input
            type="text"
            value={importPath}
            onChange={(e) => setImportPath(e.target.value)}
            placeholder={"C:\\temp\\2026.05.09\\Match 1\\"}
            disabled={importBusy}
            style={{ width: "100%", fontFamily: "monospace" }}
          />
        </label>
        <div style={{ display: "flex", gap: 16, marginBottom: 8, fontSize: 13 }}>
          <label>
            <input
              type="radio"
              checked={importMode === "copy"}
              onChange={() => setImportMode("copy")}
              disabled={importBusy}
            />{" "}
            Copy (keep source)
          </label>
          <label>
            <input
              type="radio"
              checked={importMode === "move"}
              onChange={() => setImportMode("move")}
              disabled={importBusy}
            />{" "}
            Move (rename if same drive)
          </label>
        </div>
        {importError && (
          <div
            style={{
              color: "var(--danger, #d44)",
              fontSize: 12,
              marginBottom: 8,
              whiteSpace: "pre-wrap",
            }}
          >
            {importError}
          </div>
        )}
        {importMessage && (
          <div style={{ color: "var(--ok, #5a5)", fontSize: 12, marginBottom: 8 }}>
            {importMessage}
          </div>
        )}
        <div className="toolbar" style={{ justifyContent: "flex-end" }}>
          <button
            onClick={() => {
              setImportOpen(false);
              setImportError(null);
              setImportMessage(null);
            }}
            disabled={importBusy}
          >
            Close
          </button>
          <button
            className="primary"
            onClick={submitImport}
            disabled={importBusy || !importPath.trim()}
          >
            {importBusy ? "Importing…" : "Import"}
          </button>
        </div>
      </Modal>
    </div>
  );
}
