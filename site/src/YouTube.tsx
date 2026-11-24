import { useState } from "react";
import type { Video } from "../shared/docs";

export const videoKey = (v: Video) => v.youtube_id || v.onedrive_url || v.onedrive_embed || "";

/**
 * A video that loads only when asked: a season page holds dozens, and a
 * dozen live players would stall a phone. YouTube plays from the
 * privacy-enhanced domain, which sets no cookies until play; OneDrive
 * reels play in OneDrive's embedded player, with a link to OneDrive's
 * own page underneath for when the frame won't play (or to download).
 */
export function VideoTile({ video, title }: { video: Video; title: string }) {
  const [playing, setPlaying] = useState(false);
  if (video.youtube_id) return <YouTube id={video.youtube_id} title={title} />;
  const embed = video.onedrive_embed;
  return (
    <div className="video-cell">
      {playing && embed ? (
        <div className="yt">
          <iframe src={embed} title={title} allow="autoplay; fullscreen" allowFullScreen />
        </div>
      ) : (
        <button
          className="yt yt-poster od-poster"
          onClick={() => (embed ? setPlaying(true) : window.open(video.onedrive_url, "_blank", "noopener"))}
          aria-label={`Play ${title}`}
        >
          <span className="yt-play">▶</span>
          <span className="yt-title">{title}</span>
        </button>
      )}
      {video.onedrive_url && (
        <a className="od-link small" href={video.onedrive_url} target="_blank" rel="noreferrer">
          Open in OneDrive ↗
        </a>
      )}
    </div>
  );
}

export function YouTube({ id, title }: { id: string; title: string }) {
  const [playing, setPlaying] = useState(false);
  if (playing) {
    return (
      <div className="yt">
        <iframe
          src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0`}
          title={title}
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          allowFullScreen
        />
      </div>
    );
  }
  return (
    <button className="yt yt-poster" onClick={() => setPlaying(true)} aria-label={`Play ${title}`}>
      <img src={`https://i.ytimg.com/vi/${encodeURIComponent(id)}/hqdefault.jpg`} alt="" loading="lazy" />
      <span className="yt-play">▶</span>
      <span className="yt-title">{title}</span>
    </button>
  );
}
