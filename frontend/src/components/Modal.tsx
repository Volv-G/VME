import { useEffect } from "react";

interface Props {
  open: boolean;
  title?: string;
  onClose: () => void;
  children: React.ReactNode;
  /** Width as a CSS value, e.g. "640px", "min(800px, 90vw)". */
  width?: string;
}

/** Simple overlay modal. Click backdrop or press Esc to close. */
export function Modal({ open, title, onClose, children, width = "min(720px, 92vw)" }: Props) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-card"
        style={{ width }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-header">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="modal-close" onClick={onClose} title="Close (Esc)">×</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
