import { useState } from "react";

interface ScoreFixProps {
  onSubmit: (homeDelta: number, awayDelta: number) => void;
  onClose: () => void;
}

export function ScoreFixDialog({ onSubmit, onClose }: ScoreFixProps) {
  const [home, setHome] = useState(0);
  const [away, setAway] = useState(0);
  return (
    <div className="inline-dialog">
      <div className="inline-dialog-header">
        <strong>Score correction</strong>
        <button onClick={onClose}>Cancel</button>
      </div>
      <div className="field-row">
        <label>
          Home Δ
          <input
            type="number"
            value={home}
            onChange={(e) => setHome(parseInt(e.target.value || "0", 10))}
            autoFocus
          />
        </label>
        <label>
          Away Δ
          <input
            type="number"
            value={away}
            onChange={(e) => setAway(parseInt(e.target.value || "0", 10))}
          />
        </label>
      </div>
      <div className="toolbar" style={{ justifyContent: "flex-end", marginTop: 8 }}>
        <button
          className="primary"
          onClick={() => onSubmit(home, away)}
          disabled={home === 0 && away === 0}
        >
          Apply
        </button>
      </div>
    </div>
  );
}

interface MessageProps {
  onSubmit: (text: string) => void;
  onClose: () => void;
}

export function MessageDialog({ onSubmit, onClose }: MessageProps) {
  const [text, setText] = useState("");
  return (
    <div className="inline-dialog">
      <div className="inline-dialog-header">
        <strong>Message</strong>
        <button onClick={onClose}>Cancel</button>
      </div>
      <div className="field-row">
        <label style={{ flex: 1 }}>
          Text
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && text.trim()) onSubmit(text.trim());
            }}
            autoFocus
          />
        </label>
      </div>
      <div className="toolbar" style={{ justifyContent: "flex-end", marginTop: 8 }}>
        <button className="primary" onClick={() => onSubmit(text.trim())} disabled={!text.trim()}>
          Add
        </button>
      </div>
    </div>
  );
}
