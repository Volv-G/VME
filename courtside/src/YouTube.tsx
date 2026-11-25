import { useState } from "react";
import type { Video } from "../shared/docs";
import { imageUrl } from "./api";

export const videoKey = (v: Video) => v.youtube_id || v.web || v.onedrive_url || v.onedrive_embed || "";

/**
 * A video tile. YouTube plays in place, loading only when asked (a season
 * page holds dozens, and a dozen live players would stall a phone) from
 * the privacy-enhanced domain, which sets no cookies until play.
 *
 * Reels play in place from their web copy on the site (server/videos.ts),
 * with the OneDrive original a click away. A reel without a copy yet opens
 * on OneDrive: its embedded player shows a black box on a third-party page
 * and it no longer serves files to anonymous players. `poster` is an image
 * key - an opponent's crest, a player's photo - to make up for having no
 * frame of the video to show.
 */
export function VideoTile({ video, title, poster }: { video: Video; title: string; poster?: string | null }) {
  if (video.youtube_id) return <YouTube id={video.youtube_id} title={title} />;
  if (video.web) return <WebVideo src={video.web} original={video.onedrive_url} title={title} poster={poster} />;
  const href = video.onedrive_url || video.onedrive_embed;
  if (!href) return null;
  return (
    <a className="yt yt-poster od-poster" href={href} target="_blank" rel="noreferrer" aria-label={`Watch ${title} on OneDrive`}>
      {poster && <img className="od-crest" src={imageUrl(poster)} alt="" loading="lazy" />}
      <span className="yt-play">▶</span>
      <span className="yt-title">
        {title}
        <span className="od-badge">OneDrive ↗</span>
      </span>
    </a>
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

function WebVideo({ src, original, title, poster }: { src: string; original?: string; title: string; poster?: string | null }) {
  const [playing, setPlaying] = useState(false);
  const url = `/api/video/${src.split("/").map(encodeURIComponent).join("/")}`;
  if (playing) {
    return (
      <div className="yt">
        <video src={url} title={title} controls autoPlay playsInline preload="auto" />
      </div>
    );
  }
  return (
    <div className="yt yt-poster od-poster web-poster">
      <button className="web-play" onClick={() => setPlaying(true)} aria-label={`Play ${title}`}>
        {poster && <img className="od-crest" src={imageUrl(poster)} alt="" loading="lazy" />}
        <span className="yt-play">▶</span>
      </button>
      <span className="yt-title">
        {title}
        {original && (
          <a className="od-badge" href={original} target="_blank" rel="noreferrer" title="The full-quality original on OneDrive">
            Original ↗
          </a>
        )}
      </span>
    </div>
  );
}
