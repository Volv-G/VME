import { useRef, useState } from "react";
import type { ClipDto } from "../types/api";

interface Props {
  clips: ClipDto[];
  onUpload: (file: File, onProgress: (pct: number) => void) => Promise<void>;
  onReorder: (ids: string[]) => Promise<void>;
  onDelete: (clipId: string) => Promise<void>;
  onRescan: () => Promise<void>;
}

interface QueueItem {
  file: File;
  status: "pending" | "uploading" | "done" | "failed";
  percent: number;
  error?: string;
}

const VIDEO_RX = /\.(mp4|mov|mkv|avi|m4v|webm)$/i;

export function ClipManager({ clips, onUpload, onReorder, onDelete, onRescan }: Props) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const queueRef = useRef<QueueItem[]>([]);
  const runningRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

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
        patchItem(idx, { status: "uploading", percent: 0 });
        try {
          await onUpload(item.file, (pct) => patchItem(idx, { percent: pct }));
          patchItem(idx, { status: "done", percent: 100 });
        } catch (e) {
          patchItem(idx, { status: "failed", error: String(e) });
        }
      }
    } finally {
      runningRef.current = false;
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  function clearFinished() {
    syncQueue(queueRef.current.filter((q) => q.status === "uploading" || q.status === "pending"));
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

  const pendingCount = queue.filter((q) => q.status === "pending" || q.status === "uploading").length;
  const finishedCount = queue.filter((q) => q.status === "done" || q.status === "failed").length;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Clips</h2>
        <div className="toolbar">
          <button onClick={onRescan} disabled={busy}>Rescan folder</button>
          <button className="primary" onClick={pick} disabled={busy}>Upload videos</button>
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
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
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
          : "Drop video files here, or click \"Upload videos\" to pick multiple at once."}
      </div>

      {queue.length > 0 && (
        <div className="card" style={{ marginBottom: 12, padding: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <strong style={{ fontSize: 13 }}>
              Upload queue: {pendingCount} pending, {finishedCount} done
            </strong>
            {finishedCount > 0 && (
              <button onClick={clearFinished} disabled={busy && pendingCount === 0}>
                Clear done
              </button>
            )}
          </div>
          {queue.map((q, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: 12 }}>
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {q.file.name}
              </span>
              <span style={{ width: 90, textAlign: "right", color: "var(--text-dim)" }}>
                {q.status === "uploading" && `${q.percent.toFixed(0)}%`}
                {q.status === "pending" && "queued"}
                {q.status === "done" && "✓"}
                {q.status === "failed" && (
                  <span title={q.error || ""} style={{ color: "var(--danger, #d44)" }}>failed</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {clips.length === 0 && pendingCount === 0 && (
        <p className="muted">
          No clips yet. Drop one above, click <strong>Upload videos</strong>, or copy files
          into the match folder and click <strong>Rescan folder</strong>.
        </p>
      )}

      <div className="list">
        {clips.map((c, i) => (
          <div key={c.id} className="list-row">
            <div>
              <div>{i + 1}. {c.filename}</div>
              <div className="row-meta">
                {c.frame_count} frames @ {c.fps.toFixed(2)} fps - {c.width}×{c.height}
              </div>
            </div>
            <div className="toolbar">
              <button onClick={() => move(i, -1)} disabled={i === 0 || busy}>↑</button>
              <button onClick={() => move(i, 1)} disabled={i === clips.length - 1 || busy}>↓</button>
              <button className="danger" onClick={() => void onDelete(c.id)} disabled={busy}>Delete</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
