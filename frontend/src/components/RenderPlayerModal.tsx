import { useEffect, useRef, useState } from "react";
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
 *
 * A <video> that can't play says nothing - it just spins - so this also
 * watches for the two ways that happens with our output and explains
 * them: the browser can't decode HEVC, or the connection can't keep up
 * with a ~10 Mbps master. Both are invisible otherwise, and both have
 * different answers (install the codec / open on the LAN / download).
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

/** Seconds of no buffering progress before we call it stuck. Long
 *  enough to ride out a moov fetch on a pre-faststart render (7 MB from
 *  the tail of the file) without crying wolf. */
const STALL_AFTER_SECONDS = 12;

/** Can this browser decode what our renders contain? Renders are HEVC
 *  (hevc_nvenc), which Chrome/Edge only decode with the OS HEVC
 *  extension installed and Firefox often not at all. */
function hevcSupport(): boolean {
  const v = document.createElement("video");
  return (
    v.canPlayType('video/mp4; codecs="hvc1.1.6.L93.B0"') !== "" ||
    v.canPlayType('video/mp4; codecs="hev1.1.6.L93.B0"') !== ""
  );
}

function errorText(err: MediaError | null, hevcOk: boolean): string {
  switch (err?.code) {
    case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED:
      return hevcOk
        ? "The browser refused this file. If it is still rendering, wait " +
            "for the job to finish - an unfinished mp4 has no index yet."
        : "This browser cannot decode H.265/HEVC, which is what renders " +
            "use. Install 'HEVC Video Extensions' from the Microsoft " +
            "Store (Chrome/Edge), or download the file and play it in " +
            "VLC / MPC.";
    case MediaError.MEDIA_ERR_DECODE:
      return "Decoding failed partway through - the file may be truncated " +
        "(interrupted render) or the hardware decoder gave up.";
    case MediaError.MEDIA_ERR_NETWORK:
      return "The connection dropped while streaming. Renders are ~10 Mbps, " +
        "so a slow or flaky link will not sustain playback; download " +
        "instead.";
    case MediaError.MEDIA_ERR_ABORTED:
      return "Playback was aborted.";
    default:
      return err?.message || "Playback failed for an unknown reason.";
  }
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
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  /** Media-seconds buffered per wall-second, smoothed. Below 1 means
   *  the link is slower than the file's bitrate, so playback can only
   *  ever stutter - worth saying out loud rather than letting the user
   *  blame the app. */
  const [rate, setRate] = useState<number | null>(null);

  // One watchdog for the lifetime of a given src. Everything it needs
  // (buffered end, readyState, error) lives on the element, so polling
  // once a second is both simpler and more reliable than stitching
  // together `waiting`/`stalled`/`progress`, which fire inconsistently
  // across browsers.
  useEffect(() => {
    if (!open) return;
    setProblem(null);
    setRate(null);
    const hevcOk = hevcSupport();
    let lastBuffered = 0;
    let lastGrowth = Date.now();
    let smoothed: number | null = null;
    let prevBuffered: number | null = null;
    let prevAt = Date.now();

    const tick = () => {
      const v = videoRef.current;
      if (!v) return;
      if (v.error) {
        setProblem(errorText(v.error, hevcOk));
        return;
      }
      const end = v.buffered.length ? v.buffered.end(v.buffered.length - 1) : 0;
      const now = Date.now();
      if (prevBuffered !== null && now > prevAt) {
        const mediaGain = end - prevBuffered;
        const wallGain = (now - prevAt) / 1000;
        if (mediaGain > 0) {
          const inst = mediaGain / wallGain;
          smoothed = smoothed === null ? inst : smoothed * 0.6 + inst * 0.4;
          setRate(smoothed);
        }
      }
      prevBuffered = end;
      prevAt = now;

      if (end > lastBuffered + 0.05) {
        lastBuffered = end;
        lastGrowth = now;
        // Buffering again: whatever we complained about has cleared,
        // unless it was a hard error (handled above and left in place).
        setProblem(null);
        return;
      }
      // Nothing new buffered. That is only a problem while the player
      // actually wants data: paused-and-fully-buffered is fine, and so
      // is having reached the end of the file.
      const wantsData = !v.paused && !v.ended && v.readyState < 3;
      if (wantsData && (now - lastGrowth) / 1000 > STALL_AFTER_SECONDS) {
        setProblem(
          !hevcOk
            ? "Nothing is loading, and this browser reports no H.265/HEVC " +
                "support - which is what renders use. Install 'HEVC Video " +
                "Extensions' (Microsoft Store) or download and play in VLC."
            : "Stalled waiting for data. Renders are ~10 Mbps, so a remote " +
                "connection often cannot sustain a full match - try it on " +
                "the LAN, or download the file."
        );
      }
    };

    const t = window.setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [open, src]);

  const tooSlow = rate !== null && rate < 0.9;

  return (
    <Modal open={open} onClose={onClose} title={title} width="min(960px, 94vw)">
      {/* `key` on the source forces a reload when the user opens a
          different render without closing the dialog first - otherwise
          the element keeps playing the previous file. */}
      <video
        ref={videoRef}
        key={src}
        src={src}
        controls
        autoPlay
        onError={() =>
          setProblem(errorText(videoRef.current?.error ?? null, hevcSupport()))
        }
        style={{
          width: "100%",
          maxHeight: "70vh",
          background: "#000",
          borderRadius: 4,
          display: "block",
        }}
      />
      {problem && (
        <div className="error" style={{ marginTop: 8 }}>
          {problem}
        </div>
      )}
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
            {/* Only mention throughput when it is the story: a link that
                keeps up needs no commentary. */}
            {tooSlow && (
              <span
                style={{ color: "var(--warn, #d29922)", marginLeft: 8 }}
                title={
                  "Buffering slower than real time, so playback will keep " +
                  "stalling however long you wait."
                }
              >
                · loading at {rate!.toFixed(2)}× real time
              </span>
            )}
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
