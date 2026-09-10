import { Modal } from "./Modal";

/**
 * Watch a rendered file without leaving the app.
 *
 * Clicking a render used to download it, which is the wrong default:
 * the usual reason to click is "did this come out right?", and
 * answering that by waiting for a 4.5 GB download is absurd. The server
 * serves renders with byte-range support, so the browser streams and
 * seeks straight from disk; downloading stays available as its own
 * button.
 */
interface Props {
  open: boolean;
  onClose: () => void;
  /** Stream URL (`?inline=1`) - what the <video> plays. */
  src: string;
  /** Direct URL for the download button. */
  downloadUrl: string;
  /** Shown as the dialog title; usually the leaf filename. */
  title: string;
  /** Optional context line under the player (size, date, ...). */
  subtitle?: string;
  /** When the render is on YouTube, offer that too. */
  youtubeUrl?: string | null;
}

export function RenderPlayerModal({
  open,
  onClose,
  src,
  downloadUrl,
  title,
  subtitle,
  youtubeUrl,
}: Props) {
  return (
    <Modal open={open} onClose={onClose} title={title} width="min(960px, 94vw)">
      {/* `key` on the source forces a reload when the user opens a
          different render without closing the dialog first - otherwise
          the element keeps playing the previous file. */}
      <video
        key={src}
        src={src}
        controls
        autoPlay
        style={{
          width: "100%",
          maxHeight: "70vh",
          background: "#000",
          borderRadius: 4,
          display: "block",
        }}
      />
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginTop: 10,
          flexWrap: "wrap",
        }}
      >
        {subtitle && (
          <span className="row-meta" style={{ flex: 1, minWidth: 0 }}>
            {subtitle}
          </span>
        )}
        {youtubeUrl && (
          <a
            href={youtubeUrl}
            target="_blank"
            rel="noreferrer"
            style={{ color: "#3aa55d", whiteSpace: "nowrap" }}
          >
            ▶ on YouTube
          </a>
        )}
        <a href={downloadUrl} download>
          <button>⬇ Download</button>
        </a>
      </div>
    </Modal>
  );
}
